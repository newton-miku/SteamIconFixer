# Steam图标修复 / SteamIconFixer

修复 Steam 游戏图标显示空白（列表里只有灰块）。

Fix blank/missing game icons in the Steam library on Windows.

程序通过读取 Windows 注册表定位 Steam 安装目录，再读取 `config/libraryfolders.vdf`
拿到本机已安装的 appid，最后从多个信息源取到该应用的 `clienticon` 文件名，
把官方 `.ico` 下载到 `<Steam>/steam/games/`。

> 如果注册表里没有 Steam 相关键值，读不到安装路径，重新安装一次 Steam 让注册表项重新写入即可。

---

## 快速使用

### 方式一：下载 Release（推荐）

点击右侧 [Releases](https://github.com/newton-miku/SteamIconFixer/releases/latest)，下载
`FastFixIcon.exe` 双击运行。运行结束后重启 Steam 生效。

### 方式二：源码运行

```bash
git clone https://github.com/newton-miku/SteamIconFixer.git
cd SteamIconFixer
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
python FastFixIcon.py
```

需要 Python 3.9+（CI 使用 3.12 构建 exe）。

---

## 两个入口

| 脚本 | 实现 | 说明 |
| --- | --- | --- |
| `FastFixIcon.py` | aiohttp + asyncio | **推荐**。并发竞速取信息，速度快；默认重试与超时完整 |
| `fixIcon.py` | requests | 同步版，无 asyncio 依赖；功能一致 |
| `icon.py` | steam 协议库 | legacy，仅老用户需要，额外装 `requirements-legacy.txt` |

### 命令行参数

```text
--steam-path PATH   手动指定 Steam 安装目录（默认读注册表）
--appid ID          只处理指定 appid，可重复传入
--jobs N            并发数，默认 4（仅 FastFixIcon）
--timeout SECONDS   单次请求超时，默认 12
--dry-run           只报告将要做什么，不写文件
--force             图标已存在也重新下载
--proxy URL         走代理，例如 http://127.0.0.1:7890（默认不用代理）
--no-pause          结束后不等待按键
```

示例：

```bash
python FastFixIcon.py --jobs 8
python FastFixIcon.py --appid 730 --appid 620 --dry-run
python FastFixIcon.py --proxy http://127.0.0.1:7890
python fixIcon.py --steam-path "D:\Steam"
```

退出码：`0` 全部成功/跳过，`1` 存在失败项，`130` 手动中断。

---

## 关于个别请求失败

程序内部准备了多个信息源，任何一个返回 403 / 超时 / 非 JSON，
都会自动跳到下一个，所以偶尔看到某一行报错是预期行为，
只要最终统计里不是大面积"失败"就不用管。

`FastFixIcon.py` 会并发请求，取最先成功的一份，其余任务立即取消；
全部失败时该 appid 标记为"失败"并继续处理下一个，不会中断整轮任务。

图标本体来自 Steam 官方 CDN，按主域名 → 备用域名的顺序回退。

---

## 常见问题

**列表里还有个别游戏是空图标？**
这些条目在 Steam 数据库里本来就没有 `clienticon`（例如 Spacewar、Steam Linux Runtime、
部分工具/DLC）。脚本会打印 `[跳过] ... 没有 clienticon 字段`，属于正常情况。

**修复后图标还是空白？**
1. 完全退出 Steam（托盘里也要退出）再启动；
2. 图标文件名是 Steam 的缓存键，若 Steam 已缓存了空图标，重启后通常会自动重建；
3. 仍不行时删除 `<Steam>/steam/games/` 下对应的 `*.ico`，再运行一次 `--force`。

**报"无法获取到 Steam 安装路径"？**
注册表缺少 `HKLM\SOFTWARE\Valve\Steam` 的 `InstallPath`。
重装一次 Steam，或用 `--steam-path` 手动指定目录。

**为什么不再直接把响应写进 .ico 了？**
旧版本不管下载结果如何都会写文件，一次 403/404 就会留下 0 字节或 HTML 内容的
`.ico`，之后"文件已存在"的判断还会一直跳过它。现在下载后会校验 `.ico`/`PNG`
文件头，校验通过才原子替换写入。

---

## 双击闪退、看不到报错？

程序不会静默退出：任何未预期的异常都会写日志、打印路径，然后以退出码 `1` 结束。

**1. 改用命令行运行一次**（能完整看到输出）

```bat
cd /d 脚本所在目录
python FastFixIcon.py --steam-path "D:\Steam"
```

**2. 看日志**

崩溃时会在**程序所在目录**（双击运行就是 exe 旁边、源码运行就是脚本旁边）
生成一个 `crash-年月日-时分秒.log`，内容包含：Python 版本、系统、工作目录、
命令行参数、SSL、代理、各依赖版本、完整堆栈，以及最后那段报错。

目录不可写（比如装在 `Program Files`）时才退到 `%LOCALAPPDATA%\SteamIconFixer`，
程序会把实际路径打印出来。

**没有崩溃就不会产生任何日志文件**：跑完正常退出、只是有几项失败，目录里都是干净的。

把这个文件直接发给作者就能定位，不用复述屏幕上的内容。

**3. 关于结尾等待**

双击运行时窗口会停住等按键；带任何命令行参数、设置环境变量
`STEAMFIX_NO_PAUSE=1`、或把输出重定向到文件时不再等待。

---

## 网络与代理

启动时会打印检测到的代理配置（环境变量 `HTTP(S)_PROXY` / `ALL_PROXY`
以及 Windows 系统代理设置），出问题时先看这几行：

- 想让程序走代理：加 `--proxy http://127.0.0.1:7890`
- 想强制直连：临时关掉系统代理或代理软件后再运行
- 信息能拿到但图标下载失败：通常是 Steam 官方 CDN 被挡，换网络再试

退出码：`0` 成功/全部跳过，`1` 存在失败项，`130` 手动中断。

---

## 开发

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -t . -v   # 离线单测，不访问网络
python -m compileall -q .
```

打 exe：

```bash
pip install pyinstaller
pyinstaller --onefile --name FastFixIcon FastFixIcon.py
pyinstaller --onefile --name fixIcon fixIcon.py
```

CI（`.github/workflows/main.yml`）在 push 时跑语法检查与单测，创建 Release 时构建两个
`exe` 并上传到该 Release。

---

## 点赞 / Like

如果这个项目帮到了你，请点一下右上角的 Star ⭐
