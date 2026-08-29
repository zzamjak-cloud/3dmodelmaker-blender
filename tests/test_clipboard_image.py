# 클립보드 이미지 붙여넣기 테스트 (Blender 없이 순수 파이썬으로 실행)
import importlib.util
import os
import shutil
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


clip = _load("clipboard_mod", "core/clipboard_image.py")


class TestSupport(unittest.TestCase):
    def test_supported_platforms(self):
        self.assertTrue(clip.is_supported() == (sys.platform in ("darwin", "win32")))


class TestTargetPath(unittest.TestCase):
    def test_png_under_directory(self):
        p = clip.target_path("/tmp/x")
        self.assertTrue(p.startswith(os.path.join("/tmp/x", "LP3D_ref_clipboard_")))
        self.assertTrue(p.endswith(".png"))


class TestPasteGuards(unittest.TestCase):
    def test_missing_directory_is_reported(self):
        path, err = clip.paste_to("/definitely/not/a/real/dir")
        self.assertIsNone(path)
        self.assertIn("폴더", err)

    def test_no_image_returns_message_not_file(self):
        # 실제 클립보드 상태에 의존하지 않도록 명령 실행을 가로챈다
        tmp = tempfile.mkdtemp(prefix="lp3d_clip_test_")
        original = clip._run
        clip._run = lambda *a, **k: "NOIMAGE"
        try:
            path, err = clip.paste_to(tmp)
            self.assertIsNone(path)
            self.assertIn("이미지가 없습니다", err)
            self.assertEqual(os.listdir(tmp), [])  # 찌꺼기 파일을 남기지 않는다
        finally:
            clip._run = original
            shutil.rmtree(tmp, ignore_errors=True)


class TestScripts(unittest.TestCase):
    # 릴리스 검증기의 문자열 백슬래시 제약 + 경로 이스케이프 사고 방지
    def test_no_backslash_in_scripts(self):
        self.assertNotIn(chr(92), clip._APPLESCRIPT)
        self.assertNotIn(chr(92), clip._POWERSHELL)

    def test_powershell_requires_sta(self):
        # Clipboard.GetImage는 STA 스레드에서만 동작한다
        self.assertIn("GetImage", clip._POWERSHELL)


if __name__ == "__main__":
    unittest.main()
