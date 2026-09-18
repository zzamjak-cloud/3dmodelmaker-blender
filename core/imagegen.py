# 이미지 생성 백엔드: OpenRouter Image API (/api/v1/images) 직접 호출
#
# 예전에는 참조 시트 3종(멀티뷰·씬 컨셉·텍스처 6면도)을 모두 `codex exec` 세션으로 만들었다.
# 이미지 한 장마다 Astra 세션이 통째로 붙어 토큰 소모가 컸고, 모델을 고를 수도 없었다.
# 이 모듈은 같은 일을 HTTP 한 번으로 처리한다 — Astra는 모델링 턴에만 쓰인다.
#
# 모델별 지원 파라미터는 추측하지 않는다. https://openrouter.ai/api/v1/images/models 의
# `supported_parameters` 응답을 그대로 옮겨 적었다 (2026-09-17 실측). 모델을 추가할 때는
# 반드시 같은 방식으로 확인하라 — 미지원 파라미터를 보내면 400이 난다.
import base64
import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/images"
APP_TITLE = "AI LowPoly ModelMaker"

# gpt-image 계열 공통 비율 (OpenRouter 실측). 'auto'는 사용자가 고를 값이 아니라 제외
_GPT_RATIOS = ("1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16", "21:9")
# Gemini 계열 — 초극단 비율(1:4·1:8·4:1·8:1)은 쓸 일이 없어 넣지 않았다
_GEMINI_RATIOS = ("1:1", "3:2", "2:3", "4:3", "3:4", "4:5", "5:4", "16:9", "9:16", "21:9")

# id, 라벨, provider, 지원 품질, 지원 해상도, 참조 이미지 상한
MODELS = (
    {"id": "openai/gpt-image-2", "label": "덕테이프 (GPT Image 2)",
     "desc": "기본값 — 레이아웃·지시 준수도가 가장 높다. 3면도/6면도 격자를 정확히 지킨다",
     "provider": "openai", "qualities": ("low", "medium", "high"),
     "resolutions": (), "ratios": _GPT_RATIOS, "max_refs": 16},
    {"id": "openai/gpt-image-2.5-sunburst", "label": "덕테이프 2.5 선버스트",
     "desc": "GPT Image 2.5 상위 티어 — 품질 xhigh/max 사용 가능, 느리고 비싸다",
     "provider": "openai", "qualities": ("low", "medium", "high", "xhigh", "max"),
     "resolutions": (), "ratios": _GPT_RATIOS, "max_refs": 16},
    {"id": "openai/gpt-image-2.5-flare", "label": "덕테이프 2.5 플레어",
     "desc": "GPT Image 2.5 경량 티어",
     "provider": "openai", "qualities": ("low", "medium", "high", "xhigh", "max"),
     "resolutions": (), "ratios": _GPT_RATIOS, "max_refs": 16},
    {"id": "google/gemini-3-pro-image-preview", "label": "나노바나나 프로",
     "desc": "Gemini 3 Pro Image — 묘사력이 높지만 격자 레이아웃 지시를 종종 무시한다",
     "provider": "gemini", "qualities": (),
     "resolutions": ("1K", "2K", "4K"), "ratios": _GEMINI_RATIOS, "max_refs": 14},
    {"id": "google/gemini-3.1-flash-image-preview", "label": "나노바나나 2",
     "desc": "Gemini 3.1 Flash Image — 빠르고 저렴",
     "provider": "gemini", "qualities": (),
     "resolutions": ("512", "1K", "2K", "4K"), "ratios": _GEMINI_RATIOS, "max_refs": 14},
    {"id": "google/gemini-3.1-flash-lite-image", "label": "나노바나나 2 라이트",
     "desc": "Gemini 3.1 Flash Lite Image — 가장 저렴, 1K 고정",
     "provider": "gemini", "qualities": (),
     "resolutions": ("1K",), "ratios": _GEMINI_RATIOS, "max_refs": 14},
)

# 참조 시트는 레이아웃(2x2 격자·좌우 2분할·6면도 3x2) 준수가 품질보다 중요하다.
# StyleStudio의 타일맵 세션이 같은 이유로 이 모델에 고정돼 있다.
DEFAULT_MODEL = "openai/gpt-image-2"

_BY_ID = {m["id"]: m for m in MODELS}


def enum_items():
    """EnumProperty items — 환경설정 드롭다운용."""
    return [(m["id"], m["label"], m["desc"]) for m in MODELS]


def model_def(model_id: str) -> dict:
    """모델 정의. 모르는 id면 기본 모델로 떨어진다 (구버전 설정값 보호)."""
    return _BY_ID.get(str(model_id or ""), _BY_ID[DEFAULT_MODEL])


def api_key() -> str:
    """환경설정 키 → 없으면 OPENROUTER_API_KEY 환경변수."""
    from .. import preferences
    prefs = preferences.get_prefs()
    return (getattr(prefs, "openrouter_api_key", "") or
            os.environ.get("OPENROUTER_API_KEY", "")).strip()


def is_available() -> bool:
    """OpenRouter 경로를 쓸 수 있는가 — 키가 있어야 한다."""
    return bool(api_key())


def _data_url(path: str) -> str:
    with open(path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")


def build_body(prompt: str, model_id: str, refs=None, aspect_ratio: str = "1:1",
               quality: str = "high", resolution: str = "2K") -> dict:
    """요청 본문. 모델이 지원하지 않는 파라미터는 아예 넣지 않는다 (400 방지)."""
    spec = model_def(model_id)
    body = {"model": spec["id"], "prompt": prompt}
    if aspect_ratio in spec["ratios"]:
        body["aspect_ratio"] = aspect_ratio
    if spec["qualities"]:
        body["quality"] = quality if quality in spec["qualities"] else spec["qualities"][-1]
    if spec["resolutions"]:
        body["resolution"] = (resolution if resolution in spec["resolutions"]
                              else spec["resolutions"][-1])
    paths = [p for p in (refs or []) if p and os.path.isfile(p)][:spec["max_refs"]]
    if paths:
        body["input_references"] = [
            {"type": "image_url", "image_url": {"url": _data_url(p)}} for p in paths
        ]
    return body


def _explain(status: int, detail: str) -> str:
    """HTTP 오류를 사용자가 조치할 수 있는 한국어 문장으로."""
    if status == 401:
        return "OpenRouter API 키가 올바르지 않거나 만료됐습니다 (환경설정에서 확인)"
    if status == 402:
        return "OpenRouter 크레딧이 부족합니다 (openrouter.ai에서 잔액 확인)"
    if status == 429:
        return "OpenRouter 요청 한도를 초과했습니다 — 잠시 후 다시 시도하세요"
    if status == 400:
        return "OpenRouter가 요청을 거부했습니다 (모델이 지원하지 않는 파라미터일 수 있음): " + detail
    return "OpenRouter 오류 %d: %s" % (status, detail)


def _worker_module():
    """http_worker 를 가져온다. 패키지 밖(단위 테스트)에서 상대 임포트가 안 되면 파일로 로드한다(sys.modules 는 건드리지 않음)."""
    try:
        from . import http_worker
        return http_worker
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_lp3d_http_worker", os.path.join(os.path.dirname(os.path.abspath(__file__)), "http_worker.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def build_spec(prompt: str, out_path: str, model_id: str = None, refs=None,
               aspect_ratio: str = "1:1", quality: str = "high",
               resolution: str = "2K", timeout: int = 300):
    """HTTP 워커용 요청 spec. 본문은 파일로 넘긴다(참조 이미지 data URL 이 커서 인자로 못 넘긴다).

    (spec, None) 또는 (None, 오류 문자열)."""
    key = api_key()
    if not key:
        return None, "OpenRouter API 키가 설정되지 않았습니다"
    body = build_body(prompt, model_id or DEFAULT_MODEL, refs=refs,
                      aspect_ratio=aspect_ratio, quality=quality, resolution=resolution)
    work_dir = os.path.dirname(os.path.abspath(out_path))
    body_file = out_path + ".request.json"
    response_file = out_path + ".response.json"
    try:
        with open(body_file, "w", encoding="utf-8") as f:
            json.dump(body, f)
    except OSError as e:
        return None, "요청 본문 저장 실패: %s" % e
    return {"url": API_URL, "method": "POST", "body_file": body_file, "out_file": response_file,
            "work_dir": work_dir, "timeout": timeout,
            "headers": {"Authorization": "Bearer " + key, "Content-Type": "application/json",
                        "X-OpenRouter-Title": APP_TITLE}}, None


def _online_hint() -> str:
    """Blender 의 인터넷 접근 설정이 꺼져 있으면 원인 후보로 알려준다(요청 자체는 애드온 설정의 키로 사용자가 명시한 것)."""
    try:
        import bpy
        if not getattr(bpy.app, "online_access", True):
            return " — Blender 환경설정 > 시스템 > '온라인 접근 허용'이 꺼져 있습니다"
    except ImportError:
        pass
    return ""


def handle_result(result: dict, spec: dict, out_path: str):
    """워커 결과 → 이미지 저장. (저장 경로, None) 또는 (None, 오류 문자열)."""
    if not result:
        return None, "OpenRouter 응답 없음"
    if result.get("kind") == "http":
        return None, _explain(int(result.get("status") or 0), result.get("detail", ""))
    if result.get("kind") in ("url", "timeout"):
        return None, "OpenRouter에 연결할 수 없습니다: %s%s" % (result.get("reason", ""), _online_hint())
    if result.get("kind"):
        return None, "OpenRouter 응답 처리 실패: %s" % result.get("reason", "")
    try:
        with open(spec["out_file"], encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError) as e:
        return None, "OpenRouter 응답 처리 실패: %s" % e
    data = (payload.get("data") or [None])[0] or {}
    b64 = data.get("b64_json")
    if not b64:
        return None, "OpenRouter 응답에 이미지 데이터가 없습니다 (b64_json)"
    try:
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(b64))
    except (OSError, ValueError) as e:
        return None, "이미지 저장 실패: %s" % e
    for temp in (spec.get("body_file"), spec.get("out_file")):
        try:
            if temp and os.path.exists(temp):
                os.remove(temp)
        except OSError:
            pass
    return out_path, None


def request_image(prompt: str, out_path: str, model_id: str = None, refs=None,
                  aspect_ratio: str = "1:1", quality: str = "high",
                  resolution: str = "2K", timeout: int = 300):
    """이미지를 생성해 out_path에 저장한다 (블로킹 — 스모크·테스트용, 애드온 안에서는 generate 를 쓴다).

    (저장 경로, None) 또는 (None, 오류 문자열)."""
    http_worker = _worker_module()
    spec, error = build_spec(prompt, out_path, model_id=model_id, refs=refs, aspect_ratio=aspect_ratio,
                             quality=quality, resolution=resolution, timeout=timeout)
    if spec is None:
        return None, error
    return handle_result(http_worker.perform(spec), spec, out_path)


def generate(prompt: str, out_path: str, timeout: int, on_done, refs=None,
             aspect_ratio: str = "1:1", job_key=None):
    """참조 시트를 비동기로 생성한다. 완료 시 메인 스레드에서 on_done(경로|None, 오류|None).

    codex 경로(multiview/sceneview/texgen)와 콜백 계약을 일부러 똑같이 맞췄다 —
    호출부는 백엔드를 한 줄로 갈아끼울 수 있다."""
    from .. import preferences
    from . import runner

    prefs = preferences.get_prefs()
    model_id = getattr(prefs, "image_model", DEFAULT_MODEL)
    quality = getattr(prefs, "image_quality", "high")

    spec, error = build_spec(prompt, out_path, model_id=model_id, refs=refs,
                             aspect_ratio=aspect_ratio, quality=quality, timeout=timeout)
    if spec is None:
        runner._finish_now(on_done, None, error)
        return
    runner.run_http_async(spec, lambda result, err=None: on_done(*(handle_result(result, spec, out_path) if not err else (None, err))),
                          job_key=job_key)
