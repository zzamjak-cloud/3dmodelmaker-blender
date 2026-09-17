# 이미지→3D 셰이프(GLB) 가져오기와 리토폴로지 — 하이폴리 "찰흙" 메시를 게임용 메시로
#
# 순서: GLB 임포트 → 가장 큰 연결 덩어리만 남김(떠다니는 조각 제거) → 복셀 리메시로 표면 통일 →
# QuadriFlow 리토폴로지(실패하면 데시메이트) → 키·방향·바닥 정규화 → 세션 컬렉션에 링크.
# 전부 bmesh/data API 또는 컨텍스트 override로 돈다 — 타이머 콜백에서 호출된다.
import bmesh
import bpy
from mathutils import Vector

from .names import safe_id_name


def _override(obj):
    win = bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None
    kwargs = dict(object=obj, active_object=obj, selected_objects=[obj],
                  selected_editable_objects=[obj])
    if win is not None:
        kwargs["window"] = win
    return bpy.context.temp_override(**kwargs)


def import_glb(path: str) -> list:
    """GLB를 가져와 새 메시 오브젝트 목록을 돌려준다 (씬 컬렉션 링크는 호출자가 정리)."""
    before = set(bpy.data.objects)
    with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
        bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == 'MESH']
    for o in new:
        if o.type != 'MESH':
            bpy.data.objects.remove(o, do_unlink=True)  # 빈 노드·카메라 등
    return meshes


def keep_largest_island(obj, min_ratio: float = 0.0) -> int:
    """가장 큰 연결 덩어리만 남긴다. 제거한 덩어리 수를 돌려준다.

    이미지→3D 결과에는 몸에서 떨어진 작은 파편이 수십 개 붙어 나온다."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    seen, islands = set(), []
    for v in bm.verts:
        if v.index in seen:
            continue
        stack, comp = [v], []
        while stack:
            cur = stack.pop()
            if cur.index in seen:
                continue
            seen.add(cur.index)
            comp.append(cur)
            for e in cur.link_edges:
                n = e.other_vert(cur)
                if n.index not in seen:
                    stack.append(n)
        islands.append(comp)
    if len(islands) <= 1:
        bm.free()
        return 0
    islands.sort(key=len, reverse=True)
    keep = set(v.index for v in islands[0])
    # 큰 부속(두 번째 덩어리가 본체의 일정 비율 이상 — 분리된 무기 등)은 남긴다
    for comp in islands[1:]:
        if min_ratio and len(comp) >= len(islands[0]) * min_ratio:
            keep.update(v.index for v in comp)
    removed = sum(1 for comp in islands[1:] if comp[0].index not in keep)
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if v.index not in keep], context='VERTS')
    bm.to_mesh(obj.data)
    bm.free()
    return removed


def voxel_remesh(obj, voxel_size: float) -> None:
    """복셀 리메시 — 겹친 셸·자기교차를 하나의 닫힌 표면으로 녹인다."""
    obj.data.remesh_voxel_size = max(float(voxel_size), 0.002)
    obj.data.remesh_voxel_adaptivity = 0.0
    obj.data.use_remesh_fix_poles = True
    with _override(obj):
        bpy.ops.object.voxel_remesh()


def retopo(obj, target_faces: int) -> str:
    """QuadriFlow 리토폴로지, 실패하면 데시메이트. 쓴 방법 이름을 돌려준다."""
    before = len(obj.data.polygons)
    try:
        with _override(obj):
            bpy.ops.object.quadriflow_remesh(target_faces=int(target_faces),
                                             use_preserve_sharp=False,
                                             use_mesh_symmetry=False, seed=1)
    except RuntimeError:
        pass
    if len(obj.data.polygons) != before:
        return "quadriflow"
    # QuadriFlow는 셸이 여러 개거나 노멀이 어긋나면 조용히 아무것도 안 한다
    obj.data.calc_loop_triangles()
    tris = len(obj.data.loop_triangles)
    ratio = min(1.0, (int(target_faces) * 2.0) / max(tris, 1))
    mod = obj.modifiers.new("LP3D_Decimate", 'DECIMATE')
    mod.ratio = ratio
    with _override(obj):
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return "decimate"


def importance_weights(obj, head_frac: float = 0.0, curvature_gain: float = 6.0) -> list:
    """정점별 중요도 0~1 — 곡률이 높거나 머리 영역이면 1에 가깝다.

    곡률은 정점 노멀과 이웃 노멀의 평균 편차다(이마·눈두덩·주둥이·손가락은 크고, 팔뚝·허벅지·
    갑옷 판은 작다). head_frac>0이면 바운딩 박스 위쪽 그 비율(예: 인간형 0.24)을 머리로 보고
    무조건 1로 둔다 — 얼굴은 곡률이 낮은 뺨도 촘촘해야 표정 변형이 깨지지 않는다."""
    mesh = obj.data
    mesh.calc_loop_triangles()
    normals = [v.normal.copy() for v in mesh.vertices]
    neighbors = [[] for _ in mesh.vertices]
    for e in mesh.edges:
        a, b = e.vertices
        neighbors[a].append(b)
        neighbors[b].append(a)
    zs = [v.co.z for v in mesh.vertices]
    zmin, zmax = min(zs), max(zs)
    head_z = zmax - (zmax - zmin) * float(head_frac) if head_frac > 0 else float("inf")
    weights = []
    for i, v in enumerate(mesh.vertices):
        n = normals[i]
        dev = 0.0
        if neighbors[i]:
            dev = sum(1.0 - max(-1.0, min(1.0, n.dot(normals[j]))) for j in neighbors[i]) / len(neighbors[i])
        w = min(1.0, dev * curvature_gain)
        if v.co.z >= head_z:
            w = 1.0
        weights.append(w)
    # 이웃 평균으로 한 번 부드럽게 — 밀도 경계가 톱니처럼 끊기지 않게
    smooth = []
    for i in range(len(weights)):
        ring = neighbors[i]
        smooth.append((weights[i] + sum(weights[j] for j in ring)) / (1 + len(ring)) if ring else weights[i])
    return smooth


def adaptive_unsubdivide(obj, weights: list, threshold: float = 0.35, iterations: int = 1) -> int:
    """중요도가 낮은 정점 영역만 un-subdivide 해서 밀도를 낮춘다. 줄어든 면 수를 돌려준다.

    QuadriFlow는 밀도가 균일하다. 균일한 고밀도 쿼드에서 팔·다리·갑옷 판처럼 평평한 곳만
    격자를 한 단계 풀면(4면→1면) 얼굴은 촘촘하고 몸통은 성긴, 리깅용 밀도 분포가 된다."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    before = len(bm.faces)
    # 2단계: 아주 평평한 곳(팔뚝·허벅지·갑옷 판)은 두 번(16면→1면), 중간은 한 번(4면→1면).
    # 한 번만 풀면 머리와 몸통의 밀도 차가 눈에 띄지 않았다(실측).
    very_low = [bm.verts[i] for i, w in enumerate(weights) if w < threshold * 0.5]
    if len(very_low) >= 8:
        bmesh.ops.unsubdivide(bm, verts=very_low, iterations=int(iterations) + 1)
    bm.verts.ensure_lookup_table()
    # 첫 단계 뒤 정점 인덱스가 바뀌므로 위치로 다시 고른다 — 남은 정점 중 임계 아래
    kd_weights = _weights_by_position(obj, weights)
    low = [v for v in bm.verts if kd_weights(v.co) < threshold]
    if len(low) >= 8:
        bmesh.ops.unsubdivide(bm, verts=low, iterations=int(iterations))
    # 경계에서 생기는 삼각형 쌍은 가능하면 쿼드로 합친다
    bmesh.ops.join_triangles(bm, faces=bm.faces, angle_face_threshold=0.7,
                             angle_shape_threshold=0.7)
    bm.to_mesh(obj.data)
    after = len(bm.faces)
    bm.free()
    return before - after


def _weights_by_position(obj, weights: list):
    """원본 정점 위치 → 중요도 조회 함수 (un-subdivide로 인덱스가 바뀐 뒤에도 쓸 수 있게)."""
    from mathutils import kdtree
    coords = [v.co.copy() for v in obj.data.vertices]
    tree = kdtree.KDTree(len(coords))
    for i, co in enumerate(coords):
        tree.insert(co, i)
    tree.balance()

    def lookup(co):
        _, idx, _ = tree.find(co)
        return weights[idx] if idx is not None else 1.0
    return lookup


def normalize(obj, height: float, face_axis: str = '-Y') -> None:
    """키를 height(m)에 맞추고 발바닥을 z=0, 중심을 X=Y=0에 둔다.

    glTF 임포트 결과는 원점 중심·1m 안팎 크기다. face_axis는 정면이 향하는 축으로,
    Hunyuan3D 출력은 glTF +Z(정면)가 Blender -Y로 들어와 기본값이 맞는다."""
    bpy.context.view_layer.update()
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    zmin, zmax = min(p.z for p in pts), max(p.z for p in pts)
    span = max(zmax - zmin, 1e-6)
    scale = float(height) / span
    obj.scale = (obj.scale.x * scale, obj.scale.y * scale, obj.scale.z * scale)
    bpy.context.view_layer.update()
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    cx = (min(p.x for p in pts) + max(p.x for p in pts)) / 2
    cy = (min(p.y for p in pts) + max(p.y for p in pts)) / 2
    obj.location.x -= cx
    obj.location.y -= cy
    obj.location.z -= min(p.z for p in pts)
    # 트랜스폼을 메시에 굽는다 — 이후 단계(언랩·베이크·익스포트)가 단위 변환을 전제한다
    with _override(obj):
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def decimate(obj, target_tris: int) -> None:
    """데시메이트(collapse)로 목표 트라이 수까지 줄인다 — 실루엣·디테일을 가장 잘 지킨다.

    마칭큐브 출력은 매니폴드가 아니어서 QuadriFlow가 거부하고, 복셀 리메시를 거치면
    얼굴·털 같은 작은 디테일이 뭉개진다. 게임용 트라이 메시가 목표라면 데시메이트가 낫다."""
    obj.data.calc_loop_triangles()
    tris = len(obj.data.loop_triangles)
    if tris <= target_tris:
        return
    mod = obj.modifiers.new("LP3D_Decimate", 'DECIMATE')
    mod.ratio = float(target_tris) / float(tris)
    mod.use_collapse_triangulate = True
    with _override(obj):
        bpy.ops.object.modifier_apply(modifier=mod.name)


def process_glb(path: str, name: str, collection, height: float = 1.8,
                target_faces: int = 12000, voxel_size: float = 0.012,
                method: str = 'QUADRIFLOW', adaptive: bool = False,
                head_frac: float = 0.0) -> dict:
    """GLB 한 개를 게임용 메시 오브젝트 하나로 만들어 collection에 넣는다. 통계 dict 반환.

    method='QUADRIFLOW'(기본): 조각 제거 → 복셀 리메시 → QuadriFlow 쿼드 → 하이폴리 슈링크랩으로
    디테일 복원. adaptive=True면 평평한 영역을 un-subdivide로 성기게 만들어 밀도 차등을 주지만
    (head_frac은 무조건 촘촘하게 둘 머리 비율), 경계에 삼각형이 생겨 와이어가 지저분하다 —
    실측 결과 기본은 끈다. 애니메이션용 밀도·와이어 흐름은 템플릿 베이스 메시 방식이 필요하다.
    method='DECIMATE': 조각 제거 → 데시메이트. 빠르지만 삼각형 그대로다."""
    meshes = import_glb(path)
    if not meshes:
        raise RuntimeError("GLB에 메시가 없다")
    obj = meshes[0]
    for extra in meshes[1:]:  # 여러 조각이면 하나로 합친다
        bpy.data.objects.remove(extra, do_unlink=True)
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    collection.objects.link(obj)
    obj.name = safe_id_name(name)
    obj.data.name = obj.name
    obj.data.materials.clear()
    raw_tris = len(obj.data.polygons)
    removed = keep_largest_island(obj)
    reduced = 0
    if str(method).upper() == 'QUADRIFLOW':
        # 하이폴리 사본을 남겨 두고 저폴리를 만든 뒤 슈링크랩으로 표면 디테일을 되찾는다 —
        # 복셀 리메시·QuadriFlow가 뭉갠 얼굴·털 굴곡이 돌아온다 (실측: 15k 쿼드에서 원본과 구분 어려움)
        hi = obj.copy()
        hi.data = obj.data.copy()
        collection.objects.link(hi)
        zs = [v.co.z for v in obj.data.vertices]
        height_units = max(max(zs) - min(zs), 1e-6)
        voxel_remesh(obj, height_units / 350.0)  # 복셀 상자 350칸 — 눈·주둥이가 살아남는 해상도
        remeshed = len(obj.data.polygons)
        # 밀도 분포: 균일 고밀도(목표 x2.5)로 뽑은 뒤 평평한 영역만 un-subdivide(4면→1면).
        # 얼굴·손·곡률 높은 곳은 촘촘하게, 팔·다리·갑옷 판은 성기게 — 리깅용 분포
        dense_target = int(target_faces * 2.5) if adaptive else int(target_faces)
        method = retopo(obj, dense_target)
        reduced = 0
        if adaptive and method == "quadriflow":
            weights = importance_weights(obj, head_frac=head_frac)
            reduced = adaptive_unsubdivide(obj, weights)
            method = "quadriflow+adaptive"
        mod = obj.modifiers.new("LP3D_Shrinkwrap", 'SHRINKWRAP')
        mod.target = hi
        mod.wrap_method = 'NEAREST_SURFACEPOINT'
        with _override(obj):
            bpy.ops.object.modifier_apply(modifier=mod.name)
        bpy.data.objects.remove(hi, do_unlink=True)
    else:
        remeshed = len(obj.data.polygons)
        decimate(obj, int(target_faces) * 2)  # target_faces는 쿼드 기준 — 트라이는 2배
        method = "decimate"
    for p in obj.data.polygons:
        p.use_smooth = True
    normalize(obj, height)
    obj.data.calc_loop_triangles()
    quads = sum(1 for p in obj.data.polygons if len(p.vertices) == 4)
    return {"obj": obj, "raw_faces": raw_tris, "floaters_removed": removed,
            "remeshed_faces": remeshed, "method": method, "reduced_faces": reduced,
            "faces": len(obj.data.polygons), "quads": quads,
            "tris": len(obj.data.loop_triangles)}
