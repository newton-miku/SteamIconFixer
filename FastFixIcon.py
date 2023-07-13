import os,winreg
from time import sleep
import vdf
from colorama import Fore, Style, init
from fastest_request import get_fastest_result
import asyncio

init(autoreset=True)
print(Fore.GREEN + "本脚本由newton_miku制作")
print(Fore.GREEN + "Powered by newton_miku")
print(Fore.GREEN + "Github:newton_miku	https://github.com/newton-miku")
print(Fore.GREEN + "B站：Sin说数境		https://space.bilibili.com/24915794")
print(Fore.GREEN + "博客地址：https://blog.ddxnb.cn\n")

def read_reg(ep, p="", k=''):
    try:
        key = winreg.OpenKeyEx(ep, p)
        value = winreg.QueryValueEx(key, k)
        if key:
            winreg.CloseKey(key)
        return value[0]
    except Exception as e:
        return None
    return None

Path1 = str(read_reg(ep=winreg.HKEY_LOCAL_MACHINE, p=r"SOFTWARE\Wow6432Node\Valve\Steam", k='InstallPath'))
Path2 = str(read_reg(ep=winreg.HKEY_LOCAL_MACHINE, p=r"SOFTWARE\Valve\Steam", k='InstallPath'))
pathCtrl = 0
Path = ""
if not os.path.exists(Path1):
    Path1 = ""
    pathCtrl += 1
if not os.path.exists(Path2):
    Path2 = ""
    pathCtrl += 10
if pathCtrl == 0 or pathCtrl == 10:
    Path = Path1
elif pathCtrl == 1:
    Path = Path2
else:
    print("无法获取到Steam路径")
    input('请按任意键退出')
    exit()
print("成功获取到Steam路径：" + Fore.GREEN + Path)
if not os.path.exists(Path + '\config\libraryfolders.vdf'):
    print(Fore.RED + "无法获取到Steam库相关文件，请检查" + Path + '\config\libraryfolders.vdf 文件是否存在')
    input('请按任意键退出')
    exit()
vdfText = vdf.parse(open(Path + '\config\libraryfolders.vdf'))
print("正在读取Steam本地库")

async def main():
    for i in vdfText['libraryfolders']:
        for id in vdfText['libraryfolders'][str(i)]['apps']:
            appid = int(id)
            if appid != 228980:
                print("正在获取信息，appid:" + str(appid),end="\r")
                result = await get_fastest_result(appid)
                if result is None:
                    print(Fore.RED + f"无法获取到appid：{appid}的图标名，跳过当前应用")
                    continue
                pic_name = result['common']['clienticon']
                url = f"http://cdn.akamai.steamstatic.com/steamcommunity/public/images/apps/{appid}/{pic_name}.ico"
                app_name = result['common'].get('name_localized', {}).get('schinese') or result['common'].get('name_localized', {}).get('tchinese') or result['common'].get('name', '')
                if os.path.exists(f"{Path}\\steam\\games\\{pic_name}.ico"):
                    print(Fore.LIGHTGREEN_EX + f"{app_name}"+Fore.YELLOW+f" 的图标已存在，appid：{appid}")
                    sleep(0.5)  # 增加等待时间，避免频繁请求导致的拒绝
                    continue
                print(f"正在下载 {app_name} 的图标")
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url) as response:
                            if response.status == 200:
                                with open(f"{Path}\\steam\\games\\{pic_name}.ico", "wb") as f:
                                    f.write(await response.read())
                                print(Fore.GREEN + f"{app_name} 的图标下载成功")
                            else:
                                print(Fore.RED + f"无法下载 {app_name} 的图标，错误代码: {response.status}")
                except aiohttp.ClientError as e:
                    print(Fore.RED + f"发生错误，无法下载 {app_name} 的图标，错误类型是 {e.__class__.__name__}")
                    print(Fore.RED + f"错误明细是 {str(e)}")

asyncio.run(main())
input('请按任意键退出')
