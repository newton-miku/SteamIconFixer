# Steam图标修复/SteamIconFixer
修复Steam游戏图标显示空白

自动修复Steam图标显示问题，依靠读取Windows的注册表识别Steam安装地址，故如果注册表中没有相关项，则可能需要重新安装一遍steam以注册相关注册表项

Fix your Steam games' icon on Windows.

Automatically fix the issue with Steam icon display, relying on reading the Windows registry to identify the Steam installation address. Therefore, if there are no relevant keys in the registry, it may be necessary to reinstall Steam to register the relevant registry keys

### 如何使用（How to Use）

点击页面右侧的[Release](https://github.com/newton-miku/SteamIconFixer/releases/latest)，下载最新版本打开即可

如果您的设备具有python环境，也可使用下面的代码运行

Click on the Release button on the right side of the page to download the latest version and open it.

If your device has a Python environment, you can also use the following code to run it

```bash
git clone https://github.com/newton-miku/SteamIconFixer.git
cd SteamIconFixer
pip install -r requirements.txt -i http://mirrors.aliyun.com/pypi/simple/
python fixIcon.py
```

### 点赞（Like）

如果您觉得项目好用，请点击页面右上角的Star⭐

If you like this project, please click the Star⭐ in the upper right corner of the page 
