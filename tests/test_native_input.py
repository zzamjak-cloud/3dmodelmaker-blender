# 네이티브 입력 팝업 명령 구성 테스트 (Blender 없이 순수 파이썬으로 실행)
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name: str, relpath: str):
    """리포 내 파일을 패키지 임포트 없이 모듈로 읽어들인다 (core/__init__.py의 bpy 회피)."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, relpath))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


native_input = _load("native_input", "core/native_input.py")


class TestToSingleLine(unittest.TestCase):
    # StringProperty는 단일행 — 개행·연속 공백을 공백 하나로 정규화
    def test_newlines_become_spaces(self):
        self.assertEqual(native_input.to_single_line("낡은 나무 배럴\r\n금속 밴드 2개"),
                         "낡은 나무 배럴 금속 밴드 2개")

    def test_strip_and_collapse(self):
        self.assertEqual(native_input.to_single_line("  a \n\n b  "), "a b")

    def test_empty(self):
        self.assertEqual(native_input.to_single_line(""), "")


class TestBuildCommand(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="lp3d_test_")
        self.init = os.path.join(self.work, "initial.txt")
        self.result = os.path.join(self.work, "result.txt")

    def test_darwin_uses_osascript_with_paths_as_argv(self):
        cmd, flags = native_input.build_command("darwin", self.work, "프롬프트 입력",
                                                self.init, self.result)
        self.assertEqual(cmd[0], "osascript")
        # 경로·제목은 스크립트 문자열에 끼워넣지 않고 argv로 넘긴다 (이스케이프 회피)
        self.assertEqual(cmd[-3:], ["프롬프트 입력", self.init, self.result])
        self.assertEqual(flags, 0)

    def test_win32_writes_ps1_and_hides_console(self):
        cmd, flags = native_input.build_command("win32", self.work, "프롬프트 입력",
                                                self.init, self.result)
        self.assertEqual(cmd[0], "powershell")
        self.assertIn("-File", cmd)
        script_path = cmd[cmd.index("-File") + 1]
        self.assertTrue(os.path.isfile(script_path))
        # PS 5.1 한글 처리를 위해 BOM 포함 UTF-8이어야 한다
        with open(script_path, "rb") as f:
            self.assertEqual(f.read(3), b"\xef\xbb\xbf")
        with open(script_path, encoding="utf-8-sig") as f:
            body = f.read()
        self.assertIn("입력완료", body)
        self.assertEqual(cmd[-3:], ["프롬프트 입력", self.init, self.result])
        self.assertEqual(flags, getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def test_unsupported_platform(self):
        cmd, flags = native_input.build_command("linux", self.work, "t", self.init, self.result)
        self.assertIsNone(cmd)

    def test_dialog_scripts_have_no_backslash(self):
        # 릴리스 검증기의 문자열 백슬래시 제약 + Windows 경로 이스케이프 사고 방지
        self.assertNotIn(chr(92), native_input._APPLESCRIPT)
        self.assertNotIn(chr(92), native_input._POWERSHELL)


if __name__ == "__main__":
    unittest.main()
