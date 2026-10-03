# 엣지 선 가이드 헤드리스 검증 — 그린 선을 따라 출력 와이어 엣지가 깔리는지(선 커버리지)를 가이드 없이 깐 결과와 비교한다.
#
#   ./scripts/dev_run.sh --background --python tests/verify_edge_line_in_blender.py              # 합성 구(대칭 반쪽 경로)
#   ./scripts/dev_run.sh --background "<자동 저장 .blend>" --python tests/verify_edge_line_in_blender.py  # 그 파일의 원본(좌우 분할 등)
#
# 커버리지 = 양 끝이 선에서 출력 엣지 × 0.2 안이고 선 방향과 25° 안으로 나란한 출력 엣지들이 덮는 선 구간(합집합) / 선 길이.
import importlib
import math
import os
import sys

import bmesh
import bpy
from mathutils import Vector

ADDON = "bl_ext.user_default.lp3d_modelmaker"
if ADDON not in sys.modules:
    print("SKIP 애드온이 로드되지 않았습니다 (dev_run.sh 로 실행)")
    sys.exit(0)
quadretopo = importlib.import_module(ADDON + ".lowpoly.quadretopo")
edge_line = importlib.import_module(ADDON + ".lowpoly.edge_line")
ring_guides = importlib.import_module(ADDON + ".ui.ring_guides")

TARGET = int(os.environ.get("LP3D_EDGE_TARGET", "3000"))
REPORT_ONLY = {"배가로"}
failures = []


def check(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def coverage(obj, points_world, offset):
    """출력 메시(obj, 월드 offset 만큼 옮겨 둔 결과)에서 선을 따라 깔린 엣지 비율."""
    points = [tuple(Vector(p) + offset) for p in points_world]
    total = edge_line.length(points)
    faces = len(obj.data.polygons)
    area = sum(p.area for p in obj.data.polygons)
    edge = math.sqrt(area / max(faces, 1))
    tol = edge * 0.2
    spans = []
    mw = obj.matrix_world
    for e in obj.data.edges:
        a = mw @ obj.data.vertices[e.vertices[0]].co
        b = mw @ obj.data.vertices[e.vertices[1]].co
        da = edge_line.closest(points, tuple(a))
        db = edge_line.closest(points, tuple(b))
        if da[0] > tol or db[0] > tol:
            continue
        lo, hi = sorted((da[1], db[1]))
        if hi - lo >= (b - a).length * math.cos(math.radians(25)):
            spans.append((lo, hi))
    covered, end = 0.0, -1e9
    for lo, hi in sorted(spans):
        lo = max(lo, end)
        if hi > lo:
            covered += hi - lo
            end = hi
    return min(1.0, covered / total) if total > 0 else 0.0, edge


def run(source, coll, symmetry, edges, label):
    result = quadretopo.retopologize(source, coll, target_faces=TARGET, symmetry=symmetry, texture_size=256,
                                     normal_map=False, progress=lambda t: None, edge_guides=edges)
    obj = result["obj"]
    offset = obj.matrix_world.translation - source.matrix_world.translation
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    boundary = sum(1 for e in bm.edges if len(e.link_faces) == 1)
    nonmanifold = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    tris = sum(1 for f in bm.faces if len(f.verts) == 3)
    bm.free()
    print(f"INFO {label}: {result['method']} · 면 {result['faces']} · 쿼드 {result['quads']} · 삼각형 {tris} · "
          f"대칭 {result['symmetry']} · 분할 {result['split']} · 엣지 선 {result.get('edges')} · {result['seconds']}s")
    for note in result["notes"]:
        print(f"  note: {note}")
    return obj, offset, result, boundary, nonmanifold


def evaluate(source, coll, symmetry, lines):
    base_obj, base_offset, _r, _b, _n = run(source, coll, symmetry, (), "가이드 없음")
    base = [coverage(base_obj, pts, base_offset)[0] for _name, pts in lines]
    obj, offset, result, boundary, nonmanifold = run(source, coll, symmetry, lines, "엣지 선")
    check("QuadriFlow 로 깔림", result["method"] == "QUADRIFLOW", result["method"])
    check("엣지 선 모두 용접", result.get("edges") == len(lines), f"{result.get('edges')}/{len(lines)}")
    check("결과에 작업용 고정 속성이 남지 않음", edge_line.PIN_ATTRIBUTE not in obj.data.attributes)
    check("구멍 없음", boundary == 0, boundary)
    check("비매니폴드 0", nonmanifold == 0, nonmanifold)
    for (name, pts), before in zip(lines, base):
        after, edge = coverage(obj, pts, offset)
        print(f"INFO {name}: 커버리지 {before:.2f} → {after:.2f} (출력 엣지 {edge:.3f})")
        if name in REPORT_ONLY:
            continue
        check(f"{name} 커버리지 0.75 이상", after >= 0.75, f"{after:.2f}")


def synthetic():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    coll = bpy.data.collections.new("LP3D_EdgeTest")
    bpy.context.scene.collection.children.link(coll)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=5, radius=1.0)
    body = bpy.context.active_object
    for c in body.users_collection:
        c.objects.unlink(body)
    coll.objects.link(body)
    body.name = "Body"
    mat = bpy.data.materials.new("BodyMat")
    body.data.materials.append(mat)
    # 양의 쪽에서 자오선과 35° 기운 대원 호 — QuadriFlow 가 저절로는 깔지 않는 방향
    tilt = math.radians(35)
    normal = Vector((math.cos(tilt), 0.0, math.sin(tilt))).cross(Vector((0.0, 1.0, 0.0))).normalized()
    u = Vector((0.0, 1.0, 0.0))
    v = normal.cross(u).normalized()
    pts = []
    for k in range(41):
        t = math.radians(-50 + 100 * k / 40)
        p = u * math.sin(t) + v * math.cos(t)
        if p.x < 0:
            p = -p
        pts.append(tuple(p))
    pts = [p for p in pts if p[0] > 0.35]
    print(f"INFO 합성 선: 점 {len(pts)} · x {min(p[0] for p in pts):.2f}~{max(p[0] for p in pts):.2f}")
    evaluate(body, coll, True, [("기운호", pts)])


def from_file():
    source = next((o for o in bpy.data.objects if o.type == 'MESH' and o.get('lp3d_gen')
                   and not o.get(quadretopo.RETOPO_OF_KEY)), None)
    coll = next((c for c in bpy.data.collections if source is not None and source.name in c.objects), None)
    if source is None or coll is None:
        print("SKIP 원본이 없습니다")
        return
    if coll.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(coll)
    for guide in [o for o in coll.objects if o.type == 'CURVE']:
        coll.objects.unlink(guide)   # 기존 링 가이드는 이 검증과 무관하다
    bpy.context.view_layer.update()
    height = source.dimensions.z
    base_z = min((source.matrix_world @ Vector(c)).z for c in source.bound_box)

    def front_hit(x, zf):
        origin = Vector((x, -5.0, base_z + height * zf))
        inverse = source.matrix_world.inverted()
        ok, loc, nrm, _i = source.ray_cast(inverse @ origin, inverse.to_3x3() @ Vector((0.0, 1.0, 0.0)))
        if not ok:
            return None
        return tuple(source.matrix_world @ loc), tuple((source.matrix_world.to_3x3() @ nrm).normalized())

    # (x, 키 비율) 쌍 — 메카닉(SD_메카닉) 정면 기준: 오른 허벅지 앞 세로, 왼 허벅지 사선(음의 반쪽), 가슴 판 가로
    # 배가로는 중앙선을 가로지른다 — 반쪽 경로는 대칭면 근처를 잘라 내므로 커버리지는 보고만 한다
    specs = {"허벅지세로": ((0.20, 0.42), (0.20, 0.30)), "왼허벅지사선": ((-0.14, 0.42), (-0.26, 0.31)),
             "배가로": ((-0.18, 0.55), (0.22, 0.55))}
    lines = []
    for name, (pa, pb) in specs.items():
        a, b = front_hit(*pa), front_hit(*pb)
        if a is None or b is None:
            print(f"INFO {name}: 표면을 맞히지 못함")
            continue
        path = [a[0]] + ring_guides.surface_segment(source, a[0], b[0], a[1], b[1])
        print(f"INFO {name}: 점 {len(path)} · 길이 {edge_line.length(path):.3f}")
        lines.append((name, path))
    check("파일 선 3개 생성", len(lines) == 3, len(lines))
    # UI 경로 그대로: 가이드 커브를 만들고 패널·연산자가 읽는 함수로 다시 읽는다
    for name, path in lines:
        guide = ring_guides.create_edge_guide(coll, source, path)
        guide.name = f"LP3D_Edge_{name}"
    read = ring_guides.edge_world_points(coll)
    check("가이드 커브 왕복", len(read) == 3 and not ring_guides.ring_guides(coll), [len(p) for _n, p in read])
    check("가이드는 열린 커브", all(not g.data.splines[0].use_cyclic_u for g in ring_guides.edge_guides(coll)))
    lines = [(name.replace("LP3D_Edge_", ""), points) for name, points in read]
    evaluate(source, coll, True, lines)


if any(o.get('lp3d_gen') for o in bpy.data.objects if o.type == 'MESH'):
    from_file()
else:
    synthetic()
print("RESULT " + ("FAIL " + ", ".join(failures) if failures else "ALL PASS"))
