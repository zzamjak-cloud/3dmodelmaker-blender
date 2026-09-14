# 배경 모드 플랜 JSON: 추출 · 파싱 · 검증 · 정규화 (bpy 무의존 순수 모듈)
#
# 플랜 턴에서 Astra가 돌려준 JSON은 신뢰할 수 없다 — 키가 빠지거나, 존재하지 않는
# 구역을 참조하거나, 트라이 예산을 훌쩍 넘는 개수를 적어 온다. 형식 오류만 재요청하고
# 고칠 수 있는 값은 여기서 조용히 바로잡은 뒤, 무엇을 고쳤는지 경고로 남긴다.
import copy
import json

# size_class별 에셋 1개의 트라이 상한
SIZE_CLASS_TRI = {"L": 6000, "M": 2500, "S": 800}
# 씬 규모별 한 변 길이(m)
SCENE_SIZE_M = {"S": 20.0, "M": 40.0, "L": 80.0}
# 지형·구조물이 쓸 트라이 여유분
TERRAIN_RESERVE_TRI = 2000

_DEFAULT_SIZE_CLASS = "M"
_DEFAULT_PALETTE = ["#7fa855", "#c2a06a", "#8fb8d8", "#e4dcc4", "#d9603f"]
_MIN_PALETTE = 3
_MAX_PALETTE = 6
_HEX_DIGITS = "0123456789abcdef"


class PlanError(Exception):
    """플랜 JSON을 쓸 수 없을 때 — 호출자는 1회 재요청 후 실패 처리한다."""


def extract_json_block(text: str):
    """응답 텍스트에서 마지막 json 펜스 블록의 본문을 돌려준다 (없으면 None).

    언어 태그가 `json`인 블록을 우선하고, 태그가 없더라도 본문이 `{`로 시작하면 받아준다
    (모델이 태그를 자주 빠뜨린다). agents/parsing.py와 같은 ``` 분할 방식이다."""
    if not text:
        return None

    tagged, untagged = None, None
    parts = text.split("```")
    for i in range(1, len(parts), 2):
        block = parts[i]
        newline_at = block.find("\n")
        if newline_at == -1:
            body, lang = block.strip(), ""
        else:
            lang = block[:newline_at].strip().lower()
            body = block[newline_at + 1:].strip()
        if lang == "json":
            tagged = body
        elif not lang and body.startswith("{"):
            untagged = body
    return tagged if tagged is not None else untagged


def parse_plan(text: str) -> dict:
    """응답 텍스트에서 플랜 JSON을 꺼내 dict로 만든다. 실패하면 PlanError."""
    block = extract_json_block(text)
    if not block:
        raise PlanError("응답에서 json 코드 블록을 찾지 못했다")
    try:
        plan = json.loads(block)
    except ValueError as e:
        raise PlanError("플랜 JSON 파싱 실패: %s" % e)
    if not isinstance(plan, dict):
        raise PlanError("플랜 JSON의 최상위는 객체(dict)여야 한다")
    return plan


def asset_tri_limit(size_class) -> int:
    """size_class(L/M/S)에 해당하는 에셋 1개의 트라이 상한."""
    return SIZE_CLASS_TRI.get(_size_class(size_class), SIZE_CLASS_TRI[_DEFAULT_SIZE_CLASS])


def estimated_tris(plan: dict) -> int:
    """Σ(개수 × size_class 상한) + 지형 여유 — 플랜 단계의 예산 추정치."""
    total = TERRAIN_RESERVE_TRI
    for asset in plan.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        total += _positive_int(asset.get("count"), 1) * asset_tri_limit(asset.get("size_class"))
    return total


def normalize(plan: dict, tri_budget: int, max_assets: int):
    """플랜을 검증·보정한 사본과 한국어 경고 목록을 돌려준다.

    필수 키가 없거나 구조 자체가 틀린 경우에만 PlanError를 던지고, 값 수준의 문제
    (알 수 없는 size_class, 없는 구역 참조, 예산 초과 등)는 고친 뒤 경고로 남긴다."""
    if not isinstance(plan, dict):
        raise PlanError("플랜은 객체(dict)여야 한다")
    plan = copy.deepcopy(plan)
    warnings = []

    for key in ("scene", "zones", "assets"):
        if key not in plan:
            raise PlanError("플랜에 필수 키 '%s'가 없다" % key)
    if not isinstance(plan["scene"], dict):
        raise PlanError("'scene'은 객체(dict)여야 한다")
    if not isinstance(plan["zones"], list) or not plan["zones"]:
        raise PlanError("'zones'는 비어 있지 않은 배열이어야 한다")
    if not isinstance(plan["assets"], list) or not plan["assets"]:
        raise PlanError("'assets'는 비어 있지 않은 배열이어야 한다")

    _normalize_scene(plan, warnings)
    _normalize_terrain(plan, warnings)
    zone_names = _normalize_zones(plan, warnings)
    _normalize_assets(plan, zone_names, warnings)
    _mark_landmark(plan, warnings)
    _limit_asset_kinds(plan, max_assets, warnings)
    _fit_budget(plan, tri_budget, warnings)
    _normalize_rules(plan)
    return plan, warnings


# --- 내부 유틸 ---------------------------------------------------------------

def _size_class(value) -> str:
    return str(value or "").strip().upper()


def _positive_int(value, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number >= 1 else fallback


def _to_float(value, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _pair(value, fallback):
    """[x, y] 형태로 강제한다 — 길이가 다르거나 숫자가 아니면 기본값."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return [_to_float(value[0], fallback[0]), _to_float(value[1], fallback[1])]
    return list(fallback)


def _slug(text: str, fallback: str) -> str:
    """영문 소문자·숫자·밑줄만 남긴 슬러그 (한글 등은 버린다)."""
    out = []
    for ch in str(text or "").strip().lower():
        if ch.isascii() and (ch.isalnum()):
            out.append(ch)
        elif ch in (" ", "_", "-"):
            out.append("_")
    slug = "".join(out).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or fallback


def _dedupe(name: str, taken) -> str:
    """이미 쓰인 이름이면 겹치지 않을 때까지 _2, _3... 접미어를 올린다.

    한 번만 붙이면 'a'가 세 번 나올 때 'a_2'가 또 겹친다 — 중복 구역·에셋 key는
    배치 턴에서 엉뚱한 오브젝트를 부르게 하므로 반드시 유일해야 한다."""
    if name not in taken:
        return name
    n = 2
    while "%s_%d" % (name, n) in taken:
        n += 1
    return "%s_%d" % (name, n)


def _is_hex_color(value) -> bool:
    text = str(value or "").strip().lower()
    if len(text) != 7 or not text.startswith("#"):
        return False
    return all(ch in _HEX_DIGITS for ch in text[1:])


def _normalize_scene(plan: dict, warnings: list):
    scene = plan["scene"]
    size = _size_class(scene.get("size"))
    if size not in SCENE_SIZE_M:
        if scene.get("size"):
            warnings.append("씬 규모 '%s'를 알 수 없어 M으로 대체했다" % scene.get("size"))
        size = "M"
    scene["size"] = size

    raw = scene.get("palette")
    palette, dropped = [], 0
    for color in raw if isinstance(raw, list) else []:
        if _is_hex_color(color):
            text = str(color).strip().lower()
            if text not in palette:
                palette.append(text)
        else:
            dropped += 1
    if dropped:
        warnings.append("팔레트에서 hex 형식이 아닌 색 %d개를 제거했다" % dropped)
    if len(palette) > _MAX_PALETTE:
        warnings.append("팔레트가 %d색이라 %d색으로 줄였다" % (len(palette), _MAX_PALETTE))
        palette = palette[:_MAX_PALETTE]
    if len(palette) < _MIN_PALETTE:
        for color in _DEFAULT_PALETTE:
            if len(palette) >= _MIN_PALETTE:
                break
            if color not in palette:
                palette.append(color)
        warnings.append("팔레트 색이 부족해 기본 캐주얼 팔레트로 보충했다")
    scene["palette"] = palette
    scene["mood"] = str(scene.get("mood") or "")


def _normalize_terrain(plan: dict, warnings: list):
    terrain = plan.get("terrain")
    if not isinstance(terrain, dict):
        if terrain is not None:
            warnings.append("'terrain' 형식이 잘못되어 기본값으로 대체했다")
        terrain = {}
    relief = _to_float(terrain.get("relief"), 0.4)
    if relief < 0.0 or relief > 1.5:
        warnings.append("지형 릴리프 %.2f를 0.0~1.5 범위로 잘랐다" % relief)
        relief = min(max(relief, 0.0), 1.5)
    terrain["relief"] = relief
    terrain["style"] = str(terrain.get("style") or "")
    plan["terrain"] = terrain


def _normalize_zones(plan: dict, warnings: list) -> list:
    zones, names = [], []
    for i, zone in enumerate(plan["zones"]):
        if not isinstance(zone, dict):
            warnings.append("구역 %d번이 객체가 아니어서 제외했다" % (i + 1))
            continue
        name = _dedupe(_slug(zone.get("name"), "zone%d" % (i + 1)), names)
        names.append(name)
        zones.append({
            "name": name,
            "center": _pair(zone.get("center"), (0.0, 0.0)),
            "extent": _pair(zone.get("extent"), (10.0, 10.0)),
            "purpose": str(zone.get("purpose") or ""),
        })
    if not zones:
        raise PlanError("쓸 수 있는 구역(zone)이 하나도 없다")
    plan["zones"] = zones
    return names


def _normalize_assets(plan: dict, zone_names: list, warnings: list):
    assets, keys = [], []
    for i, asset in enumerate(plan["assets"]):
        if not isinstance(asset, dict):
            warnings.append("에셋 %d번이 객체가 아니어서 제외했다" % (i + 1))
            continue
        raw_key = str(asset.get("key") or "").strip()
        key = _slug(raw_key, "asset%d" % (i + 1))
        if raw_key and key != raw_key.lower():
            warnings.append("에셋 key '%s'를 영문 슬러그 '%s'로 바꿨다" % (raw_key, key))
        new_key = _dedupe(key, keys)
        if new_key != key:
            warnings.append("에셋 key '%s'가 중복이라 '%s'로 바꿨다" % (key, new_key))
            key = new_key
        keys.append(key)

        size_class = _size_class(asset.get("size_class"))
        if size_class not in SIZE_CLASS_TRI:
            if asset.get("size_class"):
                warnings.append("에셋 '%s'의 size_class를 알 수 없어 M으로 대체했다" % key)
            size_class = _DEFAULT_SIZE_CLASS

        zone = _slug(asset.get("zone"), "")
        if zone not in zone_names:
            warnings.append("에셋 '%s'가 없는 구역을 가리켜 '%s'로 옮겼다" % (key, zone_names[0]))
            zone = zone_names[0]

        prompt = str(asset.get("prompt") or "").strip()
        if not prompt:
            prompt = key.replace("_", " ")
            warnings.append("에셋 '%s'에 prompt가 없어 key를 요청문으로 썼다" % key)

        assets.append({
            "key": key,
            "prompt": prompt,
            "count": _positive_int(asset.get("count"), 1),
            "size_class": size_class,
            "zone": zone,
            "landmark": bool(asset.get("landmark")),
        })
    if not assets:
        raise PlanError("쓸 수 있는 에셋이 하나도 없다")
    plan["assets"] = assets


def _mark_landmark(plan: dict, warnings: list):
    assets = plan["assets"]
    if any(a["landmark"] for a in assets):
        return
    target = next((a for a in assets if a["size_class"] == "L"), assets[0])
    target["landmark"] = True
    warnings.append("랜드마크가 없어 '%s'를 랜드마크로 지정했다" % target["key"])


def _limit_asset_kinds(plan: dict, max_assets: int, warnings: list):
    assets = plan["assets"]
    limit = _positive_int(max_assets, len(assets))
    if len(assets) <= limit:
        return
    ranked = sorted(assets, key=lambda a: (not a["landmark"], -a["count"]))
    keep = set(a["key"] for a in ranked[:limit])
    dropped = [a["key"] for a in assets if a["key"] not in keep]
    plan["assets"] = [a for a in assets if a["key"] in keep]  # 원래 순서는 유지
    warnings.append("에셋 종류가 상한(%d종)을 넘어 %s를 제외했다" % (limit, ", ".join(dropped)))


def _fit_budget(plan: dict, tri_budget: int, warnings: list):
    budget = _positive_int(tri_budget, 0)
    if not budget or estimated_tris(plan) <= budget:
        return
    assets = plan["assets"]
    landmark_tris = sum(a["count"] * asset_tri_limit(a["size_class"])
                        for a in assets if a["landmark"])
    others = [a for a in assets if not a["landmark"]]
    other_tris = sum(a["count"] * asset_tri_limit(a["size_class"]) for a in others)
    available = budget - landmark_tris - TERRAIN_RESERVE_TRI

    if others and other_tris > 0 and available > 0:
        factor = float(available) / float(other_tris)
        for asset in others:
            asset["count"] = max(1, int(asset["count"] * factor))
    elif others:
        for asset in others:
            asset["count"] = 1
    warnings.append("트라이 예산(%d)을 넘어 반복 프랍 개수를 줄였다" % budget)

    remain = estimated_tris(plan)
    if remain > budget:
        warnings.append("축소 후에도 예상 트라이가 %d로 예산 %d를 넘는다 — 배치 턴에서 밀도를 더 낮춰야 한다"
                        % (remain, budget))


def _normalize_rules(plan: dict):
    rules = plan.get("rules")
    plan["rules"] = [str(r) for r in rules if str(r).strip()] if isinstance(rules, list) else []
