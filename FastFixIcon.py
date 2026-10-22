# -*- coding: utf-8 -*-
"""Steam 图标修复（异步版，推荐使用）

并发向多个信息源取 appid 的图标信息，谁先返回用谁，
再从 Steam 官方 CDN 下载 .ico 写入 <Steam>/steam/games。

用法：
    python FastFixIcon.py                 # 自动从注册表找 Steam
    python FastFixIcon.py --jobs 8        # 提高并发
    python FastFixIcon.py --appid 730     # 只处理指定 appid（可重复）
    python FastFixIcon.py --dry-run       # 只报告，不写文件

出错时会把详细日志写到本地，并把路径打印出来，方便定位问题。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import steam_utils as su
from steam_utils import Fore, Style

su.setup_console()

try:  # 缺依赖时给出可读提示并停住窗口，而不是一闪而过
    import aiohttp
except ImportError as _exc:  # pragma: no cover
    sys.exit(su.missing_dependency("aiohttp", "requirements.txt", _exc))

import fastest_request as fr  # noqa: E402  (放在依赖检查之后)

DOWNLOAD_ATTEMPTS = 3


@dataclass
class Stats:
    ok: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.ok + self.skipped + self.failed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="FastFixIcon",
        description="修复 Steam 游戏图标显示空白（异步版）",
    )
    parser.add_argument("--steam-path", help="手动指定 Steam 安装目录（默认读注册表）")
    parser.add_argument("--appid", type=int, action="append", dest="appids",
                        help="只处理指定 appid，可重复传入")
    parser.add_argument("--jobs", type=int, default=4, help="并发数，默认 4")
    parser.add_argument("--timeout", type=float, default=12.0, help="单次请求超时秒数，默认 12")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要做什么，不写文件")
    parser.add_argument("--force", action="store_true", help="即使图标已存在也重新下载")
    parser.add_argument("--proxy", help="走代理，例如 http://127.0.0.1:7890（默认不用代理）")
    parser.add_argument("--no-pause", action="store_true",
                        help="结束后不等待按键（脚本/自动化调用时使用）")
    return parser


def _short(name: str, limit: int = 28) -> str:
    name = name or "未知应用"
    return name if len(name) <= limit else name[: limit - 1] + "…"


async def download_icon(
    session: aiohttp.ClientSession,
    appid: int,
    icon: str,
    attempts: int = DOWNLOAD_ATTEMPTS,
    proxy: Optional[str] = None,
) -> Tuple[Optional[bytes], Optional[str], bool]:
    """按 CDN 顺序下载并校验图标。

    返回 (数据, 最后一次错误, 是否为上游缺图标)。
    第三个字段为 True 表示所有 CDN 都明确 404 —— 说明 Valve 那边就没有这个
    .ico（例如 Dota 2），属于不可修复的条目，调用方按"跳过"处理，不当成失败。
    """
    last_error = "没有可用的下载地址"
    saw_not_found = False
    for attempt in range(attempts):
        all_not_found = True
        for url in su.icon_urls(appid, icon):
            try:
                async with session.get(url, proxy=proxy) as response:
                    status = response.status
                    if status != 200:
                        last_error = "HTTP {0}".format(status)
                        all_not_found = all_not_found and status == 404
                        continue
                    all_not_found = False
                    data = await response.read()
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                last_error = "{0}: {1}".format(type(exc).__name__, exc)
                all_not_found = False
                continue
            if not su.is_valid_icon(data):
                last_error = "响应内容不是有效图标（{0} 字节）".format(len(data))
                all_not_found = False
                continue
            return data, None, False
        saw_not_found = saw_not_found or all_not_found
        if saw_not_found:
            break  # 上游就没有这个文件，重试没有意义
        if attempt + 1 < attempts:
            await asyncio.sleep(1.5 * (attempt + 1))
    return None, last_error, saw_not_found


async def process_one(
    session: aiohttp.ClientSession,
    appid: int,
    icon_dir: Path,
    args: argparse.Namespace,
    stats: Stats,
    progress: List[int],
) -> None:
    progress[0] += 1
    prefix = "[{0}/{1}]".format(progress[0], progress[1])

    info = await fr.get_app_info(appid, session=session, proxy=getattr(args, "proxy", None))
    if info is None:
        stats.failed += 1
        print("{0} {1}[失败]{2} appid={3} 所有信息源都没有返回结果".format(
            prefix, Fore.RED, Style.RESET_ALL, appid))
        return

    if info.get("_missing_token"):
        stats.skipped += 1
        print("{0} {1}[跳过]{2} appid={3} 该应用要求鉴权 token".format(
            prefix, Fore.YELLOW, Style.RESET_ALL, appid))
        return

    common = info.get("common") or {}
    icon = su.icon_name(common)
    name = su.display_name(common)
    if not icon:
        # Spacewar / Steam Linux Runtime / 工具类条目本来就没有 clienticon
        stats.skipped += 1
        print("{0} {1}[跳过]{2} appid={3} {4} 没有 clienticon 字段".format(
            prefix, Fore.YELLOW, Style.RESET_ALL, appid, _short(name)))
        return

    target = icon_dir / "{0}.ico".format(icon)
    if not args.force and target.is_file() and target.stat().st_size > 0:
        stats.skipped += 1
        print("{0} {1}[已有]{2} appid={3} {4}".format(
            prefix, Fore.LIGHTGREEN_EX, Style.RESET_ALL, appid, _short(name)))
        return

    if args.dry_run:
        stats.ok += 1
        print("{0} {1}[试运行]{2} appid={3} {4} -> {5}".format(
            prefix, Fore.CYAN, Style.RESET_ALL, appid, _short(name), target.name))
        return

    data, error, missing_upstream = await download_icon(
        session, appid, icon, proxy=getattr(args, "proxy", None))
    if data is None and missing_upstream:
        stats.skipped += 1
        print("{0} {1}[跳过]{2} appid={3} {4} Steam 官方 CDN 没有该条目的 .ico".format(
            prefix, Fore.YELLOW, Style.RESET_ALL, appid, _short(name)))
        return
    if data is None:
        stats.failed += 1
        print("{0} {1}[失败]{2} appid={3} {4} 下载失败：{5}".format(
            prefix, Fore.RED, Style.RESET_ALL, appid, _short(name), error))
        return

    try:
        su.atomic_write(target, data)
    except OSError as exc:
        stats.failed += 1
        print("{0} {1}[失败]{2} appid={3} {4} 写入失败：{5}".format(
            prefix, Fore.RED, Style.RESET_ALL, appid, _short(name), exc))
        return

    stats.ok += 1
    print("{0} {1}[完成]{2} appid={3} {4}（{5} 字节）".format(
        prefix, Fore.GREEN, Style.RESET_ALL, appid, _short(name), len(data)))


async def run(appids: Sequence[int], icon_dir: Path, args: argparse.Namespace) -> Stats:
    stats = Stats()
    progress = [0, len(appids)]
    semaphore = asyncio.Semaphore(max(1, args.jobs))

    async with fr.create_session(args.timeout) as session:
        async def guarded(appid: int) -> None:
            async with semaphore:
                await process_one(session, appid, icon_dir, args, stats, progress)

        await asyncio.gather(*(guarded(appid) for appid in appids))
    return stats


def main(argv: Optional[Sequence[str]] = None) -> int:
    su.setup_console()
    print(Fore.GREEN + su.BANNER + "\n")

    args = build_parser().parse_args(argv)
    if args.no_pause:
        os.environ[su.NO_PAUSE_ENV] = "1"

    for line in su.proxy_hints():
        print(Fore.YELLOW + line)

    try:
        steam_dir = su.find_steam_dir(args.steam_path)
        print("成功获取到 Steam 路径：" + Fore.GREEN + str(steam_dir))
        vdf_path = su.find_library_folders(steam_dir)
        icon_dir = su.ensure_icon_dir(steam_dir)
    except su.SteamError as exc:
        return su.fatal(str(exc), exc=exc)
    except OSError as exc:
        return su.fatal("定位 Steam 目录失败：{0}".format(exc), exc=exc)

    try:
        appids = list(args.appids) if args.appids else su.collect_app_ids(vdf_path)
    except (OSError, ValueError) as exc:
        return su.fatal("解析 libraryfolders.vdf 失败：{0}".format(exc), exc=exc)

    if not appids:
        print(Fore.YELLOW + "没有在 Steam 库里找到任何 appid")
        return 0

    print("图标目录：" + Fore.GREEN + str(icon_dir))
    print("待处理 {0} 个条目，并发 {1}\n".format(len(appids), max(1, args.jobs)))

    stats = asyncio.run(run(appids, icon_dir, args))

    print("\n{0}完成：新增/修复 {1}，跳过 {2}，失败 {3}，共 {4}{5}".format(
        Fore.CYAN, stats.ok, stats.skipped, stats.failed, stats.total, Style.RESET_ALL))
    if stats.ok:
        print(Fore.YELLOW + "图标已写入，重启 Steam 后生效（必要时刷新图标缓存）。")
    if stats.failed:
        print(Fore.YELLOW + "有 {0} 项失败，可加 --appid <id> 单独重跑看详细原因。".format(stats.failed))

    return 1 if stats.failed else 0


if __name__ == "__main__":
    sys.exit(su.run_guarded(main))
