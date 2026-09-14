# 이전 스냅샷 정리·레인 배치·멀티뷰 보관 경로 테스트
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

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
lanes = _load("lanes_mod", "core/lanes.py")


class TestLaneLayout(unittest.TestCase):
    """레인 배치 산술. bpy 호출에 붙어 있어 테스트가 못 보던 계산을 여기서 고정한다."""

    def test_new_result_uses_lane_offset(self):
        for lane in range(4):
            self.assertEqual(lanes.lane_shift(None, lane), lanes.lane_dy(lane))

    def test_lane_zero_is_noop(self):
        self.assertEqual(lanes.lane_dy(0), 0.0)
        self.assertEqual(lanes.lane_shift(None, 0), 0.0)
        self.assertEqual(lanes.lane_shift(0, 0), 0.0)

    def test_repeated_finalize_does_not_double(self):
        # 이미 배치된 오브젝트를 다시 처리해도 이동량이 누적되지 않는다.
        for lane in range(1, 4):
            self.assertEqual(lanes.lane_shift(lane, lane), 0.0)
            # 몇 번을 돌려도 누적 이동량은 첫 이동 그대로다
            total = lanes.lane_shift(None, lane)
            for _ in range(5):
                total += lanes.lane_shift(lane, lane)
            self.assertEqual(total, lanes.lane_dy(lane))

    def test_lane_change_moves_by_difference(self):
        # 레인이 바뀌면 차분만 움직여 새 레인에 정확히 안착한다
        self.assertEqual(lanes.lane_shift(1, 3), lanes.lane_dy(3) - lanes.lane_dy(1))

    def test_lanes_do_not_overlap(self):
        offsets = [lanes.lane_dy(lane) for lane in range(4)]
        self.assertEqual(len(set(offsets)), len(offsets))

    def test_negative_lane_is_clamped(self):
        # 레인은 음수가 될 수 없다 — 방어적으로 원점 뒤로 밀지 않는다
        self.assertEqual(lanes.lane_dy(-1), 0.0)


class TestLegacySnapshotCleanup(unittest.TestCase):
    def test_clear_only_matching_legacy_snapshots(self):
        collections = [SimpleNamespace(name=name) for name in (
            "LP3D_Car", "LP3D_Car_turn1", "LP3D_Car_turn2", "LP3D_House_turn1")]
        bpy = SimpleNamespace(data=SimpleNamespace(collections=collections))
        with (patch.dict(sys.modules, {"bpy": bpy}),
              patch.object(snapshots, "remove_collection") as remove):
            self.assertEqual(snapshots.clear_all("LP3D_Car"), 2)
        self.assertEqual([call.args[0] for call in remove.call_args_list],
                         ["LP3D_Car_turn1", "LP3D_Car_turn2"])


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


class TestLaneSpacing(unittest.TestCase):
    """레인 간격 파라미터화. 배경 공간은 결과가 수십 미터라 기본 4m로는 겹친다."""

    def test_default_spacing_unchanged(self):
        for lane in range(4):
            self.assertEqual(lanes.lane_dy(lane), lanes.LANE_SPACING * lane)

    def test_custom_spacing_scales_offset(self):
        self.assertEqual(lanes.lane_dy(3, 20.0), 60.0)
        self.assertEqual(lanes.lane_dy(0, 20.0), 0.0)

    def test_custom_spacing_clamps_negative_lane(self):
        self.assertEqual(lanes.lane_dy(-2, 20.0), 0.0)

    def test_shift_uses_same_spacing_for_both_ends(self):
        self.assertEqual(lanes.lane_shift(1, 3, 20.0), 40.0)
        self.assertEqual(lanes.lane_shift(3, 3, 20.0), 0.0)

    def test_shift_default_spacing_unchanged(self):
        self.assertEqual(lanes.lane_shift(1, 3), lanes.lane_shift(1, 3, lanes.LANE_SPACING))


if __name__ == "__main__":
    unittest.main()
