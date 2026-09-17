# 배경 세션의 에셋 키트 집계 산술 (bpy 무의존 순수 모듈)
#
# 자식 에셋 잡의 성공/실패 집계, 중단 임계 판정, 키트 명단(manifest) 산출은
# 전부 bpy 호출에 붙어 있으면 테스트가 볼 수 없다. 판정 규칙이 틀리면 씬 하나가
# 통째로 날아가거나(과도한 중단) 랜드마크 없는 빈 벌판이 나오므로(관대한 통과)
# 산술만 여기로 떼어내 유닛 테스트로 고정한다.
from . import scene_plan

# 레인 간격에 더하는 여백(m) — 옆 레인 씬의 프랍이 경계에 닿지 않게 한다
LANE_MARGIN_M = 8.0


def kit_status_text(done: int, total: int) -> str:
    """부모 잡 상태줄에 쓰는 진행 문구."""
    return "에셋 키트 생성 %d/%d" % (int(done), int(total))


def kit_verdict(total: int, landmark_uids, results: dict):
    """자식 집계 상태를 판정한다. ('RUNNING'|'FAILED'|'DONE', 사유)를 반환.

    results는 {자식 uid: 성공 여부}다. 아직 끝나지 않은 자식은 키가 없다.
    실패가 절반을 넘거나 랜드마크가 전멸하면 남은 자식을 기다릴 이유가 없다 —
    나머지가 다 성공해도 쓸 만한 씬이 나오지 않는다."""
    total = int(total)
    landmark_uids = list(landmark_uids or [])
    failed = [uid for uid, ok in results.items() if not ok]
    if total and len(failed) * 2 > total:
        return 'FAILED', "에셋 %d/%d종이 실패해 절반을 넘었다" % (len(failed), total)
    if landmark_uids and all(uid in results and not results[uid] for uid in landmark_uids):
        return 'FAILED', "랜드마크 에셋 %d종이 모두 실패했다" % len(landmark_uids)
    if len(results) >= total:
        return 'DONE', ""
    return 'RUNNING', ""


def unique_name(base: str, taken) -> str:
    """키트 안에서 겹치지 않는 오브젝트 이름 (겹치면 _2, _3... 접미어).

    Blender는 전역 이름 충돌을 .001로 알아서 피하지만, 그러면 배치 턴에 넘길
    명단의 이름과 실제 이름이 어긋난다. 키트 내부 충돌은 여기서 먼저 없앤다."""
    base = str(base or "asset").strip() or "asset"
    if base not in taken:
        return base
    n = 2
    while "%s_%d" % (base, n) in taken:
        n += 1
    return "%s_%d" % (base, n)


def bbox_size(corners) -> tuple:
    """8개 bbox 꼭짓점 좌표에서 (x, y, z) 크기(m)를 구한다."""
    pts = [(float(c[0]), float(c[1]), float(c[2])) for c in corners or []]
    if not pts:
        return (0.0, 0.0, 0.0)
    return tuple(round(max(p[i] for p in pts) - min(p[i] for p in pts), 3)
                 for i in range(3))


def manifest_entry(key: str, obj_name: str, size, tri: int) -> dict:
    """배치 턴 프롬프트(prompts.build_scene_place_prompt)가 읽는 명단 한 줄."""
    return {
        "key": str(key),
        "obj_name": str(obj_name),
        "size": tuple(float(v) for v in size),
        "tri": int(tri),
    }


def manifest_keys(manifest) -> list:
    return [item.get("key", "") for item in manifest or []]


def dropped_assets(plan: dict, manifest) -> list:
    """플랜에는 있으나 키트에 들어가지 못한 에셋 key 목록."""
    present = set(manifest_keys(manifest))
    return [a.get("key") for a in (plan or {}).get("assets") or []
            if a.get("key") not in present]


def prune_plan(plan: dict, manifest) -> dict:
    """키트에 실제로 존재하는 에셋만 남긴 플랜 사본. 배치 턴에 이것을 넘긴다."""
    if not isinstance(plan, dict):
        return {}
    present = set(manifest_keys(manifest))
    pruned = dict(plan)
    pruned["assets"] = [a for a in plan.get("assets") or [] if a.get("key") in present]
    return pruned


def plan_summary(plan: dict) -> str:
    """플랜 확정 직후 로그에 남길 한 줄 요약."""
    zones = len((plan or {}).get("zones") or [])
    assets = (plan or {}).get("assets") or []
    total = sum(int(a.get("count") or 1) for a in assets)
    scene = (plan or {}).get("scene") or {}
    extent = scene.get("extent") or []
    shape = ""
    if len(extent) == 2:
        shape = " 부지 %.0fx%.0fm%s," % (float(extent[0]), float(extent[1]),
                                        " 비정형" if scene.get("outline") else "")
    return ("플랜 확정:%s 구역 %d개, 에셋 %d종(배치 %d개), 예상 %d tris"
            % (shape, zones, len(assets), total, scene_plan.estimated_tris(plan or {})))


def scene_spacing(scene_size: str, plan: dict = None) -> float:
    """배경 잡의 레인 간격(m) — 부지 긴 변 + 여백. 기본 4m로는 옆 레인과 겹친다.

    플랜에 extent가 있으면 그 긴 변을 쓴다 — 부지가 더는 정사각형이 아니어서
    규모의 한 변만으로는 옆 레인과 겹칠 수 있다."""
    size = str(scene_size or "M").strip().upper()
    side = scene_plan.SCENE_SIZE_M.get(size, scene_plan.SCENE_SIZE_M["M"])
    extent = ((plan or {}).get("scene") or {}).get("extent") or []
    if len(extent) == 2:
        try:
            side = max(side, float(extent[0]), float(extent[1]))
        except (TypeError, ValueError):
            pass
    return side + LANE_MARGIN_M


def scaled_timeout(base_timeout, scale) -> int:
    """배경 턴용 CLI 타임아웃 — 배수가 이상해도 기본값 아래로는 내려가지 않는다."""
    try:
        base = int(base_timeout)
    except (TypeError, ValueError):
        base = 300
    try:
        factor = float(scale)
    except (TypeError, ValueError):
        factor = 1.0
    return max(base, int(base * max(factor, 1.0)))
