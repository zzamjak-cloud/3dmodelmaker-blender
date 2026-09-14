# 시스템/유저 프롬프트 조립 + lp 헬퍼 API 레퍼런스 자동 추출
import inspect
import json
import os

_PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")

# 배경 모드 프롬프트에만 노출하는 씬 헬퍼 — lowpoly.SCENE_API를 읽지 못하는 환경
# (bpy 없는 유닛 테스트)에서도 프롬프트 형태를 검증할 수 있도록 이름 목록을 복제해 둔다.
_SCENE_API_FALLBACK = ["terrain", "instance", "place_grid", "place_along", "place_scatter",
                       "wall_run", "path_strip", "ground_snap", "kit"]
# 배치 턴에서도 필요한 최소 모델링 헬퍼 (단순 구조물과 배색용)
_SCENE_BASE_API = ["set_color", "box", "cylinder", "cone", "plane", "join", "array"]


def _read(filename: str) -> str:
    with open(os.path.join(_PROMPT_DIR, filename), encoding="utf-8") as f:
        return f.read()


def _helpers():
    """lowpoly 모듈 — bpy가 없는 환경(유닛 테스트)에서는 None."""
    try:
        from .. import lowpoly
        return lowpoly
    except Exception:
        return None


def _api_reference(names=None) -> str:
    """주어진 이름 목록(기본: lowpoly.__all__ 전체)의 시그니처+독스트링으로 API 문서를 생성한다."""
    lowpoly = _helpers()
    if names is None:
        names = list(lowpoly.__all__) if lowpoly else []
    lines = []
    for name in names:
        fn = getattr(lowpoly, name, None) if lowpoly else None
        sig, doc = "(...)", ""
        if fn is not None:
            try:
                sig = str(inspect.signature(fn))
            except (TypeError, ValueError):
                sig = "(...)"
            doc = inspect.getdoc(fn) or ""
        lines.append(f"### lp.{name}{sig}")
        if doc:
            lines.append(doc)
        lines.append("")
    return "\n".join(lines)


def _scene_api_names() -> list:
    lowpoly = _helpers()
    names = list(getattr(lowpoly, "SCENE_API", None) or []) if lowpoly else []
    return (names or list(_SCENE_API_FALLBACK)) + list(_SCENE_BASE_API)


def build_system_prompt(mode: str = "OBJECT") -> str:
    """모드별 시스템 프롬프트. OBJECT는 기존과 동일하고, SCENE은 배경용 지침+씬 API만 노출한다."""
    if str(mode or "").strip().upper() == "SCENE":
        return (_read("system_scene.md") + "\n\n## lp 헬퍼 API 레퍼런스\n\n"
                + _api_reference(_scene_api_names()))
    return _read("system_lowpoly.md") + "\n\n## lp 헬퍼 API 레퍼런스\n\n" + _api_reference()


def _ref_note(ref_image: str) -> str:
    return (
        f"\n\n사용자가 참조 이미지를 제공했다: {ref_image}\n"
        "코드를 쓰기 전에 참조 이미지를 반드시 확인하고, 형태·비율·실루엣·색 구성을 "
        "참조에 최대한 가깝게 만들어라. 로우폴리로 단순화하되 참조의 시그니처 요소와 비율은 유지하라."
    )


def _multiview_note(multiview: str) -> str:
    return (
        f"\n\n{multiview} 는 이 대상의 멀티뷰 참조 시트다 "
        "(좌상=정면, 우상=측면, 좌하=상면, 우하=3/4뷰). "
        "코드를 쓰기 전에 반드시 확인하고, 각 뷰의 실루엣·비율·색을 그대로 따라 모델링하라."
    )


def _fewshot_note(examples: list) -> str:
    """과거 합격 결과를 스타일 참고용 예시로 붙인다 (그대로 베끼지 않도록 명시)."""
    parts = ["\n\n## 참고: 이 프로젝트에서 이미 합격한 유사 모델의 코드"]
    for request, code in examples:
        parts.append(f"\n요청: {request}\n```python\n{code}\n```")
    parts.append(
        "\n위 코드는 스타일·구조(질량 위계, 헬퍼 사용법, 배색 방식) 참고용이다. "
        "그대로 베끼지 말고 이번 요청에 맞는 형태를 새로 설계하되, 검증된 패턴은 적극 활용하라."
    )
    return "\n".join(parts)


def build_initial_prompt(user_request: str, ref_image: str = None, multiview: str = None,
                         fewshot: list = None) -> str:
    return (
        f"다음 로우폴리 모델을 만들어라: {user_request}\n\n"
        "시스템 지침의 출력 형식(STATUS 헤더 + python 코드 블록 1개)을 반드시 지켜라."
    ) + (_ref_note(ref_image) if ref_image else "") \
      + (_multiview_note(multiview) if multiview else "") \
      + (_fewshot_note(fewshot) if fewshot else "")


def build_error_prompt(traceback_text: str) -> str:
    return (
        "직전 코드 실행이 실패했다. traceback:\n\n"
        f"```\n{traceback_text}\n```\n\n"
        "원인을 수정한 **전체 코드**를 다시 작성하라 (이전 생성물은 자동 삭제된다). "
        "출력 형식: 첫 줄 `STATUS: DONE` + python 코드 블록 1개."
    )


def build_variation_prompt(original_request: str, final_code: str, count: int) -> str:
    return (
        f"이전에 다음 요청으로 모델을 만들었다: {original_request}\n\n"
        f"그때의 최종 코드:\n```python\n{final_code}\n```\n\n"
        f"같은 스타일을 유지하면서 치수·비율·색·디테일을 다르게 한 변형(variation) {count}개를 "
        "한 코드 안에서 X축으로 나란히 배치해 생성하라. "
        "출력 형식: 첫 줄 `STATUS: DONE` + python 코드 블록 1개."
    )


# --- 배경(SCENE) 모드 프롬프트 -------------------------------------------------

_SIZE_METERS = {"S": 20, "M": 40, "L": 80}
_SIZE_CLASS_TRI_TEXT = "L 6000 / M 2500 / S 800"


def _sceneview_note(sceneview: str) -> str:
    return (
        f"\n\n{sceneview} 는 이 씬의 컨셉 시트다 "
        "(좌=아이소메트릭 조감도, 우=탑다운 레이아웃 맵). "
        "설계 전에 반드시 확인하고, 구역 구성·랜드마크 위치·색 구성을 시트에 맞춰라."
    )


def build_scene_plan_prompt(request: str, scene_size: str, tri_budget: int, max_assets: int,
                            ref_image: str = None, sceneview: str = None) -> str:
    """플랜 턴 유저 프롬프트 — 코드가 아니라 플랜 JSON을 요구한다."""
    size = str(scene_size or "M").strip().upper()
    meters = _SIZE_METERS.get(size, _SIZE_METERS["M"])
    return (
        f"다음 배경 공간(씬)을 설계하라: {request}\n\n"
        f"씬 규모: {size} — 한 변 약 {meters}m의 정사각형 부지.\n"
        f"씬 전체 트라이 예산: {tri_budget}\n"
        f"에셋 종류 상한: {max_assets}종 — 종류를 늘리지 말고 같은 프랍을 count로 반복하라.\n"
        f"에셋 1개당 트라이 상한: {_SIZE_CLASS_TRI_TEXT}\n"
        "Σ(count × size_class 상한) + 지형 여유 2000 이 예산을 넘지 않게 개수를 정하라.\n\n"
        "이번 턴은 **플랜 턴**이다. 코드를 쓰지 마라. 구역(zones)·지형(terrain)·"
        "에셋 목록(assets)·씬 팔레트를 설계해 시스템 지침의 플랜 JSON 스키마 그대로 출력하라.\n"
        "출력 형식: 첫 줄 `STATUS: PLAN` + json 코드 블록 정확히 1개 (python 블록 금지)."
    ) + (_ref_note(ref_image) if ref_image else "") \
      + (_sceneview_note(sceneview) if sceneview else "")


def build_scene_asset_prompt(asset: dict, palette: list, tri_limit: int) -> str:
    """자식 에셋 잡의 요청문 — 이 문자열이 기존 오브젝트 파이프라인의 request로 들어간다."""
    asset = asset or {}
    key = str(asset.get("key") or "asset")
    prompt = str(asset.get("prompt") or key.replace("_", " ")).strip()
    colors = ", ".join(str(c) for c in (palette or [])) or "캐주얼 톤 자유 배색"
    return (
        f"{prompt}\n"
        f"씬 팔레트 힌트: {colors} — 이 색들 위주로 배색하되 재질 고유색은 지켜라.\n"
        f"트라이 상한 {tri_limit} 이내로 만들어라.\n"
        "원점은 바닥 중앙(X=Y=0, 바닥이 Z=0)에 오게 하고, 마지막에 lp.join으로 "
        "단일 오브젝트로 마무리하라.\n"
        "배경에 여러 개가 반복 배치될 프랍이다 — 캐주얼 과장(특징 부위 130~160%)은 유지하되 "
        "실루엣이 멀리서도 읽히게 단순하게 정리하라."
    )


def _kit_manifest_table(kit_manifest: list) -> str:
    lines = ["| key | 오브젝트 이름 | 크기 x,y,z (m) | 트라이 |", "|---|---|---|---|"]
    for item in kit_manifest or []:
        size = item.get("size") or (0, 0, 0)
        try:
            size_text = "%.2f x %.2f x %.2f" % (float(size[0]), float(size[1]), float(size[2]))
        except (TypeError, ValueError, IndexError):
            size_text = "?"
        lines.append("| %s | %s | %s | %s |" % (item.get("key", "?"), item.get("obj_name", "?"),
                                                size_text, item.get("tri", "?")))
    return "\n".join(lines)


def build_scene_place_prompt(plan: dict, kit_manifest: list, tri_budget: int,
                             sceneview: str = None) -> str:
    """배치 턴 유저 프롬프트 — 플랜과 완성된 키트 명단을 주고 배치 코드를 요구한다."""
    plan_text = json.dumps(plan or {}, ensure_ascii=False, indent=2)
    keys = ", ".join(str(item.get("key")) for item in (kit_manifest or []))
    return (
        "에셋 키트가 준비됐다. 이번 턴은 **배치 턴**이다 — 아래 플랜대로 지형·구조물을 만들고 "
        "키트를 배치하는 코드를 작성하라.\n\n"
        "## 플랜 (확정본)\n"
        f"```json\n{plan_text}\n```\n\n"
        "## 사용 가능한 키트 (이 key만 존재한다)\n"
        f"{_kit_manifest_table(kit_manifest)}\n\n"
        f"사용 가능한 key: {keys}\n"
        "`lp.kit`에는 위 표의 **오브젝트 이름**을 그대로 넘겨라 (key는 플랜과 대조하는 용도다). "
        "명단에 없는 에셋은 존재하지 않는다 — 빠진 에셋은 없는 셈 치고 구성을 조정하고, "
        "키트에 없는 프랍을 새로 모델링하지 마라.\n\n"
        "## 작성 순서\n"
        "1. `lp.terrain`으로 지형 1개를 만든다 (플랜의 relief 반영, 색은 팔레트에서).\n"
        "2. `lp.wall_run` / `lp.path_strip` / 단순 `lp.box`로 담장·길·계단 같은 구조물을 만든다. "
        "길은 씬 가장자리에서 시작해 랜드마크로 향하게 하라.\n"
        "3. 구역별로 `lp.kit(\"오브젝트 이름\")`으로 원본을 가져와 `lp.place_grid` / `lp.place_along` / "
        "`lp.place_scatter` / `lp.instance`로 배치한다. 회전·스케일 지터로 변주하고 "
        "빈 공간 40%는 남겨라.\n"
        "4. 마지막에 `lp.ground_snap(모든 인스턴스 리스트, 지형)`을 **한 번** 호출한다.\n\n"
        "`lp.set_color`는 이 턴에서 새로 만든 오브젝트(지형·벽·길·box)에만 쓴다. "
        "인스턴스에 칠하면 공유 메시의 UV가 바뀌어 원본과 나머지 인스턴스까지 전부 같은 색이 된다 — "
        "키트 에셋의 색은 이미 정해져 있으니 건드리지 마라.\n\n"
        f"씬 전체 트라이 예산: {tri_budget}. 초과가 예상되면 프랍 개수와 지형 셀 수를 먼저 줄여라 "
        "(랜드마크는 마지막까지 지킨다).\n"
        "출력 형식: 첫 줄 `STATUS: DONE` + python 코드 블록 정확히 1개, 200줄 이내."
    ) + (_sceneview_note(sceneview) if sceneview else "")


def build_scene_budget_prompt(tri_count: int, tri_budget: int) -> str:
    """예산 초과 시 밀도를 낮춰 다시 받기 위한 resume 턴."""
    return (
        f"방금 실행한 배치 코드의 씬 전체 트라이가 {tri_count}로 예산 {tri_budget}을 초과했다.\n"
        "인스턴스 개수(특히 반복 프랍)와 지형 셀 수를 줄여 예산 이내로 맞춘 **전체 코드**를 "
        "다시 작성하라 (이전 생성물은 자동 삭제된다). 랜드마크와 구역 구성은 유지하고, "
        "빈 공간을 더 넓히는 방향으로 줄여라.\n"
        "출력 형식: 첫 줄 `STATUS: DONE` + python 코드 블록 1개."
    )


def build_scene_plan_retry_prompt(error: str) -> str:
    """플랜 형식·검증 오류 1회 재요청."""
    return (
        f"직전 플랜 응답을 사용할 수 없다. 원인: {error}\n\n"
        "시스템 지침의 플랜 JSON 스키마(scene / terrain / zones / assets / rules)를 그대로 지켜 "
        "처음부터 다시 출력하라. 설명이나 python 코드는 넣지 마라.\n"
        "출력 형식: 첫 줄 `STATUS: PLAN` + json 코드 블록 정확히 1개."
    )
