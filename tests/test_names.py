# Blender ID 이름 안전화(safe_id_name) 유닛 테스트 — bpy 무의존
import importlib.util
import os
import unittest

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lowpoly", "names.py")
_spec = importlib.util.spec_from_file_location("lp3d_names", _PATH)
names = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(names)


class SafeIdNameTest(unittest.TestCase):
    def test_float_suffix_is_defused(self):
        # 실제 크래시를 일으킨 이름: 점 뒤 16자리 숫자
        self.assertEqual(names.safe_id_name("DiagonalBrace_0.7853981633974483"),
                         "DiagonalBrace_0_7853981633974483")

    def test_small_numeric_suffix_kept(self):
        self.assertEqual(names.safe_id_name("Post.001"), "Post.001")
        self.assertEqual(names.safe_id_name("Rail_0.35"), "Rail_0.35")

    def test_int32_boundary(self):
        self.assertEqual(names.safe_id_name("A.2147483647"), "A.2147483647")
        self.assertEqual(names.safe_id_name("A.2147483648"), "A_2147483648")

    def test_plain_and_empty(self):
        self.assertEqual(names.safe_id_name("Barrel"), "Barrel")
        self.assertEqual(names.safe_id_name(""), "Object")
        self.assertEqual(names.safe_id_name(None, "Joined"), "Joined")
        self.assertEqual(names.safe_id_name("x.abc123"), "x.abc123")


if __name__ == "__main__":
    unittest.main()
