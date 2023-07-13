import asyncio
import aiohttp
import time
import json


async def fetch_url(url):
    try:
        start_time = time.perf_counter()
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    elapsed_time = time.perf_counter() - start_time
                    result = await response.json()
                    return url, elapsed_time, result
                else:
                    return url, float('inf'), None
    except aiohttp.ClientError:
        return url, float('inf'), None


async def get_fastest_response(urls):
    tasks = []
    for url in urls:
        task = asyncio.create_task(fetch_url(url))
        tasks.append(task)

    fastest_url = None
    fastest_time = float('inf')
    fastest_result = None

    while tasks:
        done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        for task in done:
            try:
                url, elapsed_time, result = await task
                if elapsed_time < fastest_time:
                    fastest_url = url
                    fastest_time = elapsed_time
                    fastest_result = result
            except asyncio.CancelledError:
                pass

        if fastest_url and fastest_time == float('inf'):
            continue

        for task in tasks:
            task.cancel()

    return fastest_url, fastest_result



async def get_fastest_result(appid):
    urls = [
        f'http://steama.ddxnb.cn/v1/info/{appid}',#gcore的CDN
        f'http://steam.ddxnb.cn/v1/info/{appid}',#CloudFlare的Workers路由
        f'http://steamapi.ddxnb.cn/v1/info/{appid}',#我的香港服务器
        f'http://api.steamcmd.net/v1/info/{appid}'#源地址
    ]
    fastest_url, fastest_result = await get_fastest_response(urls)
    return fastest_result['data'][str(appid)]
