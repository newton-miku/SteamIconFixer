# -*- coding: utf-8 -*-
"""在多个 appid 信息源之间竞速，返回最先成功的那一份。

某个入口失败（超时、非 200、响应不是 JSON）时直接换下一个，
全部失败按网络故障处理，由调用方跳过该 appid。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Dict, List, Optional, Sequence, Tuple

import aiohttp

from steam_utils import INFO_ENDPOINTS

#: 只要 JSON，HTML 错误页直接丢弃
_ACCEPTED_CONTENT_TYPES = frozenset(
    {"application/json", "text/json", "text/plain", "application/octet-stream"}
)

_app_cache: Dict[int, dict] = {}


def make_timeout(total: float = 12.0) -> aiohttp.ClientTimeout:
    """连接/读取分别限时，避免某个入口挂死拖住整轮任务。"""
    total = max(1.0, float(total))
    return aiohttp.ClientTimeout(total=total, connect=min(6.0, total), sock_read=total)


def create_session(timeout: Optional[float] = None) -> aiohttp.ClientSession:
    """创建共享会话（超时由 make_timeout 统一管理）。"""
    return aiohttp.ClientSession(timeout=make_timeout(timeout or 12.0))


async def _fetch_info(
    session: aiohttp.ClientSession, url: str, proxy: Optional[str] = None
) -> Optional[Tuple[float, dict]]:
    """取一个入口的信息；任何失败都返回 None，绝不抛异常打断竞速。"""
    started = time.perf_counter()
    try:
        async with session.get(url, proxy=proxy) as response:
            body = await response.read()
            status = response.status
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return None

    if status != 200:
        return None
    if content_type and content_type not in _ACCEPTED_CONTENT_TYPES:
        return None  # HTML 错误页

    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return None
    return time.perf_counter() - started, payload


async def get_fastest_response(
    urls: Sequence[str],
    session: Optional[aiohttp.ClientSession] = None,
    proxy: Optional[str] = None,
) -> Tuple[Optional[str], Optional[dict]]:
    """并发请求，拿到第一个成功的就取消其余任务。全部失败返回 (None, None)。"""
    owned = session is None
    if owned:
        session = create_session()

    pending: Dict["asyncio.Future", str] = {
        asyncio.ensure_future(_fetch_info(session, url, proxy)): url for url in urls
    }
    try:
        while pending:
            done, _ = await asyncio.wait(set(pending), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                url = pending.pop(task)
                try:
                    outcome = task.result()
                except Exception:  # pragma: no cover - _fetch_info 已吞掉常规异常
                    outcome = None
                if outcome is not None:
                    return url, outcome[1]
        return None, None
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if owned:
            await session.close()


def _extract(payload: Optional[dict], appid: int) -> Optional[dict]:
    """从 {"data": {"<appid>": {...}}} 里取出 appid 对应的信息。"""
    if not payload:
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    info = data.get(str(appid))
    return info if isinstance(info, dict) else None


async def get_app_info(
    appid: int,
    session: Optional[aiohttp.ClientSession] = None,
    proxy: Optional[str] = None,
) -> Optional[dict]:
    """返回单个 appid 的 appinfo 片段，失败返回 None（不抛异常）。"""
    try:
        appid = int(appid)
    except (TypeError, ValueError):
        return None
    if appid in _app_cache:
        return _app_cache[appid]

    urls: List[str] = [tpl.format(appid=appid) for tpl in INFO_ENDPOINTS]
    _, payload = await get_fastest_response(urls, session=session, proxy=proxy)
    info = _extract(payload, appid)
    if info is not None:
        _app_cache[appid] = info
    return info


async def get_fastest_result(
    appid: int,
    session: Optional[aiohttp.ClientSession] = None,
    proxy: Optional[str] = None,
) -> Optional[dict]:
    """兼容旧接口：原来返回 result['data'][str(appid)]。

    老实现里 fastest_result 为 None 时仍然去下标取值，直接 TypeError 崩掉；
    这里统一返回 None，由调用方跳过该 appid。
    """
    return await get_app_info(appid, session=session, proxy=proxy)


def clear_cache() -> None:
    _app_cache.clear()
