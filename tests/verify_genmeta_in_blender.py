# 생성 정보 보존 확인 — Blender 안에서 애드온을 켠 채 실행한다.
#   blender --background --factory-startup --python scripts/dev_bootstrap.py --python tests/verify_genmeta_in_blender.py
#
# 완성된 잡의 정보를 결과 컬렉션·원본 메시에 새기고, 결과만 담은 자동 저장 .blend(임시 씬, 큐 없음)를 다시 열었을 때
# 큐 항목이 되살아나 리토폴로지를 다시 누를 수 있는지, 다시 저장·열기를 반복해도 항목이 겹으로 늘지 않는지,
# 정보가 없는 옛 메시를 결과로 등록할 수 있는지 본다.
import os
import sys
import tempfile

import bmesh
import bpy

failures = []


def check(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'} {label}" + (f": {detail}" if detail != "" else ""))
    if not ok:
        failures.append(label)


def make_mesh(name, coll):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=0.5)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    coll.objects.link(obj)
    return obj


def main():
    if getattr(bpy.context.scene, "lp3d", None) is None:
        print("SKIP 애드온이 로드되지 않았습니다")
        return 0
    from bl_ext.user_default.lp3d_modelmaker.core import autosave, genmeta, jobs
    from bl_ext.user_default.lp3d_modelmaker.lowpoly import quadretopo

    folder = tempfile.mkdtemp(prefix="lp3d_genmeta_")
    scene = bpy.context.scene
    props = scene.lp3d
    job = jobs.add_job(props, prompt="근육질 좀비 캐릭터, 찢어진 셔츠", mode='CHARACTER')
    job.character_type = 'HUMANOID'
    job.front_image = 'USE_REF'
    job.ref_image_path = os.path.join(folder, "원화.png")
    job.effective_model = "gpt-6-astra"
    job.log = "세션 시작\n완료"
    coll = bpy.data.collections.new("근육질_좀비")
    scene.collection.children.link(coll)
    make_mesh("근육질_좀비", coll)
    job.collection_name = coll.name
    job.state = 'DONE'
    job.status = "완료 — 근육질_좀비"

    check("정보 기록", genmeta.stamp(job))
    obj = coll.objects["근육질_좀비"]
    data = genmeta.read(obj)
    check("원본 메시에 정보", data.get("prompt") == job.prompt, data.get("prompt"))
    check("컬렉션에 정보", genmeta.read(coll).get("character_type") == 'HUMANOID')

    # 자동 저장과 같은 경로 — 결과 컬렉션만 담은 임시 씬을 쓴다(큐 없음)
    blend = autosave.write_blend(folder, "근육질_좀비", [coll.name])
    check("결과 .blend 저장", bool(blend) and os.path.isfile(blend), blend)

    bpy.ops.wm.open_mainfile(filepath=blend)   # load_post 가 큐 항목을 되살린다
    scene = bpy.context.scene
    props = scene.lp3d
    check("씬은 결과만 담은 임시 씬", scene.name == "근육질_좀비", scene.name)
    check("큐 항목 복원 (1개)", len(props.jobs) == 1, len(props.jobs))
    restored = props.jobs[0] if props.jobs else None
    if restored is not None:
        check("프롬프트 복원", restored.prompt == "근육질 좀비 캐릭터, 찢어진 셔츠", restored.prompt)
        check("제작 모드 복원", restored.creation_mode == 'CHARACTER', restored.creation_mode)
        check("캐릭터 유형 복원", restored.character_type == 'HUMANOID', restored.character_type)
        check("정면 이미지 옵션 복원", restored.front_image == 'USE_REF', restored.front_image)
        check("모델 추적 복원", restored.effective_model == "gpt-6-astra", restored.effective_model)
        check("완료 상태", restored.state == 'DONE', restored.state)
        check("결과 컬렉션 연결", restored.collection_name == "근육질_좀비", restored.collection_name)
        props.job_index = 0
        check("리토폴로지 버튼 활성", bpy.ops.lp3d.job_retopo.poll())
        check("리토폴로지 대상은 원본", quadretopo.find_retopo_target(
            bpy.data.collections.get(restored.collection_name)).name == "근육질_좀비")

    # 전체 파일로 다시 저장·열기 — 큐가 파일에 들어 있으므로 복원이 겹치면 안 된다
    again = os.path.join(folder, "resaved.blend")
    bpy.ops.wm.save_as_mainfile(filepath=again)
    bpy.ops.wm.open_mainfile(filepath=again)
    check("다시 열어도 항목이 겹으로 늘지 않음", len(bpy.context.scene.lp3d.jobs) == 1,
          len(bpy.context.scene.lp3d.jobs))

    scene = bpy.context.scene
    props = scene.lp3d
    # 결과 컬렉션 이름을 바꿔도 새 항목을 만들지 않고 기존 항목을 잇는다
    bpy.data.collections["근육질_좀비"].name = "근육질_좀비_최종"
    check("이름 변경 뒤 복원 없음", genmeta.restore_jobs(scene) == 0)
    check("기존 항목이 새 이름을 이음", props.jobs[0].collection_name == "근육질_좀비_최종",
          props.jobs[0].collection_name)
    # 큐에서 지운 항목은 결과가 남아도 되살리지 않는다 — 생성 정보 자체는 보존
    jobs.remove_job(bpy.context, 0)
    check("지운 항목은 복원 안 함", genmeta.restore_jobs(scene) == 0 and len(props.jobs) == 0, len(props.jobs))
    check("지워도 원본의 생성 정보는 보존",
          bool(genmeta.read(bpy.data.objects["근육질_좀비"]).get("prompt")))
    # Shift+D 복제로 정보가 따라온 메시가 든 다른 컬렉션은 결과로 보지 않는다
    props_coll = bpy.data.collections.new("소품")
    scene.collection.children.link(props_coll)
    props_coll.objects.link(bpy.data.objects["근육질_좀비"].copy())
    check("복제 메시 컬렉션은 복원 안 함", genmeta.restore_jobs(scene) == 0)

    # 옛 버전 파일 — 정보가 없는 메시는 선택해서 결과로 등록한다
    old = bpy.data.collections.new("옛_캐릭터")
    scene.collection.children.link(old)
    old_obj = make_mesh("옛_캐릭터", old)
    bpy.context.view_layer.objects.active = old_obj
    check("등록 버튼 활성", bpy.ops.lp3d.adopt_mesh.poll())
    check("등록 실행", bpy.ops.lp3d.adopt_mesh() == {'FINISHED'})
    check("등록 후 항목 1개", len(props.jobs) == 1, len(props.jobs))
    check("등록 항목이 선택됨", props.active_job().collection_name == "옛_캐릭터")
    check("등록 후 정보 기록", genmeta.read(old_obj).get("collection_name") == "옛_캐릭터")
    check("등록 뒤 버튼 비활성", not bpy.ops.lp3d.adopt_mesh.poll())
    check("등록 항목 리토폴로지 활성", bpy.ops.lp3d.job_retopo.poll())

    # 결과 컬렉션(리토폴로지 복제본)은 복원 대상이 아니다
    result = bpy.data.collections.new("옛_캐릭터" + quadretopo.RESULT_SUFFIX)
    result[quadretopo.RETOPO_OF_KEY] = "옛_캐릭터"
    scene.collection.children.link(result)
    copy = old_obj.copy()
    copy[quadretopo.RETOPO_OF_KEY] = old_obj.name
    result.objects.link(copy)
    check("리토폴로지 결과는 복원 대상 아님", genmeta.restore_jobs(scene) == 0)

    print(f"\n결과: {'모두 통과' if not failures else f'실패 {len(failures)}건: {failures}'}")
    return 1 if failures else 0


sys.exit(main())
