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


class TestSceneAssetNames(unittest.TestCase):
    def test_decimal_dimensions_do_not_end_the_clause(self):
        # 실제 사고: 배경 에셋이 전부 "읽히게_단순하게_정리하라"(요청문 끝 지시문)로 이름 붙었다
        request = ("폭 0.48m, 깊이 0.36m, 높이 0.3m의 어촌 나무 상자\n색: 갈색\n"
                   "배경에 여러 개가 반복 배치될 프랍이다 — 실루엣이 멀리서도 읽히게 단순하게 정리하라.")
        self.assertEqual(naming.result_name(request), "어촌_나무_상자")

    def test_dimension_first_clause_skipped(self):
        self.assertEqual(naming.result_name("높이 0.4m의 통통한 어업용 부표. 배가 부른 몸통"), "통통한_어업용_부표")
