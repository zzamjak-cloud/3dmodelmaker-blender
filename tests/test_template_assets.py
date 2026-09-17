# 내장 베이스 메시 템플릿 자산 테스트 (Blender 없이 순수 파이썬으로 실행)
#
# core/templates.py는 bpy를 import해 여기서 직접 읽을 수 없다. 대신 배포 파일 자체를 검사한다:
# 4종 OBJ가 있고, 전부 쿼드이며, 출처 고지가 함께 있는지. 하나라도 빠지면 애드온이
# 템플릿 목록에는 보이는데 불러오기에서 실패하는 상태가 된다.
import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIR = os.path.join(_ROOT, "lowpoly", "templates")
_EXPECTED = ("humanoid_male_stylized.obj", "humanoid_female_stylized.obj",
             "humanoid_male_realistic.obj", "humanoid_female_realistic.obj")


def _face_stats(path):
    faces = quads = 0
    verts = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("v "):
                verts += 1
            elif line.startswith("f "):
                faces += 1
                if len(line.split()) - 1 == 4:
                    quads += 1
    return verts, faces, quads


class TestTemplateFiles(unittest.TestCase):
    def test_all_builtin_files_exist(self):
        for name in _EXPECTED:
            self.assertTrue(os.path.isfile(os.path.join(_DIR, name)), name)

    def test_templates_are_quad_meshes(self):
        # 리토폴로지 템플릿의 존재 이유가 쿼드 흐름이다 — 삼각형이 섞이면(glTF 저장 등) 실패
        for name in _EXPECTED:
            verts, faces, quads = _face_stats(os.path.join(_DIR, name))
            self.assertGreater(faces, 5000, name)
            self.assertGreaterEqual(quads / faces, 0.99, "%s: quads %d / %d" % (name, quads, faces))

    def test_notice_credits_cc0_source(self):
        with open(os.path.join(_DIR, "NOTICE.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("CC0", text)
        self.assertIn("Human Base Meshes", text)

    def test_templates_module_registers_every_file(self):
        with open(os.path.join(_ROOT, "core", "templates.py"), encoding="utf-8") as f:
            src = f.read()
        for name in _EXPECTED:
            self.assertIn(name, src)
        self.assertIn("HUMANOID_MALE_STYLIZED", src)
        self.assertIn("def resolve_template", src)

    def test_manifest_does_not_exclude_templates(self):
        with open(os.path.join(_ROOT, "blender_manifest.toml"), encoding="utf-8") as f:
            manifest = f.read()
        self.assertNotRegex(manifest, r'"/?lowpoly/templates')
        self.assertNotIn('"*.obj"', manifest)


if __name__ == "__main__":
    unittest.main()
