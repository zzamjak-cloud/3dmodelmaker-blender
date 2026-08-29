# 생성 라이브러리(축적·유사도·few-shot) 테스트 (Blender 없이 순수 파이썬으로 실행)
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


library = _load("library_mod", "core/library.py")
prompts = _load("prompts_mod3", "core/prompts.py")


class LibraryTestCase(unittest.TestCase):
    """root_dir을 임시 폴더로 바꿔 실제 Blender config를 건드리지 않는다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lp3d_lib_test_")
        library.root_dir = lambda: self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestSaveAndRead(LibraryTestCase):
    def test_save_and_read_back(self):
        eid = library.save_entry("낡은 나무 배럴", "code_body", stats={"tris": 420}, agent="claude")
        self.assertTrue(eid)
        entries = library.load_index()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["request"], "낡은 나무 배럴")
        self.assertEqual(entries[0]["tris"], 420)
        self.assertEqual(entries[0]["rating"], 0)
        self.assertEqual(library.read_code(eid), "code_body")

    def test_empty_request_or_code_rejected(self):
        self.assertEqual(library.save_entry("", "code"), "")
        self.assertEqual(library.save_entry("배럴", ""), "")
        self.assertEqual(library.load_index(), [])

    def test_rating_and_delete(self):
        eid = library.save_entry("나무 상자", "code")
        self.assertTrue(library.set_rating(eid, 2))
        self.assertEqual(library.load_index()[0]["rating"], 2)
        self.assertFalse(library.set_rating("없는id", 2))
        self.assertTrue(library.delete_entry(eid))
        self.assertEqual(library.load_index(), [])
        self.assertFalse(library.delete_entry(eid))

    def test_missing_index_is_empty(self):
        self.assertEqual(library.load_index(), [])


class TestSimilarity(LibraryTestCase):
    def test_identical_and_disjoint(self):
        self.assertEqual(library.similarity("빨간 승용차", "빨간 승용차"), 1.0)
        self.assertEqual(library.similarity("승용차", "배럴"), 0.0)

    def test_partial_overlap(self):
        score = library.similarity("빨간 승용차", "파란 승용차")
        self.assertGreater(score, 0.0)
        self.assertLess(score, 1.0)

    # 흔한 말만 겹치는 요청은 유사하다고 보면 안 된다
    def test_stopwords_ignored(self):
        self.assertEqual(library.similarity("배럴 모델 만들어줘", "교회 모델 만들어줘"), 0.0)

    def test_find_similar_filters_low_score(self):
        library.save_entry("빨간 승용차", "car_code")
        library.save_entry("중세 교회 건물", "church_code")
        found = library.find_similar("파란 승용차", limit=2)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["request"], "빨간 승용차")

    def test_rating_breaks_tie(self):
        low = library.save_entry("빨간 승용차", "low_code")
        high = library.save_entry("빨간 승용차", "high_code")
        library.set_rating(high, 2)
        found = library.find_similar("빨간 승용차", limit=1)
        self.assertEqual(found[0]["id"], high)
        self.assertNotEqual(found[0]["id"], low)


class TestFewshot(LibraryTestCase):
    def test_fewshot_examples(self):
        library.save_entry("빨간 승용차", "car_code")
        ex = library.fewshot_examples("파란 승용차", limit=2)
        self.assertEqual(ex, [("빨간 승용차", "car_code")])

    def test_oversized_code_excluded(self):
        library.save_entry("빨간 승용차", "x" * (library.MAX_FEWSHOT_CHARS + 1))
        self.assertEqual(library.fewshot_examples("빨간 승용차"), [])

    def test_prompt_injection(self):
        p = prompts.build_initial_prompt("파란 승용차", fewshot=[("빨간 승용차", "car_code")])
        self.assertIn("car_code", p)
        self.assertIn("빨간 승용차", p)
        # 베끼기 방지 문구가 반드시 함께 들어가야 한다
        self.assertIn("그대로 베끼지", p)

    def test_no_fewshot_no_section(self):
        self.assertNotIn("합격한 유사 모델", prompts.build_initial_prompt("파란 승용차"))


if __name__ == "__main__":
    unittest.main()
