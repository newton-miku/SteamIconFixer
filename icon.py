from steam.client import SteamClient
import requests,vdf
import os,winreg
# ***************************************************************
#                 Steam图标修复
#		本工具用于修复Steam图标空白问题
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

print("本脚本由newton_miku制作")
print("Powered by newton_miku")
print("Github:newton_miku	https://github.com/newton-miku")
print("B站：鑫说数境		https://space.bilibili.com/24915794")
print("博客地址：https://blog.ddxnb.cn")
print()
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
print("成功获取到Steam路径："+Path)
if not os.path.exists(Path + '\config\libraryfolders.vdf'):
	print("无法获取到Steam库相关文件，请检查"+Path+'\config\libraryfolders.vdf 文件是否存在')
	input('请按任意键退出')
	exit()
vdfText = vdf.parse(open(Path + '\config\libraryfolders.vdf'))
print("即将连接到Steam数据库")
client = SteamClient()
client.anonymous_login()
assert client.logged_on
print("成功连接到Steam数据库")
#print(vdfText)
for i in vdfText['libraryfolders']:
	for id in vdfText['libraryfolders'][str(i)]['apps']:
		appid = int(id)
		if(appid != 228980):
			apps_info = client.get_product_info(apps=[appid, ])
			#print(apps_info['apps'][appid]['common'])
			pic_name = apps_info['apps'][appid]['common']['clienticon'] #设置文件夹的名字
			url = "http://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/apps/"+str(appid) + "/" + str(pic_name) + ".ico"#拼接网址
			print(url)
			r = requests.get(url)#下载图片
			# 写入图片
			with open(str(Path + "\steam\games\/" + pic_name)+".ico", "wb") as f:
				f.write(r.content)
				f.close()
input('请按任意键退出')