# 매핑 단계 분리: 컨텍스트 저장/복원 (bpy 불필요)
import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ts = _load("lp3dcore.texture_stage", "core/texture_stage.py")


class ContextRoundTripTests(unittest.TestCase):
    def test_save_and_load_round_trip(self):
        store = {}
        ts.save_context(store, "판타지 소년", "/a/sheet.png", "LOWPOLY", "CHARACTER", "HUMANOID", "11,443 tris")
        ctx = ts.load_context(store)
        self.assertEqual(ctx["request"], "판타지 소년")
        self.assertEqual(ctx["multiview"], "/a/sheet.png")
        self.assertEqual(ctx["system_mode"], "CHARACTER")
        self.assertEqual(ctx["character_type"], "HUMANOID")
        self.assertEqual(ctx["final_note"], "11,443 tris")

    def test_none_values_become_empty_strings_and_load_back_as_none(self):
        store = {}
        ts.save_context(store, "x", None, None, None, None)
        self.assertEqual(store["lp3d_multiview"], "")          # ID 프로퍼티는 None 불가
        ctx = ts.load_context(store)
        self.assertIsNone(ctx["multiview"]); self.assertIsNone(ctx["style"])
        self.assertEqual(ctx["system_mode"], "OBJECT")

    def test_missing_request_is_an_error(self):
        with self.assertRaises(ValueError):
            ts.load_context({})


if __name__ == "__main__":
    unittest.main()
