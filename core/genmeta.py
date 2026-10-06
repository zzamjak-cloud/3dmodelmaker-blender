"""생성 정보를 결과 자체(컬렉션·원본 메시)에 새겨 .blend 와 함께 남긴다.

생성 큐(scene.lp3d.jobs)는 씬에 붙은 값이라, 결과만 담은 임시 씬으로 쓰는 자동 저장 .blend 나
결과를 다른 파일로 옮긴 경우에는 따라가지 않는다. 그러면 파일을 다시 열었을 때 결과는 있는데 큐 항목이
없어 리토폴로지·익스포트 같은 후처리를 누를 수 없었다. 완성 시점에 잡 입력·결과 정보를 JSON 으로
결과 컬렉션과 원본 메시의 커스텀 프로퍼티에 새겨 두고, 파일을 열 때 큐 항목이 없는 결과를 찾아 되살린다.
"""
import json
import logging
import time

log = logging.getLogger(__name__)

META_KEY = "lp3d_gen"
DISMISSED_KEY = "lp3d_gen_dismissed"   # 사용자가 큐에서 지운 결과 — 파일을 열 때 다시 되살리지 않는다
META_VERSION = 1

# 되살릴 잡 필드 — 입력값과 결과 경로·모델 추적 정보. 실행 상태(state·phase·started_at)는 담지 않는다
FIELDS = (
    "prompt", "ref_image_path", "creation_mode", "front_image", "character_type", "scene_size",
    "style", "modeling_type", "status", "log", "collection_name", "code", "entry_id",
    "multiview_path", "texture_path", "image_backend", "lane",
    "requested_model", "effective_model", "model_fallback", "model_routing",
)


def build(job) -> dict:
    """잡 항목에서 새길 정보를 뽑는다."""
    data = {"version": META_VERSION, "stamped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "uid": int(getattr(job, "uid", 0) or 0)}
    for name in FIELDS:
        if hasattr(job, name):
            value = getattr(job, name)
            data[name] = value if isinstance(value, (bool, int, float)) else str(value)
    return data


def read(id_block) -> dict:
    """ID(컬렉션·오브젝트)에 새겨진 생성 정보. 없거나 깨졌으면 빈 dict."""
    if id_block is None:
        return {}
    raw = id_block.get(META_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _stamp_targets(collection):
    """정보를 새길 오브젝트 — 컬렉션 바로 아래의 원본 메시. 리토폴로지 결과·옛 보존본은 제외한다."""
    from ..lowpoly import quadretopo
    return [obj for obj in collection.objects
            if obj.type == 'MESH' and obj.parent is None
            and not obj.get(quadretopo.RETOPO_OF_KEY) and not obj.get(quadretopo.SOURCE_KEY)]


def stamp(job, collection_name: str = "") -> bool:
    """잡 정보를 결과 컬렉션과 그 원본 메시에 새긴다. 새길 곳이 없으면 False.

    실패해도 생성 결과를 실패로 돌리면 안 되므로 예외는 로그만 남긴다."""
    import bpy
    if job is None or getattr(job, "parent_uid", ""):
        return False   # 배경 에셋 잡의 결과는 부모 키트로 합쳐진다 — 부모가 새긴다
    name = collection_name or getattr(job, "collection_name", "")
    collection = bpy.data.collections.get(name) if name else None
    if collection is None:
        return False
    try:
        data = build(job)
        data["collection_name"] = collection.name
        text = json.dumps(data, ensure_ascii=False)
        collection[META_KEY] = text
        if DISMISSED_KEY in collection:
            del collection[DISMISSED_KEY]
        targets = _stamp_targets(collection)
        # 배경·파트 조립처럼 메시가 여럿이면 컬렉션에만 새긴다 — 긴 코드·로그를 메시마다 복제하지 않게
        if data.get("creation_mode") == 'CHARACTER' or len(targets) == 1:
            for obj in targets:
                obj[META_KEY] = text
        return True
    except Exception:
        log.exception("생성 정보 기록 실패: %s", name)
        return False


def _scene_collections(scene) -> list:
    return list(scene.collection.children_recursive)


def dismiss(collection_name: str) -> None:
    """큐에서 지운 항목의 결과 표시 — 생성 정보는 그대로 두고, 파일을 열 때 항목을 되살리지 않게만 한다."""
    import bpy
    collection = bpy.data.collections.get(collection_name) if collection_name else None
    if collection is not None and META_KEY in collection:
        collection[DISMISSED_KEY] = True


def orphan_results(scene, props) -> list:
    """큐 항목이 가리키지 않는 생성 결과 (컬렉션, 정보) 목록.

    컬렉션에 새겨진 정보가 우선이고, 오브젝트만 다른 컬렉션으로 옮겨진 경우에는 오브젝트의 정보로
    그 오브젝트가 든 컬렉션을 결과로 본다. 이름이 바뀐 결과 컬렉션은 uid 로 알아보고 그 항목의 이름을 고친다."""
    import bpy
    from ..lowpoly import quadretopo
    # 같은 컬렉션이 여러 씬에 링크될 수 있다 — 어느 씬의 큐든 이미 가리키면 추적 중이다
    tracked = {job.collection_name for sc in bpy.data.scenes if getattr(sc, "lp3d", None)
               for job in sc.lp3d.jobs if job.collection_name}
    by_uid = {job.uid: job for job in props.jobs}
    stamped = {c.get(META_KEY) for c in bpy.data.collections if c.get(META_KEY)}
    found, seen = [], set()
    for coll in _scene_collections(scene):
        if coll.name in tracked or coll.get(quadretopo.RETOPO_OF_KEY) or coll.get(DISMISSED_KEY):
            continue
        data = read(coll)
        if not data:
            for obj in coll.objects:
                if obj.get(quadretopo.RETOPO_OF_KEY):
                    continue
                # 복제(Shift+D)로 정보가 따라온 메시가 아니라, 원래 결과 컬렉션이 사라진 경우만 인정한다 —
                # 같은 정보를 가진 컬렉션이 남아 있으면(이름이 바뀌었어도) 그 결과의 사본이다
                if obj.get(META_KEY) in stamped:
                    continue
                data = read(obj)
                if data:
                    break
        if not data or coll.name in seen:
            continue
        owner = by_uid.get(data.get("uid"))
        if owner is not None and owner.collection_name not in bpy.data.collections:
            owner.collection_name = coll.name   # 컬렉션 이름만 바뀐 결과 — 새 항목 대신 기존 항목을 잇는다
            tracked.add(coll.name)
            continue
        seen.add(coll.name)
        found.append((coll, data))
    return found


def _assign(job, name, value) -> None:
    try:
        setattr(job, name, value)
    except (TypeError, ValueError, AttributeError):
        # 버전이 바뀌어 없어진 enum 값 등 — 그 필드만 기본값으로 둔다
        log.info("생성 정보 필드 복원 건너뜀: %s=%r", name, value)


def restore_job(props, collection, data: dict):
    """생성 정보로 완료 상태의 큐 항목을 되살린다."""
    from . import jobs
    selected = props.job_index
    job = jobs.add_job(props, mode=str(data.get("creation_mode") or 'OBJECT'))
    props.job_index = selected   # 파일을 열자마자 선택이 튀지 않게
    for name in FIELDS:
        if name in data and name not in ("creation_mode", "collection_name", "status", "lane"):
            _assign(job, name, data[name])
    job.collection_name = collection.name
    job.state = 'DONE'
    job.status = "파일에서 복원됨 — " + str(data.get("status") or "완료")
    stamped = data.get("stamped_at")
    job.log = "\n".join((str(data.get("log") or "") + f"\n생성 정보에서 복원 ({stamped or '시각 미상'})")
                        .strip().splitlines()[-30:])
    return job


def restore_jobs(scene) -> int:
    """이 씬에서 큐 항목이 없는 생성 결과를 찾아 항목을 되살린다. 되살린 개수."""
    props = getattr(scene, "lp3d", None)
    if props is None:
        return 0
    restored = 0
    for collection, data in orphan_results(scene, props):
        try:
            restore_job(props, collection, data)
            restored += 1
        except Exception:
            log.exception("큐 항목 복원 실패: %s", collection.name)
    return restored


def adoptable_collection(obj):
    """결과 항목으로 등록할 수 있는 메시면 그 컬렉션, 아니면 None.

    씬 최상위에 바로 놓인 메시는 결과 컬렉션 단위 후처리(익스포트·리토폴로지)가 성립하지 않아 제외한다."""
    import bpy
    from ..lowpoly import quadretopo
    if obj is None or obj.type != 'MESH' or obj.get(quadretopo.RETOPO_OF_KEY):
        return None
    masters = {scene.collection for scene in bpy.data.scenes}
    return next((c for c in obj.users_collection
                 if c not in masters and not c.get(quadretopo.RETOPO_OF_KEY)), None)


def adopt(props, obj, mode: str = 'CHARACTER'):
    """생성 정보가 없는 기존 메시(옛 버전 파일)를 결과 항목으로 등록하고 정보를 새긴다."""
    from . import jobs
    collection = adoptable_collection(obj)
    if collection is None:
        raise RuntimeError("씬 최상위가 아닌 컬렉션에 든 메시만 등록할 수 있습니다")
    data = read(collection) or read(obj)
    if data:
        if DISMISSED_KEY in collection:
            del collection[DISMISSED_KEY]
        return restore_job(props, collection, data)
    job = jobs.add_job(props, prompt=obj.name, mode=mode)
    job.collection_name = collection.name
    job.state = 'DONE'
    job.status = "기존 메시를 결과로 등록함"
    stamp(job)
    return job
