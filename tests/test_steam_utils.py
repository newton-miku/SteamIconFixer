# -*- coding: utf-8 -*-
"""steam_utils 的离线单元测试（不访问网络、不依赖 Steam 安装）。"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import steam_utils as su


SAMPLE_VDF = """"libraryfolders"
{
	"0"
	{
		"path"		"C:\\\\Program Files (x86)\\\\Steam"
		"label"		""
		"apps"
		{
			"228980"		"123"
			"730"		"456"
			"480"		"789"
		}
	}
	"1"
	{
		"path"		"D:\\\\SteamLibrary"
		"apps"
		{
			"730"		"456"
			"620"		"321"
		}
	}
}
"""


class IconNameTests(unittest.TestCase):
    def test_accepts_steam_hash(self):
        common = {"clienticon": "324B323045B09BACE182F928F4104DFCD93CB7F3"}
        self.assertEqual(su.icon_name(common), "324b323045b09bace182f928f4104dfcd93cb7f3")

    def test_missing_clienticon(self):
        self.assertIsNone(su.icon_name({"name": "Spacewar", "icon": "abc"}))
        self.assertIsNone(su.icon_name({"clienticon": None}))
        self.assertIsNone(su.icon_name(None))
        self.assertIsNone(su.icon_name({"clienticon": ""}))

    def test_rejects_path_traversal(self):
        self.assertIsNone(su.icon_name({"clienticon": "../../evil"}))
        self.assertIsNone(su.icon_name({"clienticon": "a" * 20 + "/x"}))


class DisplayNameTests(unittest.TestCase):
    def test_prefers_schinese(self):
        common = {"name": "Fallback", "name_localized": {"english": "English", "schinese": "中文名"}}
        self.assertEqual(su.display_name(common), "中文名")

    def test_falls_back_to_name(self):
        self.assertEqual(su.display_name({"name": "  Portal 2  "}), "Portal 2")
        self.assertEqual(su.display_name({}), "")
        self.assertEqual(su.display_name(None), "")


class ValidIconTests(unittest.TestCase):
    def test_ico(self):
        self.assertTrue(su.is_valid_icon(b"\x00\x00\x01\x00" + b"\x00" * 64))

    def test_png(self):
        self.assertTrue(su.is_valid_icon(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64))

    def test_error_page_is_rejected(self):
        page = b"<!DOCTYPE html><html><head><title>Access denied</title></head></html>"
        self.assertFalse(su.is_valid_icon(page))
        self.assertFalse(su.is_valid_icon(b""))
        self.assertFalse(su.is_valid_icon(None))
        self.assertFalse(su.is_valid_icon(b"\x00\x00\x01\x00"))


class LibraryTests(unittest.TestCase):
    def test_collect_app_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            vdf_path = Path(tmp) / "libraryfolders.vdf"
            vdf_path.write_text(SAMPLE_VDF, encoding="utf-8")
            app_ids = su.collect_app_ids(vdf_path)
        self.assertEqual(app_ids, [480, 620, 730])

    def test_missing_library_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(su.SteamLibraryError):
                su.find_library_folders(Path(tmp))

    def test_find_library_folders_prefers_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            (root / "config" / "libraryfolders.vdf").write_text(SAMPLE_VDF, encoding="utf-8")
            self.assertEqual(su.find_library_folders(root), root / "config" / "libraryfolders.vdf")


class WriteTests(unittest.TestCase):
    def test_atomic_write_and_no_leftover(self):
        data = b"\x00\x00\x01\x00" + b"\xff" * 32
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "abc.ico"
            su.atomic_write(target, data)
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(list(Path(tmp).glob("*.part")), [])

    def test_ensure_icon_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            created = su.ensure_icon_dir(Path(tmp))
            self.assertTrue(created.is_dir())
            self.assertEqual(created.name, "games")
            self.assertEqual(created.parent.name, "steam")


class UrlTests(unittest.TestCase):
    def test_icon_urls(self):
        urls = su.icon_urls(730, "abc123")
        self.assertEqual(len(urls), 2)
        self.assertIn("/apps/730/abc123.ico", urls[0])
        self.assertTrue(all(u.startswith("https://") for u in urls))

    def test_info_endpoints_are_templates(self):
        for template in su.INFO_ENDPOINTS:
            self.assertIn("{appid}", template)
            self.assertTrue(template.format(appid=730).endswith("/v1/info/730"))

    def test_endpoint_count(self):
        # 入口数量保持精简，多一个不通的入口就会让每个 appid 白等一次超时
        self.assertEqual(len(su.INFO_ENDPOINTS), 3)


class ColorTests(unittest.TestCase):
    def test_fore_and_style_never_raise(self):
        # 无论 colorama 装没装，拼接颜色码都不能炸
        for piece in (su.Fore.RED, su.Fore.GREEN, su.Style.RESET_ALL):
            self.assertIsInstance(piece, str)
        self.assertIsInstance(su._NoColor().WHATEVER, str)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old = os.environ.get(su.LOG_DIR_ENV)
        os.environ[su.LOG_DIR_ENV] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop(su.LOG_DIR_ENV, None)
        else:
            os.environ[su.LOG_DIR_ENV] = self._old

    def test_log_dir_honours_override(self):
        self.assertEqual(su.log_dir(), Path(self._tmp.name))

    def test_log_dir_defaults_to_program_dir(self):
        os.environ.pop(su.LOG_DIR_ENV, None)
        self.assertEqual(su.log_dir(), su.program_dir())
        self.assertEqual(su.program_dir(), Path(su.__file__).resolve().parent)

    def test_log_exception_writes_crash_files(self):
        path = su.log_exception(ValueError("boom"), "单元测试")
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).is_file())
        text = Path(path).read_text(encoding="utf-8")
        self.assertIn("单元测试", text)
        self.assertIn("ValueError", text)
        self.assertIn("Python:", text)

    def test_no_log_until_something_goes_wrong(self):
        # 正常跑完不该在程序目录里留下任何文件
        self.assertEqual(list(Path(self._tmp.name).glob("*")), [])
        su.run_guarded(lambda: 0, pause=False)
        self.assertEqual(list(Path(self._tmp.name).glob("*")), [])

    def test_system_info_is_plain_text(self):
        lines = su.system_info()
        self.assertTrue(lines)
        self.assertTrue(all(isinstance(line, str) for line in lines))

    def test_detect_proxy_returns_mapping(self):
        self.assertIsInstance(su.detect_proxy(), dict)
        self.assertIsInstance(su.proxy_hints(), list)


class GuardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old_dir = os.environ.get(su.LOG_DIR_ENV)
        os.environ[su.LOG_DIR_ENV] = self._tmp.name
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old_dir is None:
            os.environ.pop(su.LOG_DIR_ENV, None)
        else:
            os.environ[su.LOG_DIR_ENV] = self._old_dir

    def test_passes_exit_code_through(self):
        self.assertEqual(su.run_guarded(lambda: 0, pause=False), 0)
        self.assertEqual(su.run_guarded(lambda: 1, pause=False), 1)
        self.assertEqual(su.run_guarded(lambda: None, pause=False), 0)

    def test_unexpected_exception_becomes_exit_code_1(self):
        def boom():
            raise RuntimeError("boom")

        self.assertEqual(su.run_guarded(boom, pause=False), 1)
        self.assertTrue(list(Path(self._tmp.name).glob("crash-*.log")))

    def test_keyboard_interrupt_becomes_130(self):
        def interrupted():
            raise KeyboardInterrupt

        self.assertEqual(su.run_guarded(interrupted, pause=False), 130)

    def test_steam_error_is_reported(self):
        def fail():
            raise su.SteamLibraryError("没有库")

        self.assertEqual(su.run_guarded(fail, pause=False), 1)

    def test_pause_skipped_when_env_set(self):
        os.environ[su.NO_PAUSE_ENV] = "1"
        self.addCleanup(os.environ.pop, su.NO_PAUSE_ENV, None)
        su.pause_if_interactive(force=True)  # 不该阻塞，也不该抛异常

    def _narrow_stdout(self):
        """把 stdout 换成只认 cp1252 的流，模拟非中文代码页/输出被重定向。"""
        buf = io.BytesIO()
        stream = io.TextIOWrapper(buf, encoding="cp1252", errors="strict", line_buffering=True)
        patcher = mock.patch.object(su.sys, "stdout", stream)
        patcher.start()
        self.addCleanup(stream.detach)
        self.addCleanup(patcher.stop)
        return buf

    def test_emit_survives_narrow_encoding(self):
        buf = self._narrow_stdout()
        su.emit("缺少依赖：vdf")  # 不能抛 UnicodeEncodeError
        su.sys.stdout.flush()
        self.assertTrue(buf.getvalue())

    def test_fatal_survives_narrow_stream(self):
        self._narrow_stdout()
        self.assertEqual(su.fatal("无法获取到 Steam 安装路径"), 1)
        self.assertTrue(list(Path(self._tmp.name).glob("crash-*.log")))


if __name__ == "__main__":
    unittest.main()
