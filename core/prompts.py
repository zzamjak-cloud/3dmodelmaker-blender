# 시스템/유저 프롬프트 조립 + lp 헬퍼 API 레퍼런스 자동 추출
import inspect
import json
import os

_PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")

# 배경 모드 프롬프트에만 노출하는 씬 헬퍼 — lowpoly.SCENE_API를 읽지 못하는 환경
# (bpy 없는 유닛 테스트)에서도 프롬프트 형태를 검증할 수 있도록 이름 목록을 복제해 둔다.
_SCENE_API_FALLBACK = ["terrain", "room", "instance", "place_grid", "place_along",
                       "place_scatter", "place_cluster", "meander", "wall_run", "fence_run",
                       "path_strip", "ground_snap", "kit"]
# 배치 턴에서도 필요한 최소 모델링 헬퍼 (단순 구조물과 배색용)
_SCENE_BASE_API = ["set_color", "box", "cylinder", "cone", "plane", "join", "array"]
# 복셀 스타일에만 노출하는 격자 헬퍼 (bpy 없는 유닛 테스트용 복제 목록)
_VOXEL_API_FALLBACK = ["voxel", "voxel_box", "voxel_column"]


def _read(filename: str) -> str:
    with open(os.path.join(_PROMPT_DIR, filename), encoding="utf-8") as f:
        return f.read()


# 패키지 밖(단위 테스트)에서 상대 임포트가 실패할 때 파일로 로드한 모듈 캐시 — sys.modules 는 건드리지 않는다(확장 검증기 제약)
_FALLBACK_MODULES = {}


def _helpers():
    """lowpoly 모듈 — bpy가 없는 환경(유닛 테스트)에서는 None."""
    try:
        from .. import lowpoly
        return lowpoly
    except Exception:
        return None


def _styles():
    """styles 모듈 — 이 파일이 패키지 없이 로드되는 경로(유닛 테스트)에서는 경로로 읽는다.

    styles.py는 bpy에 의존하지 않으므로 _helpers()와 달리 None으로 물러설 수 없다 —
    스타일 지침이 빠지면 프롬프트에서 아트 디렉션이 통째로 사라진다."""
    try:
        from . import styles
        return styles
    except ImportError:
        import importlib.util
        cached = _FALLBACK_MODULES.get("_lp3d_styles")
        if cached is not None:
            return cached
        spec = importlib.util.spec_from_file_location(
            "_lp3d_styles", os.path.join(os.path.dirname(os.path.abspath(__file__)), "styles.py"))
        module = importlib.util.module_from_spec(spec)
        _FALLBACK_MODULES["_lp3d_styles"] = module
        spec.loader.exec_module(module)
        return module


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


def _voxel_api_names() -> list:
    lowpoly = _helpers()
    names = list(getattr(lowpoly, "VOXEL_API", None) or []) if lowpoly else []
    return names or list(_VOXEL_API_FALLBACK)


def build_system_prompt(mode: str = "OBJECT", style: str = None) -> str:
    """모드·스타일별 시스템 프롬프트.

    공통 골격(system_base.md / system_scene.md) 뒤에 스타일 지침을 붙이고, 그 뒤에
    API 레퍼런스를 둔다. 스타일이 API보다 앞에 와야 "이 스타일에서는 이 헬퍼를
    쓰지 마라" 같은 지시가 레퍼런스를 읽기 전에 걸린다."""
    styles = _styles()

    names = _scene_api_names() if str(mode or "").strip().upper() == "SCENE" else None
    if names is None:
        names = list(_helpers().__all__) if _helpers() else []
    if styles.uses_voxel_api(style):
        names = names + [n for n in _voxel_api_names() if n not in names]

    mode_key = str(mode or "").strip().upper()
    if mode_key == "SCENE":
        base = _read("system_scene.md")
    elif mode_key == "CHARACTER":
        base = _read("system_character.md")  # 리깅 자세·관절 분할·턴어라운드 읽기
    else:
        base = _read("system_base.md")
    return (base + "\n\n" + styles.guide(style)
            + "\n\n## lp 헬퍼 API 레퍼런스\n\n" + _api_reference(names))


def _ref_note(ref_image: str) -> str:
    return (
        f"\n\n사용자가 참조 이미지를 제공했다: {ref_image}\n"
        "코드를 쓰기 전에 참조 이미지를 반드시 확인하고, 형태·비율·실루엣·색 구성을 "
        "참조에 최대한 가깝게 만들어라. 로우폴리로 단순화하되 참조의 시그니처 요소와 비율은 유지하라."
    )


def _multiview_note(multiview: str, mode: str = "OBJECT") -> str:
    if str(mode or "").strip().upper() == "CHARACTER":
        return (
            f"\n\n{multiview} 는 이 캐릭터의 턴어라운드 시트(3x2)다 "
            "(윗줄: 정면 | 뒷면 | 좌측면, 아랫줄: 우측면 | 상면 | 3/4뷰). "
            "코드를 쓰기 전에 반드시 확인하고, 시트의 자세·비율·얼굴·의상·장비·색을 그대로 따라 "
            "모델링하라. 시트와 요청문이 다르면 시트가 이긴다."
        )
    return (
        f"\n\n{multiview} 는 이 대상의 멀티뷰 참조 시트다 "
        "(좌상=정면, 우상=측면, 좌하=상면, 우하=3/4뷰). "
        "코드를 쓰기 전에 반드시 확인하고, 각 뷰의 실루엣·비율·색을 그대로 따라 모델링하라."
    )


# 캐릭터 유형별 비율·골격 지시 — 시스템 지침 규칙 7의 어느 항을 적용할지 못 박는다
_CHARACTER_TYPE_NOTE = {
    "AUTO": "유형은 요청문·원화에서 판단하라(인간형/동물형/크리처형) — 판단한 유형의 비율 규칙을 적용한다.",
    "HUMANOID": "유형: **인간형**. 머리 크기 기준 두신 비율(스타일이 정함), A-포즈(양팔을 45도 아래로 벌림), 어깨 폭은 머리 1.5~2배.",
    "ANIMAL": ("유형: **동물형**. 실제 동물의 골격 비율과 관절 방향(앞다리 팔꿈치 뒤·뒷다리 무릎 앞·"
               "발목 뒤)을 지키고, 네 발로 선 중립 자세로 만든다."),
    "CREATURE": ("유형: **크리처형**. 동물 2~3종의 부위를 조합하되 팔·다리·꼬리·날개가 하나의 골격 "
                 "논리로 몸통에서 나오게 하라. 조합만 늘어놓은 덩어리가 되지 않게 시그니처 3개를 먼저 정한다."),
}


def character_type_note(character_type: str) -> str:
    return _CHARACTER_TYPE_NOTE.get(str(character_type or "AUTO").strip().upper(),
                                    _CHARACTER_TYPE_NOTE["AUTO"])


def build_character_compare_prompt(sheet: str, render: str, code: str, turn: int,
                                   total: int) -> str:
    """6면도 시트와 현재 모델 렌더를 나란히 주고 차이를 고친 전체 코드를 요구한다.

    첫 생성은 시트를 '보고' 만들지만 결과가 시트와 얼마나 다른지는 알 수 없다.
    같은 6시점으로 렌더한 모델을 시트 옆에 놓고 대조시키면 빠진 요소·비율 오차·
    떨어진 파트가 드러난다."""
    return (
        f"방금 실행한 코드의 결과를 시트와 같은 6시점으로 렌더했다 (대조 {turn}/{total}).\n\n"
        f"- {sheet}: 목표 턴어라운드 시트 (윗줄 정면|뒷면|좌측면, 아랫줄 우측면|상면|3/4)\n"
        f"- {render}: 현재 모델 렌더 (윗줄 FRONT|RIGHT|BACK, 아랫줄 LEFT|TOP|BOTTOM — 칸 순서가 시트와 다르니 라벨로 대조하라)\n\n"
        "두 이미지를 칸별로 대조해 다음을 찾아 **모두** 고쳐라:\n"
        "1. 시트에는 있는데 모델에 없는 요소 (귀·꼬리·장비·무늬·털 다발 등) — 추가\n"
        "2. 비율 차이 — 머리 크기·어깨 폭·팔다리 길이·몸통 두께를 시트 기준으로 수정\n"
        "3. 몸에서 떨어져 떠 있는 파트, 관절에서 끊긴 파트 — 이웃 파트에 파묻고 관절 sphere로 이어라\n"
        "4. 실루엣 차이 — 측면·상면에서 시트와 다른 윤곽\n"
        "5. 색 배치 차이 — 어느 부위가 어떤 색인지\n\n"
        "이전 코드:\n"
        f"```python\n{code}\n```\n\n"
        "차이를 고친 **전체 코드**를 다시 작성하라 (이전 생성물은 자동 삭제된다). 잘 맞는 부분은 "
        "그대로 두고, 트라이가 늘어도 좋으니 빠진 요소를 빼먹지 마라. "
        "출력 형식: 첫 줄 `STATUS: DONE` + python 코드 블록 1개."
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
                         fewshot: list = None, mode: str = "OBJECT",
                         character_type: str = "AUTO") -> str:
    if str(mode or "").strip().upper() == "CHARACTER":
        head = (
            f"다음 게임 캐릭터를 만들어라: {user_request}\n"
            f"{character_type_note(character_type)}\n\n"
            "리깅 자세(A-포즈 / 네 발 중립), 정면 -Y, 발바닥 z=0, 관절 단위 파트 분할, "
            "반쪽 모델링 + lp.mirror_x를 반드시 지켜라. "
            "시스템 지침의 출력 형식(STATUS 헤더 + python 코드 블록 1개)을 반드시 지켜라."
        )
    else:
        head = (
            f"다음 모델을 만들어라: {user_request}\n\n"
            "시스템 지침의 출력 형식(STATUS 헤더 + python 코드 블록 1개)을 반드시 지켜라."
        )
    return head + (_ref_note(ref_image) if ref_image else "") \
        + (_multiview_note(multiview, mode) if multiview else "") \
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

def _scene_plan():
    """scene_plan 모듈 — prompts.py가 패키지 없이 로드되는 경로(유닛 테스트) 대응."""
    try:
        from . import scene_plan
        return scene_plan
    except ImportError:
        import importlib.util
        cached = _FALLBACK_MODULES.get("_lp3d_scene_plan")
        if cached is not None:
            return cached
        spec = importlib.util.spec_from_file_location(
            "_lp3d_scene_plan",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "scene_plan.py"))
        module = importlib.util.module_from_spec(spec)
        _FALLBACK_MODULES["_lp3d_scene_plan"] = module
        spec.loader.exec_module(module)
        return module


def _tri_class_text(style_scale: float) -> str:
    sp = _scene_plan()
    return " / ".join("%s %d" % (cls, sp.asset_tri_limit(cls, style_scale))
                      for cls in ("L", "M", "S"))


def _sceneview_note(sceneview: str) -> str:
    return (
        f"\n\n{sceneview} 는 이 씬의 컨셉 시트다 "
        "(좌=아이소메트릭 조감도, 우=탑다운 레이아웃 맵). "
        "설계 전에 반드시 확인하고, 구역 구성·랜드마크 위치·색 구성을 시트에 맞춰라."
    )


def build_scene_plan_prompt(request: str, scene_size: str, tri_budget: int, max_assets: int,
                            ref_image: str = None, sceneview: str = None,
                            style_scale: float = 1.0) -> str:
    """플랜 턴 유저 프롬프트 — 코드가 아니라 플랜 JSON을 요구한다.

    tri_budget이 0 이하면 씬 전체 상한을 걸지 않는다. 스타일의 트라이 상한은 모델
    1개 기준이라 여러 에셋이 들어가는 배경에 그대로 씌우면 밀도를 만들 수 없다."""
    sp = _scene_plan()
    size = str(scene_size or "M").strip().upper()
    if size not in sp.SCENE_SIZE_M:
        size = "M"
    profile = sp.size_profile(size)
    meters = sp.SCENE_SIZE_M[size]
    target = sp.target_instances(size)
    interior = profile["interior"]

    scale_rule = (f"**크기는 컨셉과 대상의 실제 치수로 정하라.** 한 변 {int(meters)}m는 이 규모의 **참고 크기**일 뿐 "
                  "목표도 상한도 아니다 — 첨부 이미지가 보여 주는 공간감과, 들어갈 가구·건물·프랍의 실제 크기와 통로 폭을 "
                  "더해 `scene.extent`를 산정하라"
                  + (" (거실·침실 4~6m, 카페·상점 6~10m). 소파는 약 2.2m, 책장 약 1m다. "
                     if interior else
                     " (캠프 한 곳 8~14m, 주유소 한 곳 18~26m, 마을 한 구역 30~40m). RV는 약 7m, 텐트는 약 2.5m다. ")
                  + "키트가 나온 뒤 시스템이 실제 치수에 맞춰 부지를 늘이거나 줄이지만(모양은 유지), 처음부터 맞게 잡아라.\n")
    place = (f"씬 규모: {size} — {profile['label']}. " + scale_rule
             + ("실내 공간(방·홀·상점 내부).\n"
                "**지형(terrain)을 만들지 마라.** 바닥·벽·천장은 `lp.room` 하나로 만든다. "
                "`terrain.relief`는 실내에서 무시되므로 0으로 두어라.\n"
                "구역(zones)은 옥외 구획이 아니라 **실내 영역**이다 "
                "(계산대 주변 / 진열 구역 / 통로 / 창가 자리).\n"
                if interior else
                "옥외 부지.\n"
                "**부지를 정사각형으로 잡지 마라.** `scene.extent`는 [폭, 깊이]로 비율 1:1.3~1:2 "
                "사이에서 지형·강·길에 맞춰 정하고, 가능하면 `scene.outline`에 6~12점 다각형으로 "
                "비정형 윤곽(해안선·능선·숲 경계)을 그려라. 구역(zones)에는 `rotation`(도)을 넣어 "
                "축에 나란하지 않게 비틀고, 구역 크기(extent)도 서로 다르게 하라.\n"
                "**격자·등간격·직각은 계획도시를 명시적으로 요청받았을 때만.** 그 외에는 실제 "
                "정착지처럼 길이 굽고 건물이 길목·광장·우물 주변에 뭉치며 간격이 고르지 않아야 한다. "
                "`rules`에 이 씬의 배치 성격(예: '집들은 굽은 큰길을 따라 불규칙하게, 뒤편은 군집')을 "
                "한 줄 이상 적어라.\n"
                + (profile["note"] + "\n" if profile.get("note") else "")))

    budget_text = (f"씬 전체 트라이 예산: {tri_budget} — Σ(count × size_class 상한) + 지형 여유 "
                   f"{sp.TERRAIN_RESERVE_TRI} 이 예산을 넘지 않게 개수를 정하라.\n"
                   if tri_budget and tri_budget > 0 else
                   "씬 전체 트라이 예산: **상한 없음**. 아래 트라이 상한은 에셋 **1개** 기준이다 — "
                   "씬 전체를 그 값으로 묶지 마라. 공간이 허전한 것이 무거운 것보다 나쁘다.\n")

    return (
        f"다음 배경 공간(씬)을 설계하라: {request}\n\n"
        + place
        + f"에셋 종류 상한: {max_assets}종 — 종류를 늘리지 말고 같은 프랍을 count로 반복하라.\n"
        + f"에셋 1개당 트라이 상한: {_tri_class_text(style_scale)}\n"
        + budget_text
        + f"**배치 총량 기준: 부지 100㎡당 인스턴스 {profile['density']:g}개** (참고 크기 {int(meters)}m 부지라면 "
        f"{target}개) — 정한 extent의 면적으로 계산해 `assets`의 count 합을 그 수에 맞춰라. 적으면 텅 비고, "
        "많으면 작은 방이 화분으로 뒤덮인다. 첨부 이미지가 있으면 거기 보이는 물건 수가 우선이다.\n"
        + f"랜드마크: {profile['landmarks']}개 — 시선을 잡는 주 구조물에만 `landmark: true`.\n\n"
        + "이번 턴은 **플랜 턴**이다. 코드를 쓰지 마라. 구역(zones)·지형(terrain)·"
        "에셋 목록(assets)·씬 팔레트를 설계해 시스템 지침의 플랜 JSON 스키마 그대로 출력하라.\n"
        "출력 형식: 첫 줄 `STATUS: PLAN` + json 코드 블록 정확히 1개 (python 블록 금지)."
    ) + (_ref_note(ref_image) if ref_image else "") \
      + (_sceneview_note(sceneview) if sceneview else "")


def build_scene_asset_prompt(asset: dict, palette: list, tri_limit: int) -> str:
    """자식 에셋 잡의 요청문 — 이 문자열이 기존 오브젝트 파이프라인의 request로 들어간다."""
    asset = asset or {}
    key = str(asset.get("key") or "asset")
    prompt = str(asset.get("prompt") or key.replace("_", " ")).strip()
    own = str(asset.get("colors") or "").strip()
    scene_colors = ", ".join(str(c) for c in (palette or []))
    if own:
        # 씬 팔레트 '위주'로 칠하라고 하면 RV가 길 색, 의자·텐트가 풀 색이 되어 배경에 묻힌다
        color_line = (f"색: {own} — 이 부위별 색을 그대로 따르라. 씬 지면·길 색과 겹쳐 묻히지 않게 "
                      "명도 대비를 유지하라.\n")
    else:
        color_line = ("색: 이 물건의 실제 고유색으로 칠하라(재질별 2~4색). "
                      + (f"씬 지면·길 색({scene_colors})과는 구분되게 하라.\n" if scene_colors else "\n"))
    return (
        f"{prompt}\n"
        + color_line
        + f"트라이 상한 {tri_limit} 이내로 만들어라.\n"
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
                             sceneview: str = None, scene_size: str = None) -> str:
    """배치 턴 유저 프롬프트 — 플랜과 완성된 키트 명단을 주고 배치 코드를 요구한다."""
    sp = _scene_plan()
    plan_text = json.dumps(plan or {}, ensure_ascii=False, indent=2)
    keys = ", ".join(str(item.get("key")) for item in (kit_manifest or []))
    size = str(scene_size or (plan or {}).get("scene", {}).get("size") or "M").strip().upper()
    if size not in sp.SCENE_SIZE_M:
        size = "M"
    interior = sp.is_interior(size)
    target = sp.target_instances(size, ((plan or {}).get("scene") or {}).get("extent"))
    shell = ("`lp.room`으로 방 껍데기(바닥·벽) 1개" if interior
             else "`lp.terrain`으로 본 지형 1개(컨셉 시트에 고지대·물가가 있으면 작은 지형을 더)")
    return (
        "에셋 키트가 준비됐다. 이번 턴은 **배치 턴**이다 — 아래 플랜대로 "
        + ("실내 공간과 구조물을 만들고 " if interior else "지형·구조물을 만들고 ")
        + "키트를 배치하는 코드를 작성하라.\n\n"
        + (f"**이 씬은 실내다.** 지형(`lp.terrain`)을 만들지 마라 — 기복 있는 땅 위에 가구를 놓으면 "
           "실내로 읽히지 않는다. 바닥·벽은 `lp.room`으로 만들고, 천장은 위에서 안이 보이도록 "
           "`ceiling=False`로 둔다. 사람이 드나드는 쪽 벽 하나는 `open_sides`로 열어 단면처럼 보여라. "
           "`lp.ground_snap`은 실내에서 쓰지 마라 (바닥이 평평하므로 z=0에 그대로 놓으면 된다).\n"
           "**방 크기는 플랜의 `scene.extent` 그대로**, 가구는 컨셉 시트 평면도의 자리를 좌표로 옮겨라. "
           "벽에 붙는 가구(소파·책장·벽난로·선반)는 키트 표의 깊이 절반만큼 벽 안쪽에 두고 방 중앙을 향하게 "
           "회전하라 — 벽에서 떨어진 가구는 넓은 빈 바닥을 만든다. 러그·테이블은 앉는 가구 앞에 붙인다.\n\n"
           if interior else "")
        + f"**배치 총량 기준: 인스턴스 합계 {target}개 안팎.** 플랜의 count를 임의로 줄이지 마라 — "
        "빈 공간 규칙은 '의도적으로 비운 구역 하나'를 뜻하지 전체를 성기게 깔라는 뜻이 아니다.\n\n"
        + ("" if interior else
           "**규칙적으로 보이면 실패다.** 결과가 도면처럼 격자·등간격·직각으로 읽히면 원화 느낌이 "
           "사라진다. 다음을 지켜라:\n"
           "- 지형은 플랜의 `scene.extent`(비정사각) 크기로 만들고, `scene.outline`이 있으면 "
           "`lp.terrain(outline=...)`으로 그 윤곽을 그대로 써라. 정사각 지형 금지. "
           "받침 두께는 플랜의 `terrain.base`를 `base=`로 넘기고, 색은 `color=`(윗면)·`side_color=`(절벽)로 준다.\n"
           "- **지면을 잘게 나눈 평판으로 때우지 마라.** 컨셉 시트의 층(고지대 언덕·절벽·물가)은 작은 윤곽의 "
           "`lp.terrain(..., elevation=, base=)`을 더 세워 표현하고, 흙마당·광장·길은 `lp.path_strip`으로 색을 "
           "나눈다. `lp.ground_snap`에는 지형 리스트([본 지면, 고지대, ...])를 넘겨 가장 높은 면에 놓는다.\n"
           "- 길·개천·성벽·울타리의 폴리라인은 반드시 `lp.meander`로 굽혀서 넘겨라. "
           "직선 두 점으로 그은 길은 도면이다.\n"
           "- 집·노점·덤불·잔해 같은 '모여 있는 것'은 `lp.place_cluster`로 광장·우물·길목 주변에 "
           "뭉치게 놓아라. `lp.place_grid`는 막사·묘지·밭·주차장처럼 실제로 격자인 것에만 쓰고, "
           "그때도 jitter·rotate_jitter를 넣어라.\n"
           "- `lp.place_along`으로 길가에 놓을 때는 `spacing_jitter`·`offset_jitter`·`rotate_jitter`를 "
           "반드시 준다(가로등·망루처럼 의도적으로 규칙적인 것만 예외).\n"
           "- 구역의 `rotation`을 존중해 그 구역의 배치 축을 비틀어라. 모든 구역이 XY축에 "
           "나란하면 안 된다.\n"
           "- 같은 프랍을 여러 번 놓을 때 스케일 지터 0.1~0.25를 준다.\n\n")
        + "## 플랜 (확정본)\n"
        f"```json\n{plan_text}\n```\n\n"
        "## 사용 가능한 키트 (이 key만 존재한다)\n"
        f"{_kit_manifest_table(kit_manifest)}\n\n"
        f"사용 가능한 key: {keys}\n"
        "`lp.kit`에는 위 표의 **오브젝트 이름**을 그대로 넘겨라 (key는 플랜과 대조하는 용도다). "
        "명단에 없는 에셋은 존재하지 않는다 — 빠진 에셋은 없는 셈 치고 구성을 조정하고, "
        "키트에 없는 프랍을 새로 모델링하지 마라.\n\n"
        "## 작성 순서\n"
        f"1. {shell}를 만든다 (색은 팔레트에서).\n"
        "2. 구조물을 만든다 — **울타리·난간·철조망은 반드시 `lp.fence_run`을 써라.** "
        "`lp.wall_run`은 속이 꽉 찬 벽면이라 울타리에 쓰면 판때기로 보인다. "
        "`lp.wall_run`은 성벽·건물 외벽·막힌 담장에만 쓴다. "
        "길·바닥 포장은 `lp.path_strip`, 계단·받침·표지판은 단순 `lp.box`다."
        + ("" if interior else " 길은 씬 가장자리에서 시작해 랜드마크로 향하게 하라.") + "\n"
        "3. 구역별로 `lp.kit(\"오브젝트 이름\")`으로 원본을 가져와 `lp.place_grid` / `lp.place_along` / "
        "`lp.place_scatter` / `lp.instance`로 배치한다. 회전·스케일 지터로 변주하라.\n"
        + ("4. 실내이므로 `lp.ground_snap`은 호출하지 않는다 (바닥이 평평하다).\n\n"
           if interior else
           "4. 마지막에 `lp.ground_snap(모든 인스턴스 리스트, 지형 또는 [지형, 고지대, ...])`을 **한 번** 호출한다.\n\n")
        + "`lp.set_color`는 이 턴에서 새로 만든 오브젝트(지형·방·벽·울타리·길·box)에만 쓴다. "
        "인스턴스에 칠하면 공유 메시의 UV가 바뀌어 원본과 나머지 인스턴스까지 전부 같은 색이 된다 — "
        "키트 에셋의 색은 이미 정해져 있으니 건드리지 마라.\n\n"
        + (f"씬 전체 트라이 예산: {tri_budget}. 초과가 예상되면 프랍 개수와 지형 셀 수를 먼저 줄여라 "
           "(랜드마크는 마지막까지 지킨다).\n"
           if tri_budget and tri_budget > 0 else
           "씬 전체 트라이 예산에는 상한이 없다. 개수를 아끼지 말고 위의 배치 총량 기준을 채워라.\n")
        + "출력 형식: 첫 줄 `STATUS: DONE` + python 코드 블록 정확히 1개, 200줄 이내."
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
