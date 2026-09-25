# 생성 큐(병렬 AI + 직렬 Blender)를 실제 Blender에서 검증한다 (헤들리스)
#
# 단위 테스트는 bpy 비의존 모듈(scheduler·lanes·snapshots 산술)만 덮는다.
# 잡 리스트·레인 배치·세션 수명처럼 bpy에 붙은 부분은 여기서만 확인할 수 있다.
# AI CLI는 부르지 않는다 — 돈·시간이 들고 네트워크에 의존한다. 대신 CLI 없이
# 도달 가능한 경로(자료구조, 레인 배치, 시작 거부, 종료 정리)만 훑는다.
#
# 실행:
#   .\scripts\dev_run.ps1 -Background -PythonFile tests\verify_queue_in_blender.py
#   ./scripts/dev_run.sh --background --python tests/verify_queue_in_blender.py
import os
import shutil
import tempfile

import bpy
from bl_ext.user_default.lp3d_modelmaker import lowpoly, preferences
from bl_ext.user_default.lp3d_modelmaker.core import (jobs, lanes, scene_kit,
                                                      scene_session, scheduler,
                                                      session)
from bl_ext.user_default.lp3d_modelmaker.pipeline import export

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond:
        fails.append(name)


# 저장된 scene 설정과 분리된 새 scene에서 기본값과 큐 동작을 확인한다.
original_scene = bpy.context.window.scene
probe_scene = bpy.data.scenes.new("LP3D_DefaultProbe")
try:
    bpy.context.window.scene = probe_scene
    props = probe_scene.lp3d

    # 1) 빈 큐 — 구버전 .blend를 열었을 때와 같은 상태다. 여기서 터지면 패널 전체가 죽는다
    check("빈 큐에서 active_job=None", props.active_job() is None)
    check("uid 조회 실패는 None", props.job_by_uid(999) is None)

    # 2) 잡 추가와 레인 배정 — 레인이 겹치면 배치 결과가 원점에 포개진다
    # 새 scene과 새 job에 제거된 에이전트 선택 속성이 남지 않아야 한다.
    check("새 scene 에이전트 선택 제거", not hasattr(props, "agent"))
    a = jobs.add_job(props, "나무 상자")
    check("새 job 에이전트 선택 제거", not hasattr(a, "agent"))
    check("새 job 생성 모델 추적값 초기 상태",
          not a.requested_model and not a.effective_model)
    check("새 job 비평 모델 추적값 제거",
          not hasattr(a, "requested_critique_model") and not hasattr(a, "effective_critique_model"))
    check("새 job fallback 초기값 false", a.model_fallback is False)
    b = jobs.add_job(props, "돌 항아리")
    c = jobs.add_job(props, "철제 랜턴")
    check("잡 3개 추가", len(props.jobs) == 3, str(len(props.jobs)))
    check("레인 충돌 없음", len({j.lane for j in props.jobs}) == 3,
          str(sorted(j.lane for j in props.jobs)))
    check("uid 순차 발급", {a.uid, b.uid, c.uid} == {1, 2, 3})
    check("uid 조회", props.job_by_uid(b.uid).prompt == "돌 항아리")

    # 3) 복제 — props.jobs.add()가 컬렉션을 재할당해도 원본 참조로 크래시하지 않아야 한다
    dup = jobs.duplicate_job(props, 0)
    check("복제 성공", dup is not None and len(props.jobs) == 4)
    check("복제본 프롬프트 승계", dup.prompt == "나무 상자", dup.prompt)
    check("복제본 레인 충돌 없음", len({j.lane for j in props.jobs}) == 4,
          str(sorted(j.lane for j in props.jobs)))

    # 4) 순서 이동 — 범위 밖 요청은 조용히 무시해야 한다 (버튼은 항상 눌린다)
    first = props.jobs[0].uid
    jobs.move_job(props, 0, 1)
    check("아래로 이동", props.jobs[1].uid == first)
    jobs.move_job(props, 1, -1)
    check("위로 이동 원복", props.jobs[0].uid == first)
    n = len(props.jobs)
    jobs.move_job(props, 0, -1)
    jobs.move_job(props, n - 1, 1)
    check("범위 밖 이동 무시", len(props.jobs) == n)

    # 5) 삭제 — job_index가 범위를 벗어나면 패널 draw가 매 프레임 터진다
    jobs.remove_job(bpy.context, len(props.jobs) - 1)
    check("삭제 반영", len(props.jobs) == n - 1)
    check("job_index 범위 유지", 0 <= props.job_index < len(props.jobs), str(props.job_index))
    while len(props.jobs):
        jobs.remove_job(bpy.context, 0)
    check("전부 삭제 후 active_job=None", props.active_job() is None)

    # 6) 빈자리 배치 멱등성 — 재적용으로 모델이 중복 이동하지 않고, 이미 놓인 결과와 겹치지 않아야 한다
    import bmesh
    def _cube(name, coll_name):
        c = bpy.data.collections.new(coll_name)
        bpy.context.scene.collection.children.link(c)
        me = bpy.data.meshes.new(name)
        bm = bmesh.new(); bmesh.ops.create_cube(bm, size=2.0); bm.to_mesh(me); bm.free()
        o = bpy.data.objects.new(name, me)
        c.objects.link(o)
        return c, o
    coll_a, obj_a = _cube("LP3D_PlaceA", "LP3D_PlaceProbeA")
    bpy.context.view_layer.update()
    jobs.apply_lane_offset("LP3D_PlaceProbeA")
    coll_b, obj_b = _cube("LP3D_PlaceB", "LP3D_PlaceProbeB")
    bpy.context.view_layer.update()
    jobs.apply_lane_offset("LP3D_PlaceProbeB")
    once = obj_b.location.x
    check("둘째 결과는 첫 결과 옆 빈자리", once - 1.0 >= obj_a.location.x + 1.0 + 0.99, f"x={once}")
    jobs.apply_lane_offset("LP3D_PlaceProbeB")
    check("재적용은 이동 없음", abs(obj_b.location.x - once) < 1e-6, f"{once} -> {obj_b.location.x}")
    for c, o in ((coll_a, obj_a), (coll_b, obj_b)):
        bpy.data.objects.remove(o)
        bpy.data.collections.remove(c)

    # 7) 스케줄러·세션 유휴 상태
    counts = scheduler.counts()
    check("스케줄러 유휴", all(v == 0 for v in counts.values()), str(counts))
    check("세션 없음", session.is_active() is False and session.active_count() == 0)

    # 8) 시작 거부 — 프롬프트가 비면 세션을 만들지 않고 오류 문구를 돌려줘야 한다
    empty = jobs.add_job(props, "")
    error = session.start_job(bpy.context.scene.name, empty.uid)
    check("빈 프롬프트 거부", bool(error), repr(error))
    check("거부 후 세션 잔재 없음", session.active_count() == 0)
    jobs.remove_job(bpy.context, len(props.jobs) - 1)

    # 9) 교착 해제 — Dev Reload·파일 다시 열기로 세션만 사라지고 RUNNING이 남는 상황
    stuck = jobs.add_job(props, "좀비 확인")
    stuck.state = 'RUNNING'
    stuck.requested_model = "GPT-6 Astra"
    stuck.effective_model = "Codex CLI 기본 모델"
    stuck.model_fallback = True
    check("reset_stale 1건 처리", jobs.reset_stale(props) == 1)
    check("RUNNING -> PENDING", stuck.state == 'PENDING', stuck.state)
    check("reset_stale 생성 모델 추적값 초기화",
          not stuck.requested_model and not stuck.effective_model)
    check("reset_stale fallback 초기화", stuck.model_fallback is False)
    jobs.remove_job(bpy.context, len(props.jobs) - 1)

    # 10) 유휴 상태 shutdown — Dev Reload가 살아있는 세션 없이도 안전해야 한다
    session.shutdown()
    check("유휴 shutdown 무해", session.active_count() == 0)

    # 11) 배경 잡 라우팅 — SCENE 최상위 잡만 배경 상태머신을 써야 한다.
    # CLI는 부르지 않는다: start()를 무력화하고 어떤 세션 객체가 만들어지는지만 본다.
    original_resolve = preferences.resolve_cli_path
    original_start = session.GenerationSession.start
    original_scene_start = scene_session.SceneSession.start
    preferences.resolve_cli_path = lambda agent='CODEX': "/usr/bin/true"
    session.GenerationSession.start = lambda self: None
    scene_session.SceneSession.start = lambda self: None
    try:
        scene_job = jobs.add_job(props, "포로 수용소")
        scene_job.creation_mode = 'SCENE'
        scene_job.scene_size = 'L'
        scene_uid = scene_job.uid
        error = session.start_job(probe_scene.name, scene_uid)
        routed = session._sessions.get(scene_uid)
        check("SCENE 잡은 SceneSession으로 라우팅",
              isinstance(routed, scene_session.SceneSession), repr(error))
        check("배경 세션이 씬 규모를 고정",
              getattr(routed, "scene_size", "") == 'L')
        check("배경 세션 타임아웃 배수 적용",
              getattr(routed, "timeout", 0) >= preferences.get_prefs().timeout,
              str(getattr(routed, "timeout", 0)))
        session._sessions.pop(scene_uid, None)

        obj_uid = jobs.add_job(props, "나무 상자").uid
        session.start_job(probe_scene.name, obj_uid)
        plain = session._sessions.get(obj_uid)
        check("OBJECT 잡은 기존 세션 유지",
              plain is not None and not isinstance(plain, scene_session.SceneSession))
        session._sessions.pop(obj_uid, None)

        # add_child_job은 부모 뒤로 항목을 옮긴다 — 옮긴 뒤의 참조가 맞아야
        # 부모 세션이 엉뚱한 잡을 자식으로 구동하지 않는다
        child = jobs.add_child_job(props, scene_uid, "감시탑")
        child_uid = child.uid
        check("자식 항목이 부모를 가리킴", child.parent_uid == str(scene_uid),
              f"{child.parent_uid} vs {scene_uid}")
        check("자식이 부모 바로 뒤에 놓임",
              [j.uid for j in props.jobs] == [scene_uid, child_uid, obj_uid],
              str([j.uid for j in props.jobs]))
        session.start_job(probe_scene.name, child_uid, multiview_override=True)
        child_session = session._sessions.get(child_uid)
        check("에셋 자식은 오브젝트 세션",
              child_session is not None
              and not isinstance(child_session, scene_session.SceneSession))
        check("자식 세션이 부모 uid를 기억",
              getattr(child_session, "parent_uid", "") == str(scene_uid))
        check("멀티뷰 오버라이드 적용", child_session._use_multiview() is True)
        session._sessions.pop(child_uid, None)
    finally:
        preferences.resolve_cli_path = original_resolve
        session.GenerationSession.start = original_start
        scene_session.SceneSession.start = original_scene_start
    jobs.remove_job(bpy.context, next(i for i, j in enumerate(props.jobs)
                                      if j.uid == scene_uid))
    check("부모 삭제로 자식도 사라짐",
          [j.uid for j in props.jobs] == [obj_uid], str([j.uid for j in props.jobs]))
    jobs.remove_job(bpy.context, next(i for i, j in enumerate(props.jobs)
                                      if j.uid == obj_uid))

    # 12) 키트 정리 — 자식 결과를 합쳐 키트 컬렉션으로 옮기고 명단을 만든다.
    # 배치 턴(_start_place)은 CLI 호출이므로 막아두고 bpy 경로만 확인한다.
    parent = jobs.add_job(props, "포로 수용소")
    parent.creation_mode = 'SCENE'
    parent.scene_size = 'M'
    parent_uid = parent.uid
    # 앞선 세션이 크래시로 남긴 동명의 키트를 재사용하면 남의 에셋을 배치하게 된다
    stale_kit = bpy.data.collections.new("LP3D_Model" + scene_session.KIT_SUFFIX)
    probe_scene.collection.children.link(stale_kit)
    kit_session = scene_session.SceneSession(
        scene_name=probe_scene.name, uid=parent_uid, request="포로 수용소",
        exe="/usr/bin/true", lane=1, scene_size='M')
    check("잔존 키트 컬렉션을 재사용하지 않음",
          kit_session.kit_collection_name != stale_kit.name,
          kit_session.kit_collection_name)
    bpy.data.collections.remove(stale_kit)
    kit_session.stage = 'KIT'
    kit_session._start_place = lambda: None  # 배치 턴 진입 차단
    kit_session.plan = {
        "scene": {"size": "M", "palette": ["#6f7a5a", "#8a7a5c", "#b8ae95"]},
        "zones": [{"name": "yard", "center": [0, 0], "extent": [20, 20], "purpose": "연병장"}],
        "assets": [
            {"key": "watchtower", "prompt": "감시탑", "count": 1, "size_class": "L",
             "zone": "yard", "landmark": True},
            {"key": "barrel", "prompt": "드럼통", "count": 4, "size_class": "S",
             "zone": "yard", "landmark": False},
            {"key": "crate", "prompt": "나무 상자", "count": 2, "size_class": "S",
             "zone": "yard", "landmark": False},
        ],
        "rules": [],
    }

    def make_child(key, part_count):
        """자식 에셋 잡 하나가 끝난 상태를 만들고 uid를 돌려준다."""
        item = jobs.add_child_job(props, parent_uid, key)
        coll_name = f"LP3D_child_{key}"
        coll = bpy.data.collections.new(coll_name)
        probe_scene.collection.children.link(coll)
        for i in range(part_count):
            mesh = bpy.data.meshes.new(f"{coll_name}_{i}")
            mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)], [],
                             [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)])
            mesh.update()
            part = bpy.data.objects.new(f"{coll_name}_{i}", mesh)
            part.location = (i * 2.0, 0.0, 0.0)
            coll.objects.link(part)
        item.collection_name = coll_name
        item.state = 'DONE'
        return item.uid

    tower = make_child("watchtower", 3)   # 여러 파트 → join으로 1개가 되어야 한다
    barrel = make_child("barrel", 1)
    crate = make_child("crate", 1)
    kit_session.children = {tower: "watchtower", barrel: "barrel", crate: "crate"}
    kit_session.landmark_uids = [tower]
    kit_session.child_results = {tower: True, barrel: True, crate: False}

    kit_session._blender_collect_kit()
    kit_coll = bpy.data.collections.get(kit_session.kit_collection_name)
    check("키트 컬렉션 생성", kit_coll is not None, kit_session.kit_collection_name)
    check("키트 컬렉션은 씬 최상위(세션 컬렉션의 형제)",
          kit_coll is not None and kit_coll.name in probe_scene.collection.children)
    names = sorted(o.name for o in kit_coll.objects) if kit_coll else []
    check("성공 에셋만 키트에 들어감", names == ["barrel", "watchtower"], str(names))
    check("여러 파트는 1개로 병합",
          kit_coll is not None
          and len([o for o in kit_coll.objects if o.name == "watchtower"]) == 1)
    check("실패 에셋은 명단에서 제외",
          sorted(scene_kit.manifest_keys(kit_session.kit_manifest)) == ["barrel", "watchtower"],
          str(kit_session.kit_manifest))
    check("명단에 크기·트라이 기록",
          all(item["tri"] > 0 and max(item["size"]) > 0 for item in kit_session.kit_manifest),
          str(kit_session.kit_manifest))
    check("배치용 플랜에서 실패 에셋 제거",
          [a["key"] for a in kit_session.place_plan["assets"]] == ["watchtower", "barrel"])
    check("자식 컬렉션 전부 제거 (실패 에셋 포함)",
          all(bpy.data.collections.get(f"LP3D_child_{k}") is None
              for k in ("watchtower", "barrel", "crate")))
    # 자식 결과 이름을 키트로 바꿔두면 자식 항목 익스포트가 키트 전체를 내보낸다
    check("자식 항목 결과 이름은 비워둠",
          all(props.job_by_uid(u).collection_name == ""
              for u in (tower, barrel, crate)),
          str([props.job_by_uid(u).collection_name for u in (tower, barrel, crate)]))
    check("lp.kit이 키트에서 원본을 찾음",
          lowpoly.kit("barrel").name == "barrel")

    # 인스턴스는 메시를 공유해야 예산과 드로우콜이 늘지 않는다
    lowpoly.set_session(kit_session.collection_name)
    inst = lowpoly.instance(lowpoly.kit("barrel"), location=(3.0, 0.0, 0.0))
    check("인스턴스가 메시를 공유", inst.data.users > 1, str(inst.data.users))

    # 키트는 결과가 아니라 재료다 — 마무리 뒤 뷰 레이어에서 빠져야 한다
    kit_session._hide_kit()
    layer = scene_session._find_layer(probe_scene.view_layers[0].layer_collection,
                                      kit_session.kit_collection_name)
    check("키트 컬렉션 뷰 레이어 제외", layer is not None and layer.exclude is True)
    # 지정을 남겨두면 다음 오브젝트 잡의 lp.kit()이 남의 키트를 본다
    check("마무리 후 키트 조회 대상 해제", lowpoly._kit_collection_name is None)

    kit_session._remove_kit()
    check("키트 정리 후 컬렉션 제거",
          bpy.data.collections.get(kit_session.kit_collection_name) is None)
    check("인스턴스 메시는 남는다", inst.data is not None and len(inst.data.polygons) > 0)

    # 실패로 끝나면 키트는 참조할 배치가 없다 — .blend에 남기지 않아야 한다
    fail_session = scene_session.SceneSession(
        scene_name=probe_scene.name, uid=parent_uid, request="실패 확인",
        exe="/usr/bin/true", lane=0, scene_size='S')
    fail_session._kit_collection()
    fail_kit_name = fail_session.kit_collection_name
    fail_session._finish("실패: 배치 코드 실행 오류 반복", ok=False)
    check("실패 종료 시 키트 제거",
          bpy.data.collections.get(fail_kit_name) is None, fail_kit_name)
    shutil.rmtree(fail_session.workdir, ignore_errors=True)

    # 자식 잡 단독 조작 금지 — 부모만이 자식을 구동하고 결과를 거둬간다
    child_index = next(i for i, j in enumerate(props.jobs) if j.uid == tower)
    check("자식 잡 단독 재시도 거부", bool(jobs.retry_job(bpy.context, child_index)))
    before = [j.uid for j in props.jobs]
    jobs.move_job(props, child_index, -1)
    check("자식 잡 이동 금지", [j.uid for j in props.jobs] == before,
          str([j.uid for j in props.jobs]))

    # 13) 배경 레인 간격 — 씬 한 변보다 넓게 벌어져야 옆 레인과 겹치지 않는다
    # 인스턴스를 만들 때 lowpoly.root()가 세션 컬렉션을 이미 만들어 두었다
    lane_coll = bpy.data.collections.get(kit_session.collection_name)
    check("세션 컬렉션 생성", lane_coll is not None, kit_session.collection_name)
    lane_obj = bpy.data.objects.new("LP3D_SceneLaneProbe",
                                    bpy.data.meshes.new("LP3D_SceneLaneProbeMesh"))
    lane_coll.objects.link(lane_obj)
    kit_session._apply_lane()
    check("배경 결과에 배치 표식", lane_coll.get(jobs.PLACE_MARK) is not None)

    # 14) 익스포트 — 인스턴스(공유 메시)가 나가고 키트 컬렉션은 따라가지 않아야 한다
    export_kit = bpy.data.collections.new(kit_session.kit_collection_name)
    probe_scene.collection.children.link(export_kit)
    kit_only = bpy.data.objects.new("LP3D_KitOnlyProbe", inst.data)
    export_kit.objects.link(kit_only)
    out_dir = tempfile.mkdtemp(prefix="lp3d_export_probe_")
    try:
        fbx = export.export_collection(lane_coll, out_dir, 'FBX')
        check("FBX 익스포트 성공", os.path.isfile(fbx) and os.path.getsize(fbx) > 0, fbx)
        glb = export.export_collection(lane_coll, out_dir, 'GLTF')
        check("glTF 익스포트 성공", os.path.isfile(glb) and os.path.getsize(glb) > 0, glb)
        check("익스포트 대상은 세션 컬렉션뿐",
              kit_only.name not in {o.name for o in lane_coll.objects})
        check("익스포트 후에도 인스턴스 메시 공유 유지", inst.data.users > 1,
              str(inst.data.users))
    except Exception as e:
        check("익스포트 예외 없음", False, f"{type(e).__name__}: {e}")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
        bpy.data.objects.remove(kit_only)
        bpy.data.collections.remove(export_kit)

    for obj in (inst, lane_obj):
        bpy.data.objects.remove(obj)
    bpy.data.collections.remove(lane_coll)
    shutil.rmtree(kit_session.workdir, ignore_errors=True)
    while len(props.jobs):
        jobs.remove_job(bpy.context, 0)

    print(("실패 " + ", ".join(fails)) if fails else "모두 통과")
    if fails:
        raise SystemExit(1)
finally:
    bpy.context.window.scene = original_scene
    bpy.data.scenes.remove(probe_scene)
