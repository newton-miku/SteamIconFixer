# -*- coding: utf-8 -*-
"""Steam 图标修复工具 —— 公共函数。

FastFixIcon.py / fixIcon.py / icon.py 共用这里的能力：

* 通过 Windows 注册表定位 Steam 安装目录（HKLM 32/64 位 + HKCU 回退）
* 解析 libraryfolders.vdf，收集本机已安装的 appid
* 校验 clienticon 字段、拼装官方图标地址
* 校验下载内容真的是 .ico / PNG，并以原子方式落盘
* 控制台/日志兜底：任何异常都不会让窗口一闪而过

Python 3.9+
"""
from __future__ import annotations

import os
import platform
import re
import sys
import sysconfig
import tempfile
import time
import traceback
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, TextIO, Tuple

try:  # 允许在非 Windows 上导入（单测用），实际功能只在 Windows 上可用
    import winreg  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]


class _NoColor:
    """颜色占位符：任何属性都返回空串，保证 format 调用不会吐字面量。"""

    def __getattr__(self, _name: str) -> str:  # noqa: D105
        return ""


# colorama 只是"锦上添花"，缺了也不能让脚本崩掉
try:  # pragma: no cover - 取决于安装环境
    from colorama import Fore as Fore, Style as Style, init as _colorama_init
except ImportError:  # pragma: no cover
    Fore = _NoColor()  # type: ignore[assignment]
    Style = _NoColor()  # type: ignore[assignment]
    _colorama_init = None  # type: ignore[assignment]


__all__ = [
    "APP_NAME",
    "INFO_ENDPOINTS",
    "STEAM_ICON_CDN",
    "EXCLUDED_APPIDS",
    "Fore",
    "Style",
    "SteamError",
    "SteamNotFoundError",
    "SteamLibraryError",
    "MissingDependencyError",
    "BANNER",
    "find_steam_dir",
    "find_library_folders",
    "load_vdf",
    "collect_app_ids",
    "icon_name",
    "display_name",
    "icon_urls",
    "is_valid_icon",
    "ensure_icon_dir",
    "atomic_write",
    "setup_console",
    "pause_if_interactive",
    "log_dir",
    "program_dir",
    "append_log",
    "system_info",
    "diagnostics_text",
    "log_exception",
    "fatal",
    "missing_dependency",
    "run_guarded",
    "proxy_hints",
    "detect_proxy",
]

# ---------------------------------------------------------------- 常量

APP_NAME = "SteamIconFixer"

#: 依次尝试的 appid 信息源（谁先返回用谁，全部失败按网络故障处理）：
#: 镜像、主站、上游源站，任一可用即可
INFO_ENDPOINTS: Tuple[str, ...] = (
    "https://steama.ddxnb.cn/v1/info/{appid}",
    "https://steam.ddxnb.cn/v1/info/{appid}",
    "https://api.steamcmd.net/v1/info/{appid}",
)

#: Steam 官方图标 CDN（按顺序回退）
STEAM_ICON_CDN: Tuple[str, ...] = (
    "https://cdn.akamai.steamstatic.com/steamcommunity/public/images/apps/{appid}/{icon}.ico",
    "https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/apps/{appid}/{icon}.ico",
)

#: 不是游戏、也没有 clienticon 的特殊条目
EXCLUDED_APPIDS = frozenset({228980})  # Steamworks Common Redistributables

#: .ico 与 PNG 的文件头，用于拦截错误页、空响应等无效数据
ICON_MAGIC = (b"\x00\x00\x01\x00", b"\x89PNG\r\n\x1a\n")
MAX_ICON_BYTES = 8 * 1024 * 1024

#: Steam 的图标文件名是 40 位十六进制 sha1，限制字符集同时挡掉路径穿越
_ICON_NAME_RE = re.compile(r"^[0-9a-fA-F]{16,64}$")

#: 环境变量：设置后任何情况下都不在结尾等待按键（方便脚本/CI 调用）
NO_PAUSE_ENV = "STEAMFIX_NO_PAUSE"
#: 环境变量：覆盖日志目录
LOG_DIR_ENV = "STEAMFIX_LOG_DIR"
#: 环境变量：覆盖代理
PROXY_ENV_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")

BANNER = (
    "本脚本由newton_miku制作\n"
    "Powered by newton_miku\n"
    "Github:newton_miku\thttps://github.com/newton-miku\n"
    "B站：鑫说数境\t\thttps://space.bilibili.com/24915794\n"
    "博客地址：https://blog.ddxnb.cn"
)


# ---------------------------------------------------------------- 异常

class SteamError(RuntimeError):
    """本工具的业务异常基类。"""


class SteamNotFoundError(SteamError):
    def __init__(self, tried: Sequence[str]):
        self.tried = list(tried)
        detail = "、".join(self.tried) if self.tried else "（注册表中没有任何 Steam 相关键值）"
        super().__init__(
            "无法获取到 Steam 安装路径，请检查注册表是否存在 "
            "HKLM\\SOFTWARE\\Valve\\Steam 的 InstallPath，或重新安装一次 Steam。已尝试：" + detail
        )


class SteamLibraryError(SteamError):
    pass


class MissingDependencyError(SteamError):
    """运行环境缺少必需的第三方包。"""


# ---------------------------------------------------------------- 注册表

def read_reg_value(ep, path: str, name: str) -> Optional[str]:
    """安全读取一个注册表字符串值，失败返回 None。"""
    if winreg is None:
        return None
    key = None
    try:
        key = winreg.OpenKey(ep, path)
        value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    finally:
        if key is not None:
            winreg.CloseKey(key)
    return value if isinstance(value, str) and value.strip() else None


def _registry_candidates() -> List[str]:
    if winreg is None:
        return []
    views = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
    )
    out: List[str] = []
    for root, sub, name in views:
        value = read_reg_value(root, sub, name)
        if value:
            out.append(value)
    return out


def find_steam_dir(explicit: Optional[str] = None) -> Path:
    """定位 Steam 安装目录；显式传入时优先使用传入值。"""
    candidates = [explicit] if explicit else _registry_candidates()
    tried: List[str] = []
    for raw in candidates:
        text = str(raw).strip().strip('"')
        if not text:
            continue
        tried.append(text)
        path = Path(text)
        if path.is_dir():
            return path
    raise SteamNotFoundError(tried)


# ---------------------------------------------------------------- VDF / 库

def _vdf_module():
    try:
        import vdf  # noqa: WPS433 - 延迟导入，缺包时给可读提示而不是闪退
    except ImportError as exc:  # pragma: no cover - 取决于安装环境
        raise MissingDependencyError(
            "缺少依赖 vdf，请执行：pip install -r requirements.txt"
        ) from exc
    return vdf


def find_library_folders(steam_dir: Path) -> Path:
    """返回 libraryfolders.vdf 的真实路径（新版在 config/，旧版在 steamapps/）。"""
    for rel in (Path("config") / "libraryfolders.vdf",
                Path("steamapps") / "libraryfolders.vdf"):
        candidate = steam_dir / rel
        if candidate.is_file():
            return candidate
    raise SteamLibraryError(
        "无法获取到 Steam 库相关文件，请检查 {0} 是否存在".format(
            steam_dir / "config" / "libraryfolders.vdf"
        )
    )


def load_vdf(path: Path) -> Dict:
    """读取 VDF（vdf 3.2+ 用 load，老版本用 parse）。"""
    vdf = _vdf_module()
    parser = getattr(vdf, "load", None) or getattr(vdf, "parse")
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return parser(handle)


def collect_app_ids(vdf_path: Path) -> List[int]:
    """收集所有库目录里已安装的 appid（去重、排序、剔除特殊条目）。"""
    data = load_vdf(vdf_path)
    root = data.get("libraryfolders") or data.get("LibraryFolders") or {}
    if not isinstance(root, dict):
        return []

    seen = set()
    app_ids: List[int] = []
    for entry in root.values():
        if not isinstance(entry, dict):  # 老格式：数值键直接是库路径
            continue
        apps = entry.get("apps")
        if not isinstance(apps, dict):
            continue
        for raw in apps:
            try:
                appid = int(str(raw).strip())
            except (TypeError, ValueError):
                continue
            if appid in EXCLUDED_APPIDS or appid in seen:
                continue
            seen.add(appid)
            app_ids.append(appid)
    app_ids.sort()
    return app_ids


# ---------------------------------------------------------------- app 信息

def icon_name(common: object) -> Optional[str]:
    """取 common.clienticon 并做合法性校验；缺失/非法返回 None。

    很多条目（Spacewar、Steam Linux Runtime、工具类）根本没有 clienticon，
    老代码直接 result['common']['clienticon'] 会 KeyError 把整轮任务打断。
    """
    if not isinstance(common, dict):
        return None
    raw = common.get("clienticon")
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not _ICON_NAME_RE.match(raw):
        return None
    return raw.lower()


def display_name(common: object) -> str:
    """优先中文名，其次英文名，最后回退到 name。"""
    if not isinstance(common, dict):
        return ""
    localized = common.get("name_localized")
    if isinstance(localized, dict):
        for key in ("schinese", "tchinese", "english"):
            value = localized.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    name = common.get("name")
    return name.strip() if isinstance(name, str) else ""


def icon_urls(appid: int, icon: str) -> List[str]:
    return [tpl.format(appid=appid, icon=icon) for tpl in STEAM_ICON_CDN]


def is_valid_icon(data: Optional[bytes]) -> bool:
    """判断响应体是不是真正的图标（挡住 HTML 错误页、空 body）。"""
    if not data or len(data) < 12 or len(data) > MAX_ICON_BYTES:
        return False
    return any(data.startswith(magic) for magic in ICON_MAGIC)


# ---------------------------------------------------------------- 落盘

def ensure_icon_dir(steam_dir: Path) -> Path:
    """Steam 的图标缓存目录 <Steam>/steam/games。"""
    target = steam_dir / "steam" / "games"
    target.mkdir(parents=True, exist_ok=True)
    return target


def atomic_write(path: Path, data: bytes) -> None:
    """先写临时文件再原子替换，避免中途断电留下半个图标。"""
    tmp = path.with_name(path.name + ".part")
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------- 日志

def program_dir() -> Path:
    """程序所在目录：打包后是 exe 同级，源码运行时是脚本同级。"""
    if getattr(sys, "frozen", False):  # pragma: no cover - 仅打包后
        try:
            return Path(sys.executable).resolve().parent
        except OSError:
            pass
    return Path(__file__).resolve().parent


def log_dir() -> Path:
    """崩溃日志目录：就放在程序所在目录（双击运行时在 exe 旁边）。

    目录不可写（比如装在 Program Files）才退到用户目录、再退到临时目录。
    只有真的要写日志时才会调用这里，正常跑完不会留下任何文件。
    """
    override = os.environ.get(LOG_DIR_ENV)
    if override:
        candidates = [Path(override)]
    else:
        candidates = [program_dir()]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / APP_NAME)
        candidates.append(Path(tempfile.gettempdir()) / APP_NAME)
    for base in candidates:
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return base
        except OSError:
            continue
    return Path(tempfile.gettempdir())


def append_log(text: str, name: str) -> Optional[Path]:
    """追加一行日志；失败也不抛异常（日志坏了不能连累主流程）。"""
    try:
        path = log_dir() / name
        with open(path, "a", encoding="utf-8", errors="replace") as handle:
            handle.write(text.rstrip() + "\n")
        return path
    except OSError:
        return None


def _package_version(name: str) -> str:
    try:
        from importlib import metadata

        return metadata.version(name)
    except Exception:  # pragma: no cover - 包不存在或元数据损坏
        return "未安装"


def detect_proxy() -> Dict[str, str]:
    """收集环境变量代理与 Windows 系统代理设置（只读，不改变行为）。"""
    info: Dict[str, str] = {}
    for key in PROXY_ENV_VARS:
        value = os.environ.get(key)
        if value:
            info[key] = value
    if winreg is not None:
        base = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        enabled = read_reg_value(winreg.HKEY_CURRENT_USER, base, "ProxyEnable")
        server = read_reg_value(winreg.HKEY_CURRENT_USER, base, "ProxyServer")
        script = read_reg_value(winreg.HKEY_CURRENT_USER, base, "AutoConfigURL")
        if server:
            info["系统代理(ProxyServer)"] = server if str(enabled).strip() not in ("0", "") else server + "（ProxyEnable=0）"
        if script:
            info["PAC(AutoConfigURL)"] = script
    return info


def proxy_hints() -> List[str]:
    """把检测到的代理整理成给用户看的提示行。"""
    proxy = detect_proxy()
    if not proxy:
        return []
    lines = ["检测到代理配置，若连接失败可先关闭代理或改用直连网络重试："]
    for key, value in proxy.items():
        lines.append("    {0} = {1}".format(key, value))
    lines.append("也可以用 --proxy 参数指定代理，或设 {0}=1 关闭结尾等待。".format(NO_PAUSE_ENV))
    return lines


def system_info() -> List[str]:
    """运行环境快照：排查"某台机器上才闪退"这类问题就靠它。"""
    lines = [
        "时间: " + time.strftime("%Y-%m-%d %H:%M:%S"),
        "程序: " + APP_NAME,
        "Python: {0} ({1})".format(platform.python_version(), sys.executable),
        "系统: {0} {1} ({2})".format(platform.system(), platform.release(), platform.machine()),
        "工作目录: " + os.getcwd(),
        "参数: " + " ".join(sys.argv),
        "冻结打包: " + ("是" if getattr(sys, "frozen", False) else "否"),
        "控制台: stdout={0} stderr={1}".format(_stream_state(sys.stdout), _stream_state(sys.stderr)),
        "依赖: aiohttp={0} requests={1} vdf={2} colorama={3}".format(
            _package_version("aiohttp"),
            _package_version("requests"),
            _package_version("vdf"),
            _package_version("colorama"),
        ),
        "SSL: " + _ssl_version(),
        "编码: stdout={0} fs={1} preferred={2}".format(
            getattr(sys.stdout, "encoding", "?"),
            sys.getfilesystemencoding(),
            sysconfig.get_preferred_scheme("prefix") if hasattr(sysconfig, "get_preferred_scheme") else "?",
        ),
    ]
    proxy = detect_proxy()
    if proxy:
        lines.append("代理: " + "; ".join("{0}={1}".format(k, v) for k, v in proxy.items()))
    else:
        lines.append("代理: 未检测到")
    return lines


def _stream_state(stream: object) -> str:
    if stream is None:
        return "None"
    try:
        return "tty" if stream.isatty() else "pipe/file"  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):  # pragma: no cover
        return "unknown"


def _ssl_version() -> str:
    try:
        import ssl

        return getattr(ssl, "OPENSSL_VERSION", "unknown")
    except Exception:  # pragma: no cover
        return "unavailable"


def diagnostics_text() -> str:
    return "\n".join(system_info())


def log_exception(
    exc: Optional[BaseException] = None,
    title: str = "未处理的异常",
    extra: Optional[Iterable[str]] = None,
) -> Optional[Path]:
    """把异常连同运行环境写进日志文件，返回日志路径。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    body = ["=" * 60, "[{0}] {1}".format(time.strftime("%Y-%m-%d %H:%M:%S"), title), "-" * 60]
    body.extend(system_info())
    if extra:
        body.append("-" * 60)
        body.extend(str(item) for item in extra)
    body.append("-" * 60)
    if exc is not None:
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    else:
        text = traceback.format_exc()
    body.append(text.rstrip())
    body.append("=" * 60)
    payload = "\n".join(body)
    return append_log(payload, "crash-{0}.log".format(stamp))


# ---------------------------------------------------------------- 控制台

def _console_open() -> bool:
    """判断当前是不是"用户双击打开、等着看结果"的场景。"""
    try:
        return bool(sys.stdout is not None and sys.stdout.isatty())
    except (AttributeError, ValueError, OSError):  # pragma: no cover
        return False


def _ensure_streams() -> None:
    """无控制台的打包方式下 sys.stdout 可能是 None，直接 print 会炸。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        handle = open(log_dir() / "console.log", "a", encoding="utf-8", errors="replace", buffering=1)
    except OSError:  # pragma: no cover
        return
    if sys.stdout is None:
        sys.stdout = handle  # type: ignore[assignment]
    if sys.stderr is None:
        sys.stderr = handle  # type: ignore[assignment]


def setup_console() -> None:
    """统一 UTF-8 输出与颜色初始化，避免 GBK 控制台编码报错导致闪退。"""
    _ensure_streams()
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (ValueError, OSError):  # pragma: no cover
            pass
    if _colorama_init is not None:
        try:
            _colorama_init(autoreset=True, strip=not _console_open())
        except Exception:  # pragma: no cover
            pass


def emit(text: str = "", end: str = "\n", stream: Optional[TextIO] = None) -> None:
    """按输出流自身的编码写文本。

    Windows 上被重定向到文件、或跑在非中文代码页的机器里时，直接 print 中文会
    抛 UnicodeEncodeError，最外层兜底自己也炸掉就没法给用户看原因了。
    这里编不出来的字符一律降级成占位符，保证提示一定能显示出来。
    """
    target = stream if stream is not None else sys.stdout
    if target is None:
        return
    payload = text + end
    try:
        target.write(payload)
        return
    except UnicodeEncodeError:
        pass
    except (ValueError, OSError):  # pragma: no cover - 流已关闭
        return
    encoding = getattr(target, "encoding", None) or "utf-8"
    try:
        target.write(payload.encode(encoding, "replace").decode(encoding, "replace"))
    except (UnicodeEncodeError, ValueError, OSError):  # pragma: no cover
        pass


def _flush_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        flush = getattr(stream, "flush", None)
        if flush is None:
            continue
        try:
            flush()
        except (ValueError, OSError):  # pragma: no cover
            pass


def pause_if_interactive(force: bool = False) -> None:
    """双击运行时保持窗口，避免"闪退"什么都看不到。

    跳过条件（满足任一）：设置 STEAMFIX_NO_PAUSE；带命令行参数且 force=False；
    stdout 不是终端。判断为交互后优先用 msvcrt 直接读键盘，
    这样即使 stdin 被重定向也能停住。
    """
    if os.environ.get(NO_PAUSE_ENV):
        return
    if not force and len(sys.argv) > 1:
        return
    if not _console_open():
        return

    _flush_streams()
    try:
        if os.name == "nt":  # pragma: no cover - 仅 Windows
            import msvcrt

            emit("请按任意键退出…", end=" ")
            msvcrt.getwch()
            emit()
            return
    except Exception:  # pragma: no cover
        pass
    try:
        input("请按任意键退出…")
    except (EOFError, KeyboardInterrupt):  # pragma: no cover
        pass


# ---------------------------------------------------------------- 失败兜底

def fatal(
    message: str,
    exc: Optional[BaseException] = None,
    hint: Optional[str] = None,
) -> int:
    """打印可读错误 + 落盘详细日志，返回退出码 1。"""
    emit(Fore.RED + message + Style.RESET_ALL)
    if hint:
        emit(hint)
    path = log_exception(exc, title=message)
    if path is not None:
        emit(Fore.YELLOW + "详细日志已保存：" + str(path))
        emit("反馈问题时可把这个文件发给作者。")
    return 1


def missing_dependency(module: str, requirements: str = "requirements.txt", exc: Optional[BaseException] = None) -> int:
    """缺依赖时给出可执行的一步修复提示，而不是一闪而过的 ImportError。"""
    return fatal(
        "缺少依赖：{0}".format(module),
        exc=exc,
        hint="请先安装依赖再运行：\n"
             "    pip install -r {0}\n"
             "如果是直接双击运行，请先确认已安装 Python 3.9+ 并在同目录执行过上面这条命令。".format(requirements),
    )


def run_guarded(func: Callable[[], int], pause: bool = True) -> int:
    """执行主函数，保证任何异常都能被看到、且窗口不会秒关。"""
    code = 1
    try:
        result = func()
        code = int(result or 0)
    except KeyboardInterrupt:
        emit(Fore.YELLOW + "\n已中断")
        code = 130
    except SteamError as exc:
        code = fatal(str(exc), exc=exc)
    except SystemExit:  # 参数解析等显式退出，交给 Python 自己处理
        raise
    except BaseException as exc:  # noqa: B902 - 兜底，宁可多抓也不能闪退
        code = fatal(
            "运行出错：{0}: {1}".format(type(exc).__name__, exc),
            exc=exc,
            hint="这通常不是 Steam 本身的问题。把上面的日志文件发给作者即可定位。",
        )
    finally:
        _flush_streams()
        if pause:
            pause_if_interactive()
    return code
