# 멀티뷰 참조 시트 생성 명령·프롬프트 구성 테스트 (Blender 없이 순수 파이썬으로 실행)
import importlib.util
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name: str, relpath: str):
    """리포 내 파일을 패키지 임포트 없이 모듈로 읽어들인다 (core/__init__.py의 bpy 회피)."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, relpath))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


multiview = _load("multiview_mod", "core/multiview.py")
prompts = _load("prompts_mod2", "core/prompts.py")


class TestBuildCommand(unittest.TestCase):
    def test_basic_command(self):
        cmd = multiview.build_command("/usr/bin/codex", "/tmp/mv")
        self.assertEqual(cmd[0], "/usr/bin/codex")
        self.assertEqual(cmd[1], "exec")
        # 이미지 저장을 위해 쓰기 샌드박스여야 한다
        self.assertIn("workspace-write", cmd)
        self.assertIn("--skip-git-repo-check", cmd)
        self.assertEqual(cmd[-1], "-")  # 프롬프트는 stdin

    def test_ref_image_attached_before_stdin_marker(self):
        cmd = multiview.build_command("codex", "/tmp/mv", ref_image="/tmp/ref.png")
        self.assertIn("-i", cmd)
        self.assertLess(cmd.index("-i"), cmd.index("-"))
        self.assertEqual(cmd[cmd.index("-i") + 1], "/tmp/ref.png")


class TestBuildPrompt(unittest.TestCase):
    def test_contains_filename_and_views(self):
        p = multiview.build_prompt("빨간 승용차")
        self.assertIn(multiview.MULTIVIEW_FILENAME, p)
        for view in ("정면", "측면", "상면", "3/4"):
            self.assertIn(view, p)
        self.assertNotIn("참조 이미지", p)

    def test_ref_note_when_has_ref(self):
        p = multiview.build_prompt("빨간 승용차", has_ref=True)
        self.assertIn("참조 이미지", p)


class TestPromptIntegration(unittest.TestCase):
    def test_initial_prompt_with_multiview(self):
        p = prompts.build_initial_prompt("승용차", multiview="multiview.png")
        self.assertIn("multiview.png", p)
        self.assertIn("멀티뷰", p)

    def test_initial_prompt_combines_reference_and_multiview(self):
        p = prompts.build_initial_prompt("승용차", ref_image="reference.png",
                                         multiview="multiview.png")
        self.assertIn("multiview.png", p)
        self.assertIn("reference.png", p)

    def test_no_multiview_no_note(self):
        p = prompts.build_initial_prompt("승용차")
        self.assertNotIn("멀티뷰", p)


if __name__ == "__main__":
    unittest.main()


class TestLatestArchived(unittest.TestCase):
    """보관 폴더에서 가장 최근 시트를 되찾는 조회 (Blender 상태 없이 파일만으로)."""

    def setUp(self):
        import tempfile
        self.dir_a = tempfile.mkdtemp(prefix="lp3d_test_a_")
        self.dir_b = tempfile.mkdtemp(prefix="lp3d_test_b_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir_a, ignore_errors=True)
        shutil.rmtree(self.dir_b, ignore_errors=True)

    def _write(self, directory, name, mtime):
        path = os.path.join(directory, name)
        with open(path, "wb") as f:
            f.write(b"x")
        os.utime(path, (mtime, mtime))
        return path

    def test_empty_dirs_return_blank(self):
        self.assertEqual(multiview.latest_in([self.dir_a, self.dir_b]), "")

    def test_picks_newest_across_directories(self):
        self._write(self.dir_a, "LP3D_multiview_old.png", 1000)
        newest = self._write(self.dir_b, "LP3D_multiview_new.png", 2000)
        self.assertEqual(multiview.latest_in([self.dir_a, self.dir_b]), newest)

    def test_ignores_unrelated_files(self):
        self._write(self.dir_a, "LP3D_ref_clipboard_20260831.png", 9000)  # 참조 붙여넣기 결과
        self._write(self.dir_a, "notes.txt", 9000)
        sheet = self._write(self.dir_a, "LP3D_multiview_box.png", 100)
        self.assertEqual(multiview.latest_in([self.dir_a]), sheet)

    def test_missing_directory_is_skipped(self):
        sheet = self._write(self.dir_a, "LP3D_multiview_box.png", 100)
        self.assertEqual(multiview.latest_in(["/definitely/not/here", self.dir_a, ""]), sheet)

    def test_archive_uses_same_prefix_as_lookup(self):
        # archive()가 쓰는 파일명과 latest_in()이 찾는 접두어가 어긋나면 조용히 실패한다
        self.assertTrue(
            multiview.unique_path(self.dir_a, multiview.ARCHIVE_PREFIX + "x")
            .endswith(multiview.ARCHIVE_PREFIX + "x.png"))


class TestGenerateSignature(unittest.TestCase):
    """generate()는 runner를 통해 codex를 띄우므로 Blender 없이는 실행할 수 없다.
    대신 잡 단위 취소에 필요한 job_key 인자가 유지되는지만 확인한다 —
    이게 빠지면 세션을 취소해도 codex 프로세스가 살아남는다."""

    def test_accepts_job_key(self):
        import inspect

        params = inspect.signature(multiview.generate).parameters
        self.assertIn("job_key", params)
        self.assertIsNone(params["job_key"].default)
