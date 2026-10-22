# -*- coding: utf-8 -*-
"""Steam 图标修复（legacy：直连 Steam 客户端协议）

已由 FastFixIcon.py 取代，仅保留给需要走 Steam 协议的老用户。
额外依赖：pip install -r requirements-legacy.txt  （steam 包）

修复点：
  * 原来 apps_info['apps']['_missing_token'] 取错了层级，遇到第一个
    需要鉴定 token 的 appid 就 KeyError 崩掉；现在按 appid 判断并跳过。
  * clienticon 缺失（Spacewar、Steam Linux Runtime 等）不再抛 KeyError。
  * 下载内容做 ico/png 校验，失败不再写出空文件。
  * 写入走原子替换，并且只写 <Steam>/steam/games 目录。
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional, Sequence

import steam_utils as su
from steam_utils import Fore, Style

su.setup_console()

try:
    import requests
except ImportError as _exc:  # pragma: no cover - 缺依赖时给出可读提示
    sys.exit(su.missing_dependency("requests", "requirements.txt", _exc))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="icon", description="修复 Steam 游戏图标显示空白（Steam 协议版，legacy）")
    parser.add_argument("--steam-path", help="手动指定 Steam 安装目录（默认读注册表）")
    parser.add_argument("--appid", type=int, action="append", dest="appids", help="只处理指定 appid")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    parser.add_argument("--no-pause", action="store_true", help="结束后不等待按键")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    print(Fore.GREEN + su.BANNER)
    print(Fore.YELLOW + "提示：本脚本为 legacy 版本，建议改用 FastFixIcon.py\n")

    args = build_parser().parse_args(argv)
    if args.no_pause:
        os.environ[su.NO_PAUSE_ENV] = "1"

    try:
        from steam.client import SteamClient
    except ImportError as exc:
        return su.missing_dependency("steam.client", "requirements-legacy.txt", exc)

    try:
        steam_dir = su.find_steam_dir(args.steam_path)
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

    print("即将连接到 Steam 数据库")
    client = SteamClient()
    try:
        client.anonymous_login()
    except Exception as exc:  # 网络/协议错误，给出原因而不是静默退出
        return su.fatal("连接 Steam 失败：{0}: {1}".format(type(exc).__name__, exc), exc)
    print("成功连接到 Steam 数据库\n")

    session = requests.Session()
    ok = skipped = failed = 0
    for index, appid in enumerate(appids, start=1):
        prefix = "[{0}/{1}]".format(index, len(appids))
        try:
            response = client.get_product_info(apps=[appid])
        except Exception as exc:
            failed += 1
            print("{0} {1}[失败]{2} appid={3} 取信息失败：{4}".format(
                prefix, Fore.RED, Style.RESET_ALL, appid, exc))
            continue

        apps = (response or {}).get("apps") or {}
        info = apps.get(appid) or apps.get(str(appid))
        if not isinstance(info, dict):
            skipped += 1
            print("{0} {1}[跳过]{2} appid={3} 没有返回产品信息（可能要求 token）".format(
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
        if target.is_file() and target.stat().st_size > 0:
            skipped += 1
            print("{0} {1}[已有]{2} appid={3} {4}".format(
                prefix, Fore.LIGHTGREEN_EX, Style.RESET_ALL, appid, name))
            continue

        if args.dry_run:
            ok += 1
            print("{0} {1}[试运行]{2} appid={3} {4} -> {5}".format(
                prefix, Fore.CYAN, Style.RESET_ALL, appid, name, target.name))
            continue

        data = None
        for url in su.icon_urls(appid, icon):
            try:
                got = session.get(url, timeout=12)
            except requests.RequestException:
                continue
            if got.status_code == 200 and su.is_valid_icon(got.content):
                data = got.content
                break
        if data is None:
            failed += 1
            print("{0} {1}[失败]{2} appid={3} {4} 下载失败".format(
                prefix, Fore.RED, Style.RESET_ALL, appid, name))
            continue

        try:
            su.atomic_write(target, data)
        except OSError as exc:
            failed += 1
            print("{0} {1}[失败]{2} appid={3} {4} 写入失败：{5}".format(
                prefix, Fore.RED, Style.RESET_ALL, appid, name, exc))
            continue

        ok += 1
        print("{0} {1}[完成]{2} appid={3} {4}".format(prefix, Fore.GREEN, Style.RESET_ALL, appid, name))

    print("\n{0}完成：新增/修复 {1}，跳过 {2}，失败 {3}，共 {4}{5}".format(
        Fore.CYAN, ok, skipped, failed, len(appids), Style.RESET_ALL))
    if failed:
        print(Fore.YELLOW + "有 {0} 项失败，可加 --appid <id> 单独重跑看详细原因。".format(failed))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(su.run_guarded(main))
