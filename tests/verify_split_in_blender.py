# 좌우 분할(SPLIT) 리토폴로지 헤드리스 검증 — 비대칭 원본에서 중앙선(X=0) 와이어가 남는지.
#
#   ./scripts/dev_run.sh --background "<자동 저장 .blend>" --python tests/verify_split_in_blender.py
#
# 환경변수: LP3D_SPLIT_TARGET(목표 면수, 기본 4000), LP3D_SPLIT_SYMMETRY(1/0, 기본 1 — 비대칭 원본이면 분할로 떨어진다)
# 자동 저장 .blend 는 라이브러리 형식이라 씬이 비어 있다 — 결과 컬렉션을 씬에 링크한 뒤 돈다.
import importlib
import os
import sys
import time

import bmesh
import bpy

ADDON = "bl_ext.user_default.lp3d_modelmaker"
if ADDON not in sys.modules:
    print("SKIP 애드온이 로드되지 않았습니다 (dev_run.sh 로 실행)")
    sys.exit(0)
quadretopo = importlib.import_module(ADDON + ".lowpoly.quadretopo")
ring_guides = importlib.import_module(ADDON + ".ui.ring_guides")

TARGET = int(os.environ.get("LP3D_SPLIT_TARGET", "4000"))
SYMMETRY = os.environ.get("LP3D_SPLIT_SYMMETRY", "1") == "1"
failures = []


def check(label, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def main():
    source = next((o for o in bpy.data.objects if o.type == 'MESH' and o.get('lp3d_gen')
                   and not o.get(quadretopo.RETOPO_OF_KEY)), None)
    if source is None:
        print("SKIP 생성 정보가 있는 원본 메시가 없습니다")
        return
    coll = next((c for c in bpy.data.collections if source.name in c.objects), None)
    if coll is None:
        print("SKIP 원본 컬렉션이 없습니다")
        return
    scene = bpy.context.scene
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    bpy.context.view_layer.update()
    guides = ring_guides.guide_world_points(coll)
    print(f"INFO 원본 {source.name}: {len(source.data.polygons):,}면 · 링 가이드 {len(guides)}개 · 목표 {TARGET} · 대칭 요청 {SYMMETRY}")
    started = time.perf_counter()
    result = quadretopo.retopologize(source, coll, target_faces=TARGET, symmetry=SYMMETRY, texture_size=512,
                                     normal_map=False, progress=lambda t: print(f"  .. {t}"), ring_guides=guides)
    print(f"INFO 결과: {result['method']} · 면 {result['faces']} · 쿼드 {result['quads']} · 링 {result['rings']} · "
          f"대칭 {result['symmetry']} · 분할 {result['split']} · 비대칭도 {result['mirror_mismatch']} · {result['seconds']}s")
    for note in result['notes']:
        print(f"  note: {note}")
    obj = result['obj']
    check("좌우 분할로 깔림", result['split'], f"symmetry={result['symmetry']} split={result['split']}")
    check("쿼드 비율 90% 이상", result['quads'] >= result['faces'] * 0.9, f"{result['quads']}/{result['faces']}")

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    scale = quadretopo._local_size(obj)
    tol = quadretopo.PLANE_TOLERANCE_RATIO * scale
    boundary = sum(1 for e in bm.edges if len(e.link_faces) == 1)
    for loop in quadretopo._boundary_loops(bm):
        verts = {v for e in loop for v in e.verts}
        cx = sum(v.co.x for v in verts) / len(verts)
        cz = sum(v.co.z for v in verts) / len(verts)
        print(f"INFO 경계 루프: 엣지 {len(loop)} · 중심 x {cx:.3f} z {cz:.3f} · |x|<tol {sum(1 for v in verts if abs(v.co.x) < tol)}/{len(verts)}")
    nonmanifold = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    check("구멍 없음 (경계 엣지 0)", boundary == 0, boundary)
    check("비매니폴드 엣지 0", nonmanifold == 0, nonmanifold)
    center = [v for v in bm.verts if abs(v.co.x) < tol]
    center_edges = [e for e in bm.edges if all(abs(v.co.x) < tol for v in e.verts)]
    zs = [v.co.z for v in center]
    # 기대 범위는 원본의 X=0 단면이 걸치는 높이 — 다리가 벌어진 캐릭터는 몸통·머리만 중앙선을 지난다
    source_zs = [v.co.z for v in source.data.vertices if abs(v.co.x) < max(tol, 0.004)]
    expected = (max(source_zs) - min(source_zs)) if source_zs else obj.dimensions.z
    span = (max(zs) - min(zs)) / expected if zs else 0.0
    check("중앙선(X=0) 정점이 원본 중앙 단면 높이의 80% 이상에 걸침", span >= 0.8,
          f"정점 {len(center)} · 엣지 {len(center_edges)} · 범위 {span:.2f} (원본 단면 높이 {expected:.2f})")
    # 중앙선 엣지가 사슬로 이어져 있는지 — 가장 긴 연결 성분
    adjacency = {}
    for e in center_edges:
        a, b = e.verts
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    seen, longest = set(), 0
    for start in adjacency:
        if start in seen:
            continue
        stack, size = [start], 0
        seen.add(start)
        while stack:
            v = stack.pop()
            size += 1
            for n in adjacency[v]:
                if n not in seen:
                    seen.add(n)
                    stack.append(n)
        longest = max(longest, size)
    check("중앙선 엣지가 한 사슬로 이어짐 (가장 긴 성분이 중앙 정점의 70% 이상)",
          center and longest >= len(center) * 0.7, f"{longest}/{len(center)}")
    triangles = sum(1 for f in bm.faces if len(f.verts) == 3)
    center_tris = sum(1 for f in bm.faces if len(f.verts) == 3 and any(abs(v.co.x) < tol for v in f.verts))
    print(f"INFO 삼각형 {triangles} (중앙선에 닿은 것 {center_tris})")
    bm.free()
    print(f"INFO 총 {time.perf_counter() - started:.1f}s")


main()
print("RESULT " + ("FAIL " + ", ".join(failures) if failures else "ALL PASS"))
