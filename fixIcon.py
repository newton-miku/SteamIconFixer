# -*- coding: utf-8 -*-
"""Steam 图标修复（同步版，requests 实现）

与 FastFixIcon.py 功能一致，只是不依赖 asyncio/aiohttp，
适合在受限环境里跑，也方便直接阅读/改成自己的脚本。

用法：
    python fixIcon.py
    python fixIcon.py --appid 730 --dry-run

某个入口失败时立刻换下一个，不浪费时间重试；
出错时会写出详细日志并打印路径。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional, Sequence, Tuple

import steam_utils as su
from steam_utils import Fore, Style

su.setup_console()

try:
    import requests
    from requests.adapters import HTTPAdapter
except ImportError as _exc:  # pragma: no cover - 缺依赖时给出可读提示
    sys.exit(su.missing_dependency("requests", "requirements.txt", _exc))

try:
    from urllib3.util.retry import Retry
except ImportError:  # pragma: no cover - 极老版本 requests
    Retry = None  # type: ignore[assignment]

DOWNLOAD_ATTEMPTS = 3
_ACCEPTED_CONTENT_TYPES = ("application/json", "text/json", "text/plain", "application/octet-stream")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fixIcon", description="修复 Steam 游戏图标显示空白（同步版）")
    parser.add_argument("--steam-path", help="手动指定 Steam 安装目录（默认读注册表）")
    parser.add_argument("--appid", type=int, action="append", dest="appids", help="只处理指定 appid，可重复传入")
    parser.add_argument("--timeout", type=float, default=12.0, help="单次请求超时秒数，默认 12")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要做什么，不写文件")
    parser.add_argument("--force", action="store_true", help="即使图标已存在也重新下载")
    parser.add_argument("--proxy", help="走代理，例如 http://127.0.0.1:7890（默认不用代理）")
    parser.add_argument("--no-pause", action="store_true", help="结束后不等待按键")
    return parser


def create_session(proxy: Optional[str] = None) -> requests.Session:
    session = requests.Session()
    # 新版 requests 在发请求时会按环境重建 proxies，只有逐次显式传入才稳定生效，
    # 所以这里把用户指定的代理挂在会话上，由 _get 在每次请求时带上。
    session.icon_proxy = {"http": proxy, "https": proxy} if proxy else None  # type: ignore[attr-defined]
    if Retry is not None:
        retry = Retry(total=2, connect=2, read=2, backoff_factor=0.5,
                      status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
    return session


def _get(session: requests.Session, url: str, timeout: float):
    """统一发 GET：用户指定了代理时逐次显式传入，没指定就走默认行为。"""
    return session.get(url, timeout=timeout, proxies=getattr(session, "icon_proxy", None))


def fetch_app_info(session: requests.Session, appid: int, timeout: float) -> Optional[dict]:
    """依次尝试各入口，第一个成功的就返回。"""
    last_error = ""
    for template in su.INFO_ENDPOINTS:
        url = template.format(appid=appid)
        try:
            response = _get(session, url, timeout)
        except requests.RequestException as exc:
            last_error = "{0}: {1}".format(type(exc).__name__, exc)
            continue
        if response.status_code == 403:
            last_error = "HTTP 403（该入口拒绝了本次请求）"
            continue  # 换入口，不要重试
        if response.status_code != 200:
            last_error = "HTTP {0}".format(response.status_code)
            continue
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type and content_type not in _ACCEPTED_CONTENT_TYPES:
            last_error = "响应不是 JSON（{0}）".format(content_type)
            continue
        try:
            payload = response.json()
        except ValueError:
            last_error = "响应不是合法 JSON"
            continue
        data = payload.get("data") if isinstance(payload, dict) else None
        info = data.get(str(appid)) if isinstance(data, dict) else None
        if isinstance(info, dict):
            return info
        last_error = "响应里没有 appid={0} 的信息".format(appid)
    if last_error:
        print(Fore.RED + "  appid={0} 获取信息失败：{1}".format(appid, last_error))
    return None


def download_icon(
    session: requests.Session, appid: int, icon: str, timeout: float
) -> Tuple[Optional[bytes], str, bool]:
    """返回 (数据, 最后一次错误, 是否为上游缺图标)。

    所有 CDN 都明确 404 时第三个字段为 True：Valve 那边就没有这个 .ico
    （例如 Dota 2），按"跳过"处理而不是失败。
    """
    last_error = "没有可用的下载地址"
    saw_not_found = False
    for attempt in range(DOWNLOAD_ATTEMPTS):
        all_not_found = True
        for url in su.icon_urls(appid, icon):
            try:
                response = _get(session, url, timeout)
            except requests.RequestException as exc:
                last_error = "{0}: {1}".format(type(exc).__name__, exc)
                all_not_found = False
                continue
            if response.status_code != 200:
                last_error = "HTTP {0}".format(response.status_code)
                all_not_found = all_not_found and response.status_code == 404
                continue
            all_not_found = False
            if not su.is_valid_icon(response.content):
                last_error = "响应内容不是有效图标（{0} 字节）".format(len(response.content))
                continue
            return response.content, "", False
        saw_not_found = saw_not_found or all_not_found
        if saw_not_found:
            break
        if attempt + 1 < DOWNLOAD_ATTEMPTS:
            time.sleep(1.5 * (attempt + 1))
    return None, last_error, saw_not_found


def run(appids: Sequence[int], icon_dir, args: argparse.Namespace) -> Tuple[int, int, int]:
    session = create_session(getattr(args, "proxy", None))
    ok = skipped = failed = 0
    total = len(appids)

    for index, appid in enumerate(appids, start=1):
        prefix = "[{0}/{1}]".format(index, total)
        info = fetch_app_info(session, appid, args.timeout)
        if info is None:
            failed += 1
            print("{0} {1}[失败]{2} appid={3} 跳过".format(prefix, Fore.RED, Style.RESET_ALL, appid))
            continue

        if info.get("_missing_token"):
            skipped += 1
            print("{0} {1}[跳过]{2} appid={3} 该应用要求鉴权 token".format(
                prefix, Fore.YELLOW, Style.RESET_ALL, appid))
            continue

        common = info.get("common") or {}
        icon = su.icon_name(common)
        name = su.display_name(common) or "未知应用"
        if not icon:
            skipped += 1
            print("{0} {1}[跳过]{2} appid={3} {4} 没有 clienticon 字段".format(
                prefix, Fore.YELLOW, Style.RESET_ALL, appid, name))
            continue

        target = icon_dir / "{0}.ico".format(icon)
        if not args.force and target.is_file() and target.stat().st_size > 0:
            skipped += 1
            print("{0} {1}[已有]{2} appid={3} {4}".format(
                prefix, Fore.LIGHTGREEN_EX, Style.RESET_ALL, appid, name))
            continue

        if args.dry_run:
            ok += 1
            print("{0} {1}[试运行]{2} appid={3} {4} -> {5}".format(
                prefix, Fore.CYAN, Style.RESET_ALL, appid, name, target.name))
            continue

        data, error, missing_upstream = download_icon(session, appid, icon, args.timeout)
        if data is None and missing_upstream:
            skipped += 1
            print("{0} {1}[跳过]{2} appid={3} {4} Steam 官方 CDN 没有该条目的 .ico".format(
                prefix, Fore.YELLOW, Style.RESET_ALL, appid, name))
            continue
        if data is None:
            failed += 1
            print("{0} {1}[失败]{2} appid={3} {4} 下载失败：{5}".format(
                prefix, Fore.RED, Style.RESET_ALL, appid, name, error))
            continue

        try:
            su.atomic_write(target, data)
        except OSError as exc:
            failed += 1
            print("{0} {1}[失败]{2} appid={3} {4} 写入失败：{5}".format(
                prefix, Fore.RED, Style.RESET_ALL, appid, name, exc))
            continue

        ok += 1
        print("{0} {1}[完成]{2} appid={3} {4}（{5} 字节）".format(
            prefix, Fore.GREEN, Style.RESET_ALL, appid, name, len(data)))

    return ok, skipped, failed


def main(argv: Optional[Sequence[str]] = None) -> int:
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
        return su.fatal(str(exc), exc, hint="可以用 --steam-path 手动指定 Steam 安装目录")
    except OSError as exc:
        return su.fatal("访问 Steam 目录失败：{0}".format(exc), exc)

    try:
        appids = list(args.appids) if args.appids else su.collect_app_ids(vdf_path)
    except (OSError, ValueError, su.SteamError) as exc:
        return su.fatal("解析 libraryfolders.vdf 失败：{0}".format(exc), exc)

    if not appids:
        print(Fore.YELLOW + "没有在 Steam 库里找到任何 appid")
        return 0

    print("图标目录：" + Fore.GREEN + str(icon_dir))
    print("待处理 {0} 个条目\n".format(len(appids)))

    ok, skipped, failed = run(appids, icon_dir, args)

    print("\n{0}完成：新增/修复 {1}，跳过 {2}，失败 {3}，共 {4}{5}".format(
        Fore.CYAN, ok, skipped, failed, len(appids), Style.RESET_ALL))
    if ok:
        print(Fore.YELLOW + "图标已写入，重启 Steam 后生效（必要时刷新图标缓存）。")
    if failed:
        print(Fore.YELLOW + "有 {0} 项失败，可加 --appid <id> 单独重跑看详细原因。".format(failed))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(su.run_guarded(main))
