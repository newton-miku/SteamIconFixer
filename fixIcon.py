import requests,vdf
import os,winreg
from time import sleep
from colorama import Fore, Style, init
# ***************************************************************
#               Steam图标修复——优化版
#			本工具用于修复Steam图标空白问题
#               @author：newton_miku
#
#	Github:newton_miku	https://github.com/newton-miku
#	BiliBili：鑫说数境	https://space.bilibili.com/24915794
#		My Blog:	https://blog.ddxnb.cn
# ***************************************************************
def read_reg(ep, p = r"", k = ''):
    try:
        key = winreg.OpenKeyEx(ep, p)
        value = winreg.QueryValueEx(key,k)
        if key:
            winreg.CloseKey(key)
        return value[0]
    except Exception as e:
        return None
    return None

#requests.packages.urllib3.util.connection.HAS_IPV6 = False
init(autoreset=True)
print(Fore.GREEN + "本脚本由newton_miku制作")
print(Fore.GREEN + "Powered by newton_miku")
print(Fore.GREEN + "Github:newton_miku	https://github.com/newton-miku")
print(Fore.GREEN + "B站：Sin说数境		https://space.bilibili.com/24915794")
print(Fore.GREEN + "博客地址：https://blog.ddxnb.cn\n")
#版权信息

Path1=str(read_reg(ep=winreg.HKEY_LOCAL_MACHINE,p=r"SOFTWARE\Wow6432Node\Valve\Steam",k = 'InstallPath'))
Path2 = str(read_reg(ep = winreg.HKEY_LOCAL_MACHINE, p = r"SOFTWARE\Valve\Steam", k = 'InstallPath'))
pathCtrl=0
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
	print(Fore.RED +"无法获取到Steam库相关文件，请检查"+Path+'\config\libraryfolders.vdf 文件是否存在')
	input('请按任意键退出')
	exit()
vdfText = vdf.parse(open(Path + '\config\libraryfolders.vdf'))
print("正在读取Steam本地库")
#print(vdfText)
for i in vdfText['libraryfolders']:
	for id in vdfText['libraryfolders'][str(i)]['apps']:
		appid = int(id)
		if(appid != 228980):
			app_info_url = f"http://steama.ddxnb.cn/v1/info/{appid}"			#gcore的CDN
			#app_info_url = f"http://steam.ddxnb.cn/v1/info/{appid}"			#cloudflare的worker
			#app_info_url = f"http://steamapi.ddxnb.cn/v1/info/{appid}"			#我的香港服务器
			#app_info_url = f"https://api.steamcmd.net/v1/info/{appid}"			#源地址
			print(f"正在获取信息，appid:{appid}",end="\r")
			try:
				apps_info_data = requests.get(app_info_url)			
				times = 0
				while apps_info_data.status_code != 200 and times<3:
					times += 1
					print(Fore.YELLOW + f"获取失败，正在重试，次数{times}", end="\r")
					apps_info_data = requests.get(app_info_url)
				if apps_info_data.status_code!=200:
					print(Fore.RED + f"无法获取到appid：{appid}的图标名(已重试{times}次)，跳过当前应用")
					print(Fore.RED + f"错误代码{apps_info_data.status_code} {apps_info_data.reason}")
					sleep(0.3)#增加等待时间，避免频繁请求导致的拒绝
					continue
				apps_info = apps_info_data.json()
				if apps_info['data'][str(appid)]["_missing_token"]:
					print(Fore.RED + f"该应用要求鉴权token，将跳过，appid：{appid}")
					continue
				common = apps_info['data'][str(appid)]['common']
				pic_name = common['clienticon'] #设置文件夹的名字
				url = f"http://cdn.akamai.steamstatic.com/steamcommunity/public/images/apps/{appid}/{pic_name}.ico"#拼接网址
				app_name = common.get('name_localized', {}).get('schinese') or common.get('name_localized', {}).get('tchinese') or common.get('name', '')

				
				if os.path.exists(f"{Path}\\steam\\games\\{pic_name}.ico"):
					print(Fore.LIGHTGREEN_EX + f"{app_name}"+Fore.YELLOW+f" 的图标已存在，appid：{appid}")
					sleep(0.3)#增加等待时间，避免频繁请求导致的拒绝
					continue
				print(f"正在下载 {app_name} 的图标")
				r = requests.get(url)#下载图片
				# 写入图片
				with open(f"{Path}\\steam\\games\\{pic_name}.ico", "wb") as f:
					f.write(r.content)
					f.close()
			except Exception as e:
				print(Fore.RED + '发生错误，错误类型是',e.__class__.__name__)
				print(Fore.RED + '错误明细是',e)
				print(traceback.print_exc())
input('请按任意键退出')