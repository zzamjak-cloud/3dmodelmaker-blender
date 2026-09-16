# OpenRouter 이미지 백엔드 테스트 (Blender 없이 순수 파이썬으로 실행)
#
# 핵심은 "모델이 지원하지 않는 파라미터를 보내지 않는다"는 것이다 — 보내면 400이 나고,
# 그 실패는 생성 도중에야 드러나서 원인을 찾기 어렵다.
import importlib.util
import json
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, relpath))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


imagegen = _load("imagegen_mod", "core/imagegen.py")


class TestCatalog(unittest.TestCase):
    def test_default_model_is_in_catalog(self):
        self.assertIn(imagegen.DEFAULT_MODEL, [m["id"] for m in imagegen.MODELS])

    def test_default_is_ducttape(self):
        # 참조 시트는 격자 레이아웃 준수가 품질보다 중요하다 — 기본값이 흔들리면
        # 3면도/6면도 칸 배치가 깨져 베이크 좌표가 어긋난다
        self.assertEqual(imagegen.DEFAULT_MODEL, "openai/gpt-image-2")

    def test_enum_items_are_triples(self):
        items = imagegen.enum_items()
        self.assertTrue(items)
        for item in items:
            self.assertEqual(len(item), 3)
            self.assertTrue(all(isinstance(part, str) for part in item))

    def test_unknown_model_falls_back_to_default(self):
        # 구버전 설정 JSON에 남은 모델 id가 드롭다운에서 사라져도 생성이 죽으면 안 된다
        self.assertEqual(imagegen.model_def("openai/gone")["id"], imagegen.DEFAULT_MODEL)


class TestBuildBody(unittest.TestCase):
    def test_gpt_model_gets_quality_not_resolution(self):
        body = imagegen.build_body("x", "openai/gpt-image-2", quality="high", resolution="4K")
        self.assertEqual(body["quality"], "high")
        self.assertNotIn("resolution", body)  # gpt-image 계열에 없는 파라미터

    def test_gemini_model_gets_resolution_not_quality(self):
        body = imagegen.build_body("x", "google/gemini-3-pro-image-preview",
                                   quality="high", resolution="2K")
        self.assertEqual(body["resolution"], "2K")
        self.assertNotIn("quality", body)  # Gemini 계열에 없는 파라미터

    def test_unsupported_quality_is_clamped(self):
        # 2.5 전용 max를 덕테이프(gpt-image-2)에 그대로 보내면 400이 난다
        body = imagegen.build_body("x", "openai/gpt-image-2", quality="max")
        self.assertEqual(body["quality"], "high")

    def test_unsupported_resolution_is_clamped(self):
        body = imagegen.build_body("x", "google/gemini-3.1-flash-lite-image", resolution="4K")
        self.assertEqual(body["resolution"], "1K")

    def test_unsupported_ratio_is_dropped(self):
        body = imagegen.build_body("x", "openai/gpt-image-2", aspect_ratio="4:5")
        self.assertNotIn("aspect_ratio", body)  # gpt-image 계열은 4:5를 지원하지 않는다

    def test_supported_ratio_is_kept(self):
        body = imagegen.build_body("x", "openai/gpt-image-2", aspect_ratio="3:2")
        self.assertEqual(body["aspect_ratio"], "3:2")

    def test_missing_reference_files_are_skipped(self):
        body = imagegen.build_body("x", "openai/gpt-image-2", refs=["/does/not/exist.png"])
        self.assertNotIn("input_references", body)

    def test_reference_file_becomes_data_url(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n")
            path = f.name
        try:
            body = imagegen.build_body("x", "openai/gpt-image-2", refs=[path])
            url = body["input_references"][0]["image_url"]["url"]
            self.assertTrue(url.startswith("data:image/png;base64,"))
        finally:
            os.unlink(path)

    def test_body_is_json_serializable(self):
        json.dumps(imagegen.build_body("배럴", "openai/gpt-image-2"))


class TestErrorMessages(unittest.TestCase):
    def test_auth_error_names_the_key(self):
        self.assertIn("키", imagegen._explain(401, ""))

    def test_credit_error_names_credit(self):
        self.assertIn("크레딧", imagegen._explain(402, ""))

    def test_unknown_status_keeps_detail(self):
        self.assertIn("teapot", imagegen._explain(418, "teapot"))


if __name__ == "__main__":
    unittest.main()
