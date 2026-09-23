# 링 가이드 확인 — 개발 프로필로 Blender 안에서 실행한다.
#   ./scripts/dev_run.sh --background --python tests/verify_ring_guide_in_blender.py
#
# ① 합성 몸체(구 + 좌우 팔 원통)에서 클릭 링 가이드 생성·오프셋 재슬라이스·삭제 오퍼레이터를 확인하고,
#    [리토폴로지] 연산자가 가이드를 절단 링으로 써서 팔에 링을 접합하는지 본다.
# ② 실제 셰이프 서버 GLB 에 팔·다리·목 가이드를 두고 리토폴로지해 접합 결과와 와이어 렌더를 남긴다.
#
# 환경변수: LP3D_RETOPO_GLB(입력 GLB, 없으면 ② 건너뜀), LP3D_RETOPO_OUT(렌더 출력 폴더),
#           LP3D_RETOPO_FACES(② 목표 면수)
import math
import os
import sys
import time

import bmesh
import bpy
from mathutils import Vector

ADDON = "bl_ext.user_default.lp3d_modelmaker"
if ADDON not in sys.modules:
    print("SKIP 애드온이 로드되지 않았습니다 — scripts/dev_run.sh 로 실행하세요")
    sys.exit(0)

import importlib  # noqa: E402

ring_guides = importlib.import_module(ADDON + ".ui.ring_guides")
scheduler = importlib.import_module(ADDON + ".core.scheduler")
quadretopo = importlib.import_module(ADDON + ".lowpoly.quadretopo")
retopo = importlib.import_module(ADDON + ".lowpoly.retopo")

GLB = os.environ.get("LP3D_RETOPO_GLB",
                     os.path.expanduser("~/Downloads/blender/character/LP3D_Model_014/셰이프.glb"))
OUT_DIR = os.environ.get("LP3D_RETOPO_OUT", os.path.expanduser("~/Downloads/blender/ring_guide_verify"))
TARGET = int(os.environ.get("LP3D_RETOPO_FACES", "8000"))

failures = []


def check(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'} {label}{': ' + str(detail) if detail != '' else ''}")
    if not ok:
        failures.append(label)


def make_job(name):
    """완료된 잡과 결과 컬렉션을 만들어 선택한다 — 결과물 패널의 버튼들이 이 잡을 대상으로 삼는다."""
    props = bpy.context.scene.lp3d
    job = props.jobs.add()
    job.uid = props.next_uid
    props.next_uid += 1
    job.state = 'DONE'
    coll = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(coll)
    job.collection_name = coll.name
    props.job_index = len(props.jobs) - 1
    return job, coll


def build_body(coll):
    """구(몸통) + 좌우 팔 원통. 겹친 셸은 리토폴로지의 복셀 리메시가 하나로 녹인다."""
    mesh = bpy.data.meshes.new("Body")
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=4, radius=1.0)
    for sign in (1.0, -1.0):
        result = bmesh.ops.create_cone(bm, cap_ends=True, segments=32, radius1=0.3, radius2=0.3, depth=1.6)
        for vert in result["verts"]:
            x, y, z = vert.co
            vert.co = Vector((sign * (1.5 + z), y, x))   # 원통 축(z)을 x 로 돌려 x=±0.7~±2.3 에 둔다
    bmesh.ops.subdivide_edges(bm, edges=[e for e in bm.edges if e.calc_length() > 0.2], cuts=2)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Body", mesh)
    coll.objects.link(obj)
    obj.location = (0.0, 0.0, 0.0)
    return obj


def ring_plane_vertices(obj, axis_value, tolerance):
    """x=axis_value 근처 정점 수 — 접합된 링이면 둘레 한 바퀴만큼 정점이 평면에 모인다."""
    return sum(1 for v in obj.data.vertices if abs(v.co.x - axis_value) < tolerance and v.co.y ** 2 + v.co.z ** 2 < 0.2)


def front_hit(target, x, z):
    """정면(-Y)에서 +Y 로 쏜 광선의 첫 히트 (월드 점, 월드 법선)."""
    from mathutils.bvhtree import BVHTree
    tree = BVHTree.FromObject(target, bpy.context.evaluated_depsgraph_get())
    inverse = target.matrix_world.inverted()
    origin = inverse @ Vector((x, -10.0, z))
    location, normal, _index, _distance = tree.ray_cast(origin, (inverse.to_3x3() @ Vector((0, 1, 0))).normalized())
    if location is None:
        return None
    return tuple(target.matrix_world @ location), tuple((target.matrix_world.to_3x3() @ normal).normalized())


def synthetic():
    job, coll = make_job("LP3D_RingTest")
    body = build_body(coll)
    bpy.context.view_layer.update()
    scene = bpy.context.scene
    previous_faces = scene.lp3d.retopo_faces
    scene.lp3d.retopo_faces = 3000

    started = time.perf_counter()
    guide, reason = ring_guides.create_ring_guide(coll, body, (1.5, 0.0, 0.3), (0.0, 0.0, 1.0))
    check("팔 가이드 생성", guide is not None, reason or f"{time.perf_counter() - started:.2f}s")
    ring = guide.lp3d_ring_guide
    check("축이 팔 방향(x)", abs(abs(ring.axis[0]) - 1.0) < 0.05, tuple(round(a, 3) for a in ring.axis))
    check("반지름 0.3 근처(복셀 프록시)", abs(ring.radius - 0.3) < 0.04, round(ring.radius, 3))
    check("둘레 비율 사용 가능", ring.ratio > 0.9, f"{ring.ratio:.2f} {ring.status}")
    xs = [p.co.x for p in guide.data.splines[0].points]
    check("커브가 클릭 단면 위", all(abs(x - 1.5) < 0.03 for x in xs), (round(min(xs), 3), round(max(xs), 3)))

    ring.offset = 0.3 if ring.axis[0] > 0 else -0.3
    ring_guides.refresh_ring_guide(guide)   # 백그라운드에는 update 콜백을 미뤄 부를 타이머가 돌지 않는다
    xs = [p.co.x for p in guide.data.splines[0].points]
    check("오프셋 재슬라이스", all(abs(x - 1.8) < 0.03 for x in xs), (round(min(xs), 3), round(max(xs), 3)))
    ring.offset = 0.0
    ring_guides.refresh_ring_guide(guide)

    extra, reason = ring_guides.create_ring_guide(coll, body, (-1.5, 0.0, 0.3), (0.0, 0.0, 1.0))
    check("반대쪽 팔 가이드 생성", extra is not None, reason)
    check("가이드 2개", len(ring_guides.ring_guides(coll)), 2)
    check("검사 연산자", bpy.ops.lp3d.ring_guide_check() == {'FINISHED'})
    check("삭제 연산자", bpy.ops.lp3d.ring_guide_remove(name=extra.name) == {'FINISHED'}
          and len(ring_guides.ring_guides(coll)) == 1)
    extra, _reason = ring_guides.create_ring_guide(coll, body, (-1.5, 0.0, 0.3), (0.0, 0.0, 1.0))
    check("추가 연산자 활성", bpy.ops.lp3d.ring_guide_add.poll())
    check("리토폴로지 대상은 메시", quadretopo.find_retopo_target(coll) == body)

    check("리토폴로지 연산자 활성", bpy.ops.lp3d.job_retopo.poll())
    bpy.ops.lp3d.job_retopo()
    scheduler.pump()
    print(f"INFO 합성 결과: {job.status}")
    print("INFO 로그:\n  " + job.log.replace("\n", "\n  "))
    check("리토폴로지 완료", job.status.startswith("리토폴로지 완료"), job.status)
    check("링 접합 보고", "절단 링" in job.log and "접합" in job.log)
    # 대칭이면 반대쪽 팔 가이드는 양의 쪽으로 미러돼 하나로 합쳐진다
    check("대칭 반쪽에서 좌우 가이드가 하나로 합쳐짐", "링 1개 접합" in job.status, job.status)
    result = quadretopo.find_retopo_target(coll)
    tolerance = 0.02
    for x in (1.5, -1.5):
        counts = [ring_plane_vertices(result, x + d, tolerance) for d in (-0.06, 0.0, 0.06)]
        print(f"INFO x={x} 근처 평면 정점 수 {counts}")
    check("가이드는 결과 뒤에도 남음", len(ring_guides.ring_guides(coll)) == 2)
    check("가이드 소스가 보존본으로 바뀜", bool(ring_guides.section_source(coll).get(quadretopo.SOURCE_KEY)))
    check("다시 검사(보존본 기준)", bpy.ops.lp3d.ring_guide_check() == {'FINISHED'})
    render(result, "00_합성")
    check("전체 삭제 연산자", bpy.ops.lp3d.ring_guide_clear() == {'FINISHED'} and not ring_guides.ring_guides(coll))
    scene.lp3d.retopo_faces = previous_faces


def character():
    if not os.path.isfile(GLB):
        print(f"SKIP 입력 GLB 가 없습니다: {GLB}")
        return
    job, coll = make_job("LP3D_RingChar")
    imported = retopo.import_textured(GLB, "캐릭터", coll)
    source = imported["obj"]
    bpy.context.view_layer.update()
    height = source.dimensions.z
    base = min((source.matrix_world @ Vector(c)).z for c in source.bound_box)
    render(source, "01_원본", wire=False)
    # 오거(LP3D_Model_014) 정면 기준 (x, 키 비율) — 위팔·팔뚝·허벅지·정강이는 +X 쪽만, 목은 가운데.
    # 고블린(002)은 QuadriFlow 입력부터 구멍 메움 조각 덩어리라 팔이 관이 아니어서 링 검증에 쓰지 않는다
    spots = {
        "위팔": (0.475, 0.572), "팔뚝": (0.60, 0.339), "허벅지": (0.16, 0.272), "정강이": (0.22, 0.131),
        "목": (0.0, 0.745),
    }
    scene = bpy.context.scene
    previous_faces = scene.lp3d.retopo_faces
    scene.lp3d.retopo_faces = TARGET
    started = time.perf_counter()
    made = 0
    for label, (x, zf) in spots.items():
        hit = front_hit(source, x, base + height * zf)
        if hit is None:
            print(f"INFO {label}: 표면을 맞히지 못함")
            continue
        guide, reason = ring_guides.create_ring_guide(coll, source, *hit)
        if guide is None:
            print(f"INFO {label}: {reason}")
            continue
        made += 1
        guide.name = f"LP3D_Ring_{label}"
        ring = guide.lp3d_ring_guide
        print(f"INFO {label}: 반지름 {ring.radius:.3f} · 축 {tuple(round(a, 2) for a in ring.axis)} · "
              f"비율 {ring.ratio:.2f} · {ring.status}")
    print(f"INFO 가이드 {made}개 생성 {time.perf_counter() - started:.1f}s")
    check("캐릭터 가이드 2개 이상", made >= 2, made)
    bpy.ops.lp3d.job_retopo()
    scheduler.pump()
    print(f"INFO 캐릭터 결과: {job.status}")
    print("INFO 로그:\n  " + job.log.replace("\n", "\n  "))
    check("캐릭터 리토폴로지 완료", job.status.startswith("리토폴로지 완료"), job.status)
    result = quadretopo.find_retopo_target(coll)
    stash = quadretopo.find_retopo_source(coll)
    if result is not None and stash is not None:
        print(f"INFO 대칭 오차 {quadretopo.symmetry_error(result):.6f}")
        render(result, "02_리토폴로지")
    scene.lp3d.retopo_faces = previous_faces


def render(obj, prefix, wire=True):
    """정면·3/4·옆을 찍는다. wire 면 와이어 모디파이어를 씌운 복사본을 겹쳐 링 흐름을 본다."""
    os.makedirs(OUT_DIR, exist_ok=True)
    scene = bpy.context.scene
    shown = [obj]
    overlay = None
    if wire:
        overlay = bpy.data.objects.new(obj.name + "_wire", obj.data)
        scene.collection.objects.link(overlay)
        overlay.matrix_world = obj.matrix_world.copy()
        mod = overlay.modifiers.new("wire", 'WIREFRAME')
        mod.thickness = max(obj.dimensions) * 0.0015
        mod.use_replace = True
        overlay.color = (0.05, 0.05, 0.05, 1.0)
        shown.append(overlay)
    bpy.context.view_layer.update()
    points = [o.matrix_world @ Vector(c) for o in shown for c in o.bound_box]
    lo = Vector([min(p[i] for p in points) for i in range(3)])
    hi = Vector([max(p[i] for p in points) for i in range(3)])
    center, radius = (lo + hi) / 2, max((hi - lo).length / 2, 0.1)
    cam_data = bpy.data.cameras.new("VerifyCam")
    cam = bpy.data.objects.new("VerifyCam", cam_data)
    scene.collection.objects.link(cam)
    saved = [(o, o.hide_render) for o in scene.objects]
    try:
        for o, _hidden in saved:
            o.hide_render = o not in shown
        scene.render.engine = 'BLENDER_WORKBENCH'
        scene.render.resolution_x = scene.render.resolution_y = 900
        scene.display.shading.light = 'STUDIO'
        scene.display.shading.color_type = 'OBJECT'
        obj.color = (0.85, 0.85, 0.85, 1.0)
        scene.camera = cam
        distance = radius / math.tan(cam_data.angle / 2) * 1.2
        for name, direction in (("front", Vector((0, -1, 0.1))), ("quarter", Vector((1, -1, 0.4))),
                                ("side", Vector((1, 0, 0.1)))):
            cam.location = center + direction.normalized() * distance
            cam.rotation_euler = (center - cam.location).to_track_quat('-Z', 'Y').to_euler()
            scene.render.filepath = os.path.join(OUT_DIR, f"{prefix}_{name}.png")
            bpy.ops.render.render(write_still=True)
            print(f"RENDER {scene.render.filepath}")
    finally:
        for o, hidden in saved:
            if o.name in bpy.data.objects:
                o.hide_render = hidden
        bpy.data.objects.remove(cam, do_unlink=True)
        bpy.data.cameras.remove(cam_data)
        if overlay is not None:
            bpy.data.objects.remove(overlay, do_unlink=True)


def main():
    stage = os.environ.get("LP3D_RING_STAGE", "all")
    # 씬 옵션은 JSON 으로 영속화된다 — GUI 에서 끈 X 대칭이 검증에 새지 않게 고정하고 끝나면 되돌린다
    props = bpy.context.scene.lp3d
    previous_symmetry = props.retopo_symmetry
    props.retopo_symmetry = True
    try:
        if stage in ("all", "synthetic"):
            synthetic()
        if stage in ("all", "character"):
            character()
    finally:
        props.retopo_symmetry = previous_symmetry
    print(f"\n결과: {'모두 통과' if not failures else f'실패 {len(failures)}건: {failures}'}")
    return 1 if failures else 0


sys.exit(main())
