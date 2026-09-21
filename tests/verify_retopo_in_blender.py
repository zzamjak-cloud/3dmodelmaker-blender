# 쿼드 리토폴로지 확인 — Blender 안에서 실행한다.
#   ./scripts/dev_run.sh --background --python tests/verify_retopo_in_blender.py
#
# 실제 셰이프 서버 결과(GLB)를 retopo.import_textured 로 가져온 뒤 quadretopo.retopologize 를 돌려
# 면수·쿼드 비율·UV·베이크 이미지·원본 보존을 확인하고, 원본과 결과를 같은 앵글로 렌더해 비교한다.
#
# 환경변수: LP3D_RETOPO_GLB(입력 GLB), LP3D_RETOPO_OUT(렌더 출력 폴더),
#           LP3D_RETOPO_FACES(목표 면수), LP3D_RETOPO_TEX(텍스처 한 변)
import os
import sys
import time

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lowpoly import quadretopo, retopo  # noqa: E402

GLB = os.environ.get(
    "LP3D_RETOPO_GLB",
    os.path.expanduser("~/Downloads/blender/character/LP3D_Model.002_002/셰이프.glb"))
OUT_DIR = os.environ.get("LP3D_RETOPO_OUT",
                         os.path.expanduser("~/Downloads/blender/retopo_verify"))
TARGET = int(os.environ.get("LP3D_RETOPO_FACES", "8000"))
TEXTURE = int(os.environ.get("LP3D_RETOPO_TEX", "2048"))

failures = []


def check(label, actual, expect, cmp="eq"):
    ok = {"eq": lambda: actual == expect,
          "ge": lambda: actual >= expect,
          "le": lambda: actual <= expect,
          "true": lambda: bool(actual)}[cmp]()
    print(f"{'PASS' if ok else 'FAIL'} {label}: {actual} (기대 {cmp} {expect})")
    if not ok:
        failures.append(label)


def render_views(objs, prefix: str, resolution: int = 800) -> list:
    """정면·3/4 를 Workbench 텍스처 셰이딩으로 찍는다 — 베이크 텍스처가 제자리에 붙었는지 눈으로 본다."""
    import math

    from mathutils import Vector

    scene = bpy.context.scene
    bpy.context.view_layer.update()   # bound_box 는 캐시다 — 메시를 직접 변환한 뒤에는 갱신해야 한다
    points = [obj.matrix_world @ Vector(c) for obj in objs for c in obj.bound_box]
    lo = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    hi = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    center, radius = (lo + hi) / 2, max((hi - lo).length / 2, 0.1)

    cam_data = bpy.data.cameras.new("VerifyCam")
    cam = bpy.data.objects.new("VerifyCam", cam_data)
    scene.collection.objects.link(cam)
    distance = radius / math.tan(cam_data.angle / 2) * 1.3

    shown = set(objs)
    saved = [(o, o.hide_render) for o in scene.collection.all_objects]
    saved_engine, saved_camera = scene.render.engine, scene.camera
    shading = scene.display.shading
    saved_shading = (shading.light, shading.color_type)
    paths = []
    try:
        for obj, _ in saved:
            obj.hide_render = obj not in shown
        scene.render.engine = 'BLENDER_WORKBENCH'
        scene.render.resolution_x = scene.render.resolution_y = resolution
        scene.render.resolution_percentage = 100
        scene.render.film_transparent = False
        scene.view_settings.view_transform = 'Standard'
        shading.light = 'STUDIO'
        shading.color_type = 'TEXTURE'
        scene.camera = cam
        for name, direction in (("front", Vector((0, -1, 0.1))), ("quarter", Vector((1, -1, 0.5)))):
            cam.location = center + direction.normalized() * distance
            cam.rotation_euler = (center - cam.location).to_track_quat('-Z', 'Y').to_euler()
            path = os.path.join(OUT_DIR, f"{prefix}_{name}.png")
            scene.render.filepath = path
            bpy.ops.render.render(write_still=True)
            paths.append(path)
    finally:
        for obj, hidden in saved:
            obj.hide_render = hidden
        scene.render.engine, scene.camera = saved_engine, saved_camera
        shading.light, shading.color_type = saved_shading
        bpy.data.objects.remove(cam, do_unlink=True)
        bpy.data.cameras.remove(cam_data)
    return paths


def mirror_ratio(obj, tolerance: float = 0.004) -> float:
    """정점마다 x 를 뒤집은 자리에서 tolerance x 모델 크기 안에 정점이 있는 비율."""
    from mathutils import Vector
    from mathutils.kdtree import KDTree

    verts = obj.data.vertices
    tree = KDTree(len(verts))
    for index, vert in enumerate(verts):
        tree.insert(vert.co, index)
    tree.balance()
    limit = max(obj.dimensions) * tolerance
    hits = sum(1 for v in verts if tree.find(Vector((-v.co.x, v.co.y, v.co.z)))[2] < limit)
    return hits / max(len(verts), 1)


def gap_match(source, result) -> float:
    """몸통 옆 겨드랑이 높이(키의 50~60%)에서 x 축 광선의 표면 교차 수가 원본과 같은 표본 비율.

    복셀 리메시가 팔과 몸통 사이 틈을 메우면 교차 수가 줄고, 슈링크랩이 면을 접으면 늘어난다."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    saved = [(obj, obj.hide_viewport, obj.hide_get()) for obj in (source, result)]
    for obj, _viewport, _hidden in saved:
        obj.hide_viewport = False
        obj.hide_set(False)
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    trees = [BVHTree.FromObject(obj, depsgraph) for obj in (source, result)]
    for obj, viewport, hidden in saved:
        obj.hide_viewport = viewport
        obj.hide_set(hidden)
    height = max(source.dimensions)
    depth = source.dimensions.y

    def hits(tree, y, z, sign):
        count, start, direction = 0, Vector((0.0, y, z)), Vector((sign, 0.0, 0.0))
        for _ in range(12):
            hit = tree.ray_cast(start, direction, height * 2)
            if hit[0] is None:
                break
            count += 1
            start = hit[0] + direction * 1e-4
        return count

    matched = total = 0
    for i in range(9):
        z = height * (0.5 + 0.0125 * i)
        for j in range(9):
            y = depth * (-0.15 + 0.0375 * j)
            for sign in (1, -1):
                total += 1
                matched += hits(trees[0], y, z, sign) == hits(trees[1], y, z, sign)
    return matched / max(total, 1)


def check_operator():
    """[리토폴로지] 버튼 경로 — 스케줄러에 제출되고, 진행 상황이 job.status 에 실리는지.

    애드온으로 로드되지 않았으면(순수 `blender --python`) 건너뛴다. 공장 초기화가 애드온을 끄므로
    본 검증보다 먼저 돌려야 한다."""
    if getattr(bpy.context.scene, "lp3d", None) is None or not hasattr(bpy.ops.lp3d, "job_retopo"):
        print("SKIP 연산자 경로 — 애드온이 로드되지 않았습니다")
        return
    import bmesh

    from bl_ext.user_default.lp3d_modelmaker.core import scheduler

    scene = bpy.context.scene
    props = scene.lp3d
    job = props.jobs.add()
    job.state = 'DONE'
    coll = bpy.data.collections.new("LP3D_OpTest")
    scene.collection.children.link(coll)
    job.collection_name = coll.name
    props.job_index = len(props.jobs) - 1
    mesh = bpy.data.meshes.new("OpTest")
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=4, radius=0.5)
    bm.to_mesh(mesh)
    bm.free()
    coll.objects.link(bpy.data.objects.new("OpTest", mesh))

    check("연산자 활성", bpy.ops.lp3d.job_retopo.poll(), True, "true")
    bpy.ops.lp3d.job_retopo()
    check("Blender 큐에 제출됨", scheduler.counts()["blender_waiting"], 1)
    check("제출 직후 상태 표시", job.status.startswith("리토폴로지:"), True, "true")
    scheduler.pump()   # 백그라운드에는 타이머 펌프가 없으므로 직접 돌린다
    print(f"INFO 연산자 결과: {job.status}")
    check("연산자 완료 보고", job.status.startswith("리토폴로지 완료"), True, "true")
    check("로그 기록", "리토폴로지 완료" in job.log, True, "true")
    check("보존본 생성", quadretopo.has_retopo_source(coll), True, "true")
    # 다시 리토폴로지 — 옵션을 바꿔 다시 누르면 보존본에서 새로 깔고, 보존본이 겹으로 늘지 않는다
    previous_faces = props.retopo_faces   # 씬 옵션은 JSON 으로 영속화되므로 검사 뒤 되돌린다
    props.retopo_faces = 3000
    check("다시 리토폴로지 활성", bpy.ops.lp3d.job_retopo.poll(), True, "true")
    bpy.ops.lp3d.job_retopo()
    scheduler.pump()
    print(f"INFO 다시 리토폴로지: {job.status}")
    check("다시 리토폴로지 완료", job.status.startswith("리토폴로지 완료"), True, "true")
    stashes = [o for o in coll.objects if o.get(quadretopo.SOURCE_KEY)]
    results = [o for o in coll.objects if o.type == 'MESH' and not o.get(quadretopo.SOURCE_KEY)]
    check("보존본은 하나", len(stashes), 1)
    check("결과 메시는 하나", len(results), 1)
    check("결과가 원래 이름 유지", results[0].name if results else "", "OpTest")
    check("다시 깐 면수가 새 목표를 따름 (4500 이하)", len(results[0].data.polygons) if results else 99999, 4500, "le")
    props.retopo_faces = previous_faces


def main():
    if not os.path.isfile(GLB):
        print(f"SKIP 입력 GLB 가 없습니다: {GLB}")
        return 0
    os.makedirs(OUT_DIR, exist_ok=True)
    check_operator()   # 애드온이 살아 있는 동안 먼저 — 아래 공장 초기화가 애드온을 끈다
    bpy.ops.wm.read_factory_settings(use_empty=True)
    coll = bpy.data.collections.new("LP3D_Retopo")
    bpy.context.scene.collection.children.link(coll)

    started = time.perf_counter()
    imported = retopo.import_textured(GLB, "캐릭터", coll)
    source = imported["obj"]
    print(f"INFO 임포트: 면 {imported['faces']} · 트라이 {imported['tris']} · "
          f"이미지 {imported['images']} · 구멍 메움 {imported['filled_holes']} · "
          f"열린 테두리 {imported['open_loops']} · 공동 {imported['cavities']} "
          f"(되살린 면 {imported['revived_faces']}) · {time.perf_counter() - started:.1f}s")
    # ⓪ 컬링이 남긴 작은 구멍은 임포트에서 메워지고, 옷의 진짜 테두리(소매·밑단·깃)만 열려 있다
    check("컬링 구멍 메움", imported["filled_holes"], 1, "ge")
    check("메운 루프가 남긴 테두리보다 많음", imported["filled_holes"], imported["open_loops"], "ge")
    before = render_views([source], "01_원본")

    def say(text):
        print(f"  … {text}")

    result = quadretopo.retopologize(source, coll, target_faces=TARGET, symmetry=True,
                                     texture_size=TEXTURE, normal_map=True, progress=say)
    obj = result["obj"]
    print(f"INFO 결과: {result['method']} · 면 {result['faces']} · 쿼드 {result['quads']} · "
          f"트라이 {result['tris']} · {result['seconds']}s")

    # ① 면수가 목표의 0.7~1.5배
    check("면수 하한 (목표 x0.7)", result["faces"], int(TARGET * 0.7), "ge")
    check("면수 상한 (목표 x1.5)", result["faces"], int(TARGET * 1.5), "le")
    # ② 쿼드 비율 (QuadriFlow 성공 시 80% 이상)
    ratio = result["quads"] / max(result["faces"], 1)
    print(f"INFO 쿼드 비율 {ratio:.1%}")
    if result["method"] == "QUADRIFLOW":
        check("쿼드 비율 80% 이상", round(ratio, 3), 0.8, "ge")
    # ③ UV 레이어
    check("UV 레이어 존재", len(obj.data.uv_layers), 1, "ge")
    # ③' 좌우 대칭 — x 를 뒤집은 자리에 정점이 있는 비율. 슈링크랩이 비대칭 원본에 붙이므로 1.0 은 못 되지만
    # 원본(0.6~0.7)보다 뚜렷이 높아야 대칭 와이어가 걸린 것이다(실측: 0.86~0.99)
    stash = bpy.data.objects.get(result["source_name"])
    if result["method"] == "QUADRIFLOW":
        source_mirror = mirror_ratio(stash)
        print(f"INFO 대칭 정점 비율: 원본 {source_mirror:.3f} → 결과 {mirror_ratio(obj):.3f}")
        # 원본이 이미 대칭(0.9+)이면 더 높아질 여지가 없으므로 0.9 를 상한으로 둔다
        check("좌우 대칭 정점 비율 (min(0.9, 원본+0.1) 이상)", round(mirror_ratio(obj), 3),
              round(min(0.9, source_mirror + 0.1), 3), "ge")
    # ③'' 좁은 틈(겨드랑이 높이) 보존 — 원본과 결과의 x 축 광선 교차 수 일치 비율. 모델마다 자세가 달라 참고값
    print(f"INFO 좁은 틈 보존(광선 교차 일치): {gap_match(stash, obj):.3f}")
    # ④ 베이크 이미지가 새 머티리얼에 연결 (NORMAL_MAP 노드 포함)
    material = obj.data.materials[0] if obj.data.materials else None
    tree = material.node_tree if material and material.use_nodes else None
    linked = [n.image.name for n in tree.nodes
              if n.type == 'TEX_IMAGE' and n.image and n.outputs["Color"].links] if tree else []
    check("연결된 베이크 이미지 3장 이상", len(linked), 3, "ge")
    check("NORMAL_MAP 노드 존재",
          bool(tree and any(n.type == 'NORMAL_MAP' for n in tree.nodes)), True, "true")
    non_black = [name for name in linked
                 if any(v > 0.01 for v in list(bpy.data.images[name].pixels[:4096]))]
    check("베이크 결과가 비어 있지 않음", len(non_black), 1, "ge")
    print(f"INFO 이미지: {result['images']} · 내용 있는 이미지 {non_black}")
    # ⑤ 원본이 숨겨진 채 남아 있음
    check("원본 보존본 존재", stash is not None and stash.name.endswith(quadretopo.SOURCE_SUFFIX),
          True, "true")
    check("보존본 숨김", bool(stash and stash.hide_render and stash.hide_get()), True, "true")
    check("보존본 표식", bool(stash and stash.get(quadretopo.SOURCE_KEY)), True, "true")
    check("결과가 원래 이름을 이어받음", obj.name, "캐릭터")

    after = render_views([obj], "02_리토폴로지")
    print("RENDER 원본:", *before, sep="\n  ")
    print("RENDER 결과:", *after, sep="\n  ")
    print(f"\n결과: {'모두 통과' if not failures else f'실패 {len(failures)}건: {failures}'}")
    return 1 if failures else 0


sys.exit(main())
