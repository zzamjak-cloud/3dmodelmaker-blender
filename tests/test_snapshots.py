# 턴 스냅샷 배치 계산·멀티뷰 보관 경로 테스트 (Blender 없이 순수 파이썬으로 실행)
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


snapshots = _load("snapshots_mod", "core/snapshots.py")
multiview = _load("multiview_mod2", "core/multiview.py")


class TestSlotOffset(unittest.TestCase):
    # 최종본이 원점, 이전 단계는 왼쪽으로 — 1턴이 가장 멀리 간다
    def test_earlier_turn_is_further_left(self):
        w = 2.0
        o1 = snapshots.slot_offset(1, 3, w)
        o2 = snapshots.slot_offset(2, 3, w)
        self.assertLess(o1, o2)
        self.assertLess(o2, 0)

    def test_second_to_last_is_one_slot_left(self):
        # 최종본 바로 앞 턴이 원점에서 한 칸 왼쪽 (간격 = 폭 * 1.35)
        self.assertAlmostEqual(snapshots.slot_offset(2, 3, 2.0), -2.7)

    # 마지막 턴은 오프셋이 0 = 최종본 자리다. 그래서 세션은 마지막 턴 스냅샷을
    # 찍지 않는다 (찍으면 최종본과 겹친다).
    def test_last_turn_would_collide_with_origin(self):
        self.assertEqual(snapshots.slot_offset(3, 3, 2.0), 0.0)

    def test_no_positive_offset(self):
        # 최종본 자리(원점)를 침범하지 않아야 한다
        for turn in range(1, 5):
            self.assertLessEqual(snapshots.slot_offset(turn, 4, 1.0), 0.0)

    def test_wider_model_spaces_further(self):
        self.assertLess(snapshots.slot_offset(1, 2, 5.0), snapshots.slot_offset(1, 2, 1.0))


class TestCollectionName(unittest.TestCase):
    def test_name_format(self):
        self.assertEqual(snapshots.collection_name("LP3D_Car", 2), "LP3D_Car_turn2")

    def test_prefix_enables_cleanup_match(self):
        name = snapshots.collection_name("LP3D_Car", 1)
        self.assertTrue(name.startswith("LP3D_Car" + snapshots.SUFFIX))


class TestMultiviewArchive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lp3d_mv_arch_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unique_path_avoids_overwrite(self):
        first = multiview.unique_path(self.tmp, "sheet")
        open(first, "w").close()
        second = multiview.unique_path(self.tmp, "sheet")
        self.assertNotEqual(first, second)
        self.assertTrue(second.endswith("_001.png"))

    def test_slug_strips_path_separators(self):
        slug = multiview._slug("빨간 승용차/../etc")
        self.assertNotIn("/", slug)
        self.assertNotIn("..", slug)
        self.assertIn("빨간", slug)

    def test_slug_fallback(self):
        self.assertEqual(multiview._slug("///"), "model")


class TestArchiveDir(unittest.TestCase):
    # 시트·클립보드 참조 이미지가 매번 다른 곳에 생기지 않도록 한 곳으로 고정한다
    def test_uses_downloads_blender(self):
        d = multiview.archive_dir()
        self.assertTrue(os.path.isdir(d), f"보관 폴더가 만들어지지 않음: {d}")
        self.assertEqual(os.path.basename(d), "blender")

    def test_under_home(self):
        self.assertTrue(multiview.archive_dir().startswith(os.path.expanduser("~")))

    def test_writable(self):
        probe = os.path.join(multiview.archive_dir(), ".lp3d_write_probe")
        with open(probe, "w") as f:
            f.write("ok")
        self.assertTrue(os.path.isfile(probe))
        os.remove(probe)

    def test_does_not_depend_on_bpy(self):
        # .blend 저장 여부로 갈리던 분기를 없앴다 — bpy 없이도 경로가 나와야 한다.
        # (예전 archive_dir()은 bpy를 import해서 Blender 밖에서는 터졌다)
        self.assertTrue(multiview.archive_dir())

    def test_stable_across_calls(self):
        self.assertEqual(multiview.archive_dir(), multiview.archive_dir())


if __name__ == "__main__":
    unittest.main()
