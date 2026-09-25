# 결과 이름 — 한국어 프롬프트가 전부 LP3D_Model이 되던 문제
import importlib.util
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("naming", os.path.join(_ROOT, "core", "naming.py"))
naming = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(naming)


class TestResultName(unittest.TestCase):
    def test_korean_prompt_keeps_korean(self):
        self.assertEqual(naming.result_name("아늑한 거실"), "아늑한_거실")

    def test_measurements_are_dropped_and_head_noun_kept(self):
        self.assertEqual(naming.result_name("폭 4.8m 깊이 1.9m의 낮고 넓은 주황색 삼인용 소파"), "주황색_삼인용_소파")

    def test_reference_phrases_are_dropped(self):
        name = naming.result_name("참조 이미지처럼 숲속 캠핑장")
        self.assertEqual(name, "숲속_캠핑장")

    def test_first_clause_only(self):
        self.assertEqual(naming.result_name("해안 등대, 주변에 어부 집과 보트"), "해안_등대")

    def test_long_name_drops_leading_words(self):
        name = naming.result_name("아주아주아주긴수식어 또다른긴긴수식어 레트로주유소")
        self.assertLessEqual(len(name), naming.MAX_CHARS)
        self.assertTrue(name.endswith("레트로주유소"))

    def test_english_prompt(self):
        self.assertEqual(naming.result_name("retro gas station"), "retro_gas_station")

    def test_nothing_usable_falls_back(self):
        self.assertEqual(naming.result_name("12m 3.6m"), naming.FALLBACK)
        self.assertEqual(naming.result_name(""), naming.FALLBACK)

    def test_no_path_separators(self):
        self.assertNotIn("/", naming.result_name("빨간 승용차/../etc"))


if __name__ == "__main__":
    unittest.main()
