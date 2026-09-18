# 매핑 단계 분리: 컨텍스트 저장/복원과 부품 기록 재구성 (bpy 불필요)
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


# texture_stage 는 상대 import 로 character_parts 를 쓴다 — 패키지 흉내를 내서 로드한다
pkg = types.ModuleType("lp3dcore"); pkg.__path__ = [str(ROOT / "core")]
sys.modules["lp3dcore"] = pkg
parts = _load("lp3dcore.character_parts", "core/character_parts.py")
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


class PartRecordTests(unittest.TestCase):
    def test_records_group_objects_by_part_and_keep_one_sheet(self):
        objs = [("Body", {"lp3d_character_part": "BODY", "lp3d_part_sheet": "/s/body.png"}),
                ("Body.001", {"lp3d_character_part": "BODY"}),          # 사용자가 분할한 조각
                ("Bag", {"lp3d_character_part": "OUTFIT", "lp3d_part_sheet": "/s/acc.png"}),
                ("Untagged", {})]
        rec = ts.part_records_from_objects(objs)
        self.assertEqual(set(rec), {"BODY", "OUTFIT"})
        self.assertEqual(rec["BODY"]["objects"], ["Body", "Body.001"])
        self.assertEqual(rec["BODY"]["sheet"], "/s/body.png")

    def test_untagged_collection_is_unified(self):
        self.assertEqual(ts.part_records_from_objects([("Model", {})]), {})

    def test_texture_order_follows_parts_and_defers_sheetless(self):
        rec = {"WEAPON": {"sheet": "/w.png"}, "BODY": {"sheet": None}, "OUTFIT": {"sheet": "/a.png"}}
        self.assertEqual(ts.texture_order(rec), ["OUTFIT", "WEAPON", "BODY"])


class PartsCatalogTests(unittest.TestCase):
    def test_three_parts_in_generation_order(self):
        self.assertEqual(parts.PARTS, ("BODY", "OUTFIT", "WEAPON"))
        self.assertEqual(parts.LABELS["OUTFIT"], "의상·악세사리")
        self.assertEqual(parts.LABELS["WEAPON"], "무기·방어구")

    def test_outfit_prompt_includes_accessories(self):
        p = parts.part_prompt("기사", "OUTFIT")
        for word in ("배낭", "벨트", "장신구"):
            self.assertIn(word, p)

    def test_weapon_prompt_includes_armor(self):
        p = parts.part_prompt("기사", "WEAPON")
        self.assertIn("방어구", p); self.assertIn("갑옷", p)

    def test_outfit_prompt_keeps_clothed_body_and_removes_weapons(self):
        # 속이 빈 옷은 셰이프 모델이 못 만든다 — 옷 입은 전신으로 요청하고 몸체는 나중에 뺀다
        p = parts.part_prompt("기사", "OUTFIT")
        self.assertIn("무기·방어구", p); self.assertIn("몸체·머리카락", p); self.assertIn("입힌 전신", p)


class PresenceCheckTests(unittest.TestCase):
    def test_parse_presence(self):
        self.assertTrue(parts.parse_presence("YES"))
        self.assertFalse(parts.parse_presence("No."))
        self.assertIsNone(parts.parse_presence("")); self.assertIsNone(parts.parse_presence("yes or no"))
        self.assertFalse(parts.parse_presence("판단: NO\n"))

    def test_presence_prompt_and_command(self):
        p = parts.presence_prompt("기사", "WEAPON")
        self.assertIn("방어구", p); self.assertIn("YES 또는 NO", p)
        cmd = parts.presence_command("codex", "/w", "/w/orig.png")
        self.assertEqual(cmd[-1], "-"); self.assertIn("read-only", cmd); self.assertEqual(cmd[cmd.index("-i") + 1], "/w/orig.png")


if __name__ == "__main__":
    unittest.main()
