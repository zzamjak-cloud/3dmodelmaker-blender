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
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == 'MESH']
    for o in new:
        if o.type != 'MESH':
            for child in list(o.children):
                world = child.matrix_world.copy()
                child.parent = None
                child.matrix_world = world
            bpy.data.objects.remove(o, do_unlink=True)  # 빈 노드·카메라 등
    return meshes


# 가장 큰 덩어리 대비 이 비율 이상인 셸은 파편이 아니라 본체의 일부다 — 실측(2026-09-18): TRELLIS.2 캐릭터가
# 상체(259,941면) + 하체·다리(83,929면, 32%) 두 셸로 와서, 가장 큰 것만 남기면 다리가 통째로 사라지고
# 남은 상체가 키 기준 정규화로 늘어났다. 실제 파편(귀·머리카락 조각)은 1% 미만이다.
KEEP_ISLAND_RATIO = 0.05


def keep_largest_island(obj, min_ratio: float = KEEP_ISLAND_RATIO) -> int:
    """작은 파편 덩어리를 지운다 — 가장 큰 덩어리와 그 min_ratio 이상인 덩어리는 남긴다. 제거한 덩어리 수를 돌려준다.

    이미지→3D 결과에는 몸에서 떨어진 작은 파편이 수십 개 붙어 나온다. 반대로 다리·부츠·들고 있는 무기가
    본체와 떨어진 큰 셸로 오는 경우도 있어, 크기로 구분하지 않으면 멀쩡한 부위를 지운다."""
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


QF_MAX_INPUT_RATIO = 6.0   # 입력 면수 / 목표 면수 상한 — 실측(2026-09-18): 28배에서 QuadriFlow 가 메모리 폭주로 SIGKILL,
                           # 6~12배는 'Remeshing failed', 3배 이하 성공. 가드가 없으면 Blender 프로세스 자체가 죽는다.


def _quadriflow_once(obj, target_faces: int) -> bool:
    """QuadriFlow 한 번 시도. 면수가 바뀌었으면 성공. (실패 시 조용히 아무것도 안 하거나 RuntimeError)"""
    before = len(obj.data.polygons)
    if before > int(target_faces) * QF_MAX_INPUT_RATIO:
        return False
    try:
        with _override(obj):
            bpy.ops.object.quadriflow_remesh(target_faces=int(target_faces),
                                             use_preserve_sharp=False,
                                             use_mesh_symmetry=False, seed=1)
    except RuntimeError:
        return False
    return len(obj.data.polygons) != before


def _dissolve_degenerate(obj, dist: float) -> None:
    """복셀 리메시가 남기는 극소 엣지를 녹인다 — QuadriFlow 사전 검사가 이 엣지에서 '비매니폴드'로 거절한다."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.dissolve_degenerate(bm, dist=dist, edges=bm.edges)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def _division_ladder(obj, hi, extent: float, target_faces: int) -> list:
    """2차 시도의 복셀 분할 수 사다리. 90분할로 한 번 재서 입력 면수가 목표의 2.5배쯤 되도록 낮춘다.

    부츠처럼 작은 셸은 90분할에서도 목표의 28배 면수가 나와 QuadriFlow 가 죽었다(QF_MAX_INPUT_RATIO 참고).
    복셀 면수는 분할 수의 제곱에 비례하므로 sqrt 로 맞추고, 그 밀도와 0.75배·0.5배를 차례로 시도한다."""
    obj.data = hi.data.copy()
    keep_largest_island(obj)
    voxel_remesh(obj, extent / 90.0)
    keep_largest_island(obj)
    faces = max(len(obj.data.polygons), 1)
    want = max(int(target_faces) * 2.5, 1.0)
    base = 90 if faces <= want * 1.2 else max(12, int(90 * (want / faces) ** 0.5))
    ladder = []
    for d in (base, int(base * 0.75), int(base * 0.5)):
        d = max(12, d)
        if d not in ladder:
            ladder.append(d)
    return ladder


def retopo_robust(obj, hi, target_faces: int, height_units: float) -> tuple:
    """QuadriFlow를 두 단계로 시도하고 실패하면 데시메이트. (방법 이름, 실제 리메시 면수) 반환.

    1차: 현재(350분할) 리메시 그대로. 2차: 하이폴리 사본에서 90분할로 다시 리메시 → 퇴화 엣지 정리 →
    최대 치수 10 단위로 확대 → QuadriFlow → 원래 크기로. 실측(2026-09-17): Blender QuadriFlow 사전 검사는
    절대 엡실론(~1e-4)을 쓰기 때문에 단위 큐브 스케일의 고밀도 리메시(최소 엣지 1e-5~1e-6)에서는 완벽한
    매니폴드라도 거절한다. 350분할은 어떤 스케일에서도 통과하지 않았고, 90분할 + ×10(또는 dissolve 2%)은 통과했다.
    잃는 디테일은 뒤따르는 하이폴리 슈링크랩이 되찾는다."""
    from mathutils import Matrix
    remeshed_first = len(obj.data.polygons)
    if _quadriflow_once(obj, target_faces):
        return "quadriflow", remeshed_first
    # 2차 — 하이폴리에서 **최대 치수 기준** 90분할로 다시 리메시하고 크기를 키워가며 시도.
    # 실측(2026-09-17): 최대치수/90 복셀 + 최대치수 ×10~×40 에서 통과. 높이 기준으로 나누면 옆으로 긴 대상이
    # 1.5~2배 촘촘해져 180분할급이 되고, 그 밀도는 어떤 스케일에서도 통과하지 않았다.
    # dissolve_degenerate 는 n-gon 을 만들어 'Remeshing failed' 를 유발했으므로 쓰지 않는다.
    # QuadriFlow 성공은 밀도에 민감하다(같은 메시가 45k 면에서는 통과, 69k 면에서는 'Remeshing failed').
    # 그래서 분할 수를 90 → 64 → 45 로 낮추는 사다리로 내려가며, 각 밀도에서 크기를 ×10/×20/×40 으로 키워 시도한다.
    coords = [v.co for v in hi.data.vertices]
    extent = max(max(c[a] for c in coords) - min(c[a] for c in coords) for a in range(3)) if coords else 1.0
    extent = max(extent, 1e-6)
    remeshed = 0
    for divisions in _division_ladder(obj, hi, extent, target_faces):
        for target_size in (10.0, 20.0, 40.0):
            obj.data = hi.data.copy()
            keep_largest_island(obj)
            voxel_remesh(obj, extent / divisions)
            keep_largest_island(obj)
            remeshed = len(obj.data.polygons)
            scale = target_size / extent
            obj.data.transform(Matrix.Scale(scale, 4))
            ok = _quadriflow_once(obj, target_faces)
            obj.data.transform(Matrix.Scale(1.0 / scale, 4))
            obj.data.update()
            if ok:
                return "quadriflow", remeshed
    obj.data = hi.data.copy()
    keep_largest_island(obj)
    voxel_remesh(obj, extent / 90.0)
    keep_largest_island(obj)
    remeshed = len(obj.data.polygons)
    # 3차 — 데시메이트 (기존 폴백)
    obj.data.calc_loop_triangles()
    tris = len(obj.data.loop_triangles)
    mod = obj.modifiers.new("LP3D_Decimate", 'DECIMATE')
    mod.ratio = min(1.0, (int(target_faces) * 2.0) / max(tris, 1))
    with _override(obj):
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return "decimate", remeshed


def _face_islands(bm) -> list:
    """bmesh 면의 연결 요소(면 인덱스 리스트) 목록."""
    bm.faces.ensure_lookup_table()
    seen = set()
    islands = []
    for f in bm.faces:
        if f.index in seen:
            continue
        stack, comp = [f], []
        while stack:
            cur = stack.pop()
            if cur.index in seen:
                continue
            seen.add(cur.index)
            comp.append(cur.index)
            for e in cur.edges:
                for lf in e.link_faces:
                    if lf.index not in seen:
                        stack.append(lf)
        islands.append(comp)
    return islands


def split_islands(obj, collection, min_face_ratio: float = 0.002) -> list:
    """오브젝트를 연결 요소(셸)별 오브젝트로 나눈다. 전체 면수의 min_face_ratio 미만인 극소 파편은 버린다.

    부품 보존 모드에서 의상 GLB 는 몸통·소매 셸 + 부츠 셸 둘처럼 여러 셸로 온다. 통째로 QuadriFlow 에 넣으면
    작은 셸이 사라지고(실측: 부츠 소실 → 시트 높이로 정규화하며 몸통만 2.8배 확대), 폴백의 섬 제거도 지운다."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    islands = _face_islands(bm)
    bm.free()
    total = max(len(obj.data.polygons), 1)
    islands = [c for c in islands if len(c) >= max(1, int(total * min_face_ratio))]
    if len(islands) <= 1:
        return [obj]
    out = []
    for i, comp in enumerate(sorted(islands, key=len, reverse=True)):
        keep = set(comp)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.index not in keep], context='FACES')
        me = bpy.data.meshes.new(f"{obj.data.name}_island{i:02d}")
        bm.to_mesh(me)
        bm.free()
        piece = bpy.data.objects.new(f"{obj.name}_island{i:02d}", me)
        piece.matrix_world = obj.matrix_world.copy()
        collection.objects.link(piece)
        out.append(piece)
    bpy.data.objects.remove(obj, do_unlink=True)
    return out


def _mesh_area(obj) -> float:
    return sum(p.area for p in obj.data.polygons) or 1e-9


def _retopo_islands(obj, hi, collection, target_faces: int, height_units: float, name: str):
    """부품 보존용: 셸별로 복셀 리메시 → QuadriFlow → 하이폴리 슈링크랩 → 다시 한 오브젝트로 합친다.

    면수 예산은 셸 표면적 비율로 배분한다(하한 500). 복셀 크기는 전체 기준으로 통일해 셸 간 밀도가 맞게 한다."""
    pieces = split_islands(obj, collection)
    if len(pieces) == 1:
        obj = pieces[0]
        voxel_remesh(obj, height_units / 350.0)
        method, remeshed = retopo_robust(obj, hi, target_faces, height_units)
        _shrinkwrap(obj, hi)
        bpy.data.objects.remove(hi, do_unlink=True)
        return obj, method, remeshed
    bpy.data.objects.remove(hi, do_unlink=True)   # 셸별 하이폴리 사본을 따로 만든다
    total_area = sum(_mesh_area(p) for p in pieces)
    methods, remeshed = [], 0
    for piece in pieces:
        hi_piece = piece.copy()
        hi_piece.data = piece.data.copy()
        collection.objects.link(hi_piece)
        budget = max(500, int(round(target_faces * _mesh_area(piece) / total_area)))
        voxel_remesh(piece, height_units / 350.0)
        method, n = retopo_robust(piece, hi_piece, budget, height_units)
        methods.append(method)
        remeshed += n
        _shrinkwrap(piece, hi_piece)
        bpy.data.objects.remove(hi_piece, do_unlink=True)
    first = pieces[0]
    with bpy.context.temp_override(object=first, active_object=first,
                                   selected_objects=pieces, selected_editable_objects=pieces):
        bpy.ops.object.join()
    first.name = safe_id_name(name)
    first.data.name = first.name
    method = "quadriflow" if all(m == "quadriflow" for m in methods) else "quadriflow+decimate"
    return first, method, remeshed


def _shrinkwrap(obj, target) -> None:
    """리토폴로지 결과를 하이폴리 표면에 붙여 복셀 리메시·QuadriFlow 가 뭉갠 디테일을 되찾는다."""
    mod = obj.modifiers.new("LP3D_Shrinkwrap", 'SHRINKWRAP')
    mod.target = target
    mod.wrap_method = 'NEAREST_SURFACEPOINT'
    with _override(obj):
        bpy.ops.object.modifier_apply(modifier=mod.name)


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


def _slice_prefit(obj, target, slices: int = 24, smooth: int = 2) -> None:
    """높이(Z) 슬라이스마다 템플릿의 X·Y 폭과 중심을 타깃 실루엣에 맞춘다.

    각 슬라이스에서 타깃 정점의 x·y 범위(min·max)와 템플릿 정점의 범위를 재고, 템플릿
    정점을 그 슬라이스 안에서 선형으로 다시 배치한다(왼쪽 끝→왼쪽 끝, 오른쪽 끝→오른쪽 끝).
    A-포즈 인간형끼리는 팔·머리·어깨 폭이 이렇게 대체로 맞고, 이어지는 슈링크랩이 세부를
    붙인다. 슬라이스 사이 값은 이웃 평균으로 smooth번 고른다."""
    import bisect
    tv = [target.matrix_world @ v.co for v in target.data.vertices]
    ov = [obj.matrix_world @ v.co for v in obj.data.vertices]
    z_lo = min(p.z for p in tv)
    z_hi = max(p.z for p in tv)
    span = max(z_hi - z_lo, 1e-6)
    n = max(4, int(slices))

    def _ranges(points):
        xs = [[] for _ in range(n)]
        ys = [[] for _ in range(n)]
        for p in points:
            k = min(n - 1, max(0, int((p.z - z_lo) / span * n)))
            xs[k].append(p.x)
            ys[k].append(p.y)
        out = []
        for k in range(n):
            if xs[k]:
                out.append((min(xs[k]), max(xs[k]), min(ys[k]), max(ys[k])))
            else:
                out.append(None)
        # 빈 슬라이스는 이웃으로 채운다
        for k in range(n):
            if out[k] is None:
                j = next((i for i in range(1, n) if 0 <= k - i < n and out[k - i] or 0 <= k + i < n and out[k + i]), None)
                cand = out[k - j] if j is not None and 0 <= k - j < n and out[k - j] else (out[k + j] if j is not None and 0 <= k + j < n else None)
                out[k] = cand or (-0.1, 0.1, -0.1, 0.1)
        for _ in range(int(smooth)):
            out = [tuple((out[max(0, k - 1)][i] + out[k][i] + out[min(n - 1, k + 1)][i]) / 3 for i in range(4))
                   for k in range(n)]
        return out

    tr = _ranges(tv)
    orr = _ranges(ov)
    # 슬라이스 경계에서 튀지 않게 이웃 슬라이스와 선형 보간한다
    centers = [z_lo + span * (k + 0.5) / n for k in range(n)]
    for v, p in zip(obj.data.vertices, ov):
        k = bisect.bisect_left(centers, p.z)
        k0, k1 = max(0, k - 1), min(n - 1, k)
        t = 0.0 if k1 == k0 else max(0.0, min(1.0, (p.z - centers[k0]) / (centers[k1] - centers[k0])))
        def lerp(a, b):
            return a + (b - a) * t
        txl, txh, tyl, tyh = (lerp(tr[k0][i], tr[k1][i]) for i in range(4))
        oxl, oxh, oyl, oyh = (lerp(orr[k0][i], orr[k1][i]) for i in range(4))
        ux = (p.x - oxl) / max(oxh - oxl, 1e-6)
        uy = (p.y - oyl) / max(oyh - oyl, 1e-6)
        v.co = Vector((txl + ux * (txh - txl), tyl + uy * (tyh - tyl), p.z))
    obj.data.update()


def fit_template(obj, target, passes: int = 4) -> None:
    """베이스 메시(obj)를 하이폴리 셰이프(target)에 단계적으로 입힌다 (래핑).

    한 번에 100% 투영하면 팔·다리처럼 위치가 어긋난 부위에서 루프가 뒤엉킨다. 그래서
    ① 노멀 방향 투영을 약하게(0.5) → 스무딩으로 루프 정리 → ② 최근접 표면 투영을 점점
    강하게 → 마지막에 1.0으로 표면에 붙이고 가벼운 스무딩으로 마무리한다. 얼굴·관절 루프의
    상대 배치가 유지된 채 실루엣만 셰이프를 따라간다. 자세가 크게 다르면(A-포즈 vs 팔 내림)
    수동 보정이 필요하다."""
    # 초기 정렬: 템플릿 바운딩 박스를 셰이프 박스에 축별로 맞춘다 (폭·두께·키). 겹침이
    # 클수록 최근접 투영이 엉뚱한 면으로 튀지 않는다.
    bpy.context.view_layer.update()
    src = [target.matrix_world @ Vector(c) for c in target.bound_box]
    dst = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    # 키(Z)로만 균일 스케일 — 축별로 늘리면 주둥이·꼬리가 있는 셰이프의 깊이(1.6m)에 맞춰
    # 템플릿(0.3m)이 5배로 찌그러져 투영이 엉킨다(실측). 중심은 X·Y 정렬, 발바닥은 Z 정렬
    s_lo = Vector([min(p[a] for p in src) for a in range(3)])
    s_hi = Vector([max(p[a] for p in src) for a in range(3)])
    d_lo = Vector([min(p[a] for p in dst) for a in range(3)])
    d_hi = Vector([max(p[a] for p in dst) for a in range(3)])
    scale = (s_hi.z - s_lo.z) / max(d_hi.z - d_lo.z, 1e-6)
    center_s = (s_lo + s_hi) / 2
    center_d = (d_lo + d_hi) / 2
    for v in obj.data.vertices:
        p = obj.matrix_world @ v.co
        v.co = Vector((center_s.x + (p.x - center_d.x) * scale,
                       center_s.y + (p.y - center_d.y) * scale,
                       s_lo.z + (p.z - d_lo.z) * scale))
    obj.matrix_world.identity()
    obj.data.update()
    # 높이 슬라이스별 실루엣 맞춤 — 팔·머리·주둥이처럼 몸통에서 떨어진 부위는 최근접 투영이
    # 닿지 못한다(템플릿 팔이 몸통 표면에 붙어 버린다, 실측). 정면·측면 폭을 슬라이스마다
    # 먼저 맞춰 템플릿 외피가 셰이프 실루엣을 덮게 한 뒤 투영한다.
    _slice_prefit(obj, target)
    # 단계적 최근접 투영: 약하게 붙이고 스무딩으로 루프를 고른 뒤 점점 강하게.
    # 노멀 방향 투영(PROJECT)은 멀리 있는 면으로 튀어 메시가 뭉개진다(실측) — 쓰지 않는다.
    # 초반은 TARGET_PROJECT(타깃 노멀 방향 투영 — 감싸기용)로 큰 형태를 잡고 스무딩을 많이,
    # 후반은 NEAREST_SURFACEPOINT로 표면에 밀착시키고 스무딩을 줄인다.
    n = max(3, int(passes))
    steps = []
    for i in range(n):
        late = i >= n // 2
        steps.append(('NEAREST_SURFACEPOINT' if late else 'TARGET_PROJECT',
                      min(1.0, 0.35 + 0.65 * (i + 1) / n),
                      max(0, (n - i) * 2 - 1)))
    # RNA 포인터(vg)는 모디파이어를 추가하면 무효가 될 수 있다 — 이름으로 매번 다시 찾는다
    vg_name = 'lp3d_template_fit'
    obj.vertex_groups.new(name=vg_name)
    all_idx = list(range(len(obj.data.vertices)))
    for method, factor, smooth_iters in steps:
        obj.vertex_groups[vg_name].add(all_idx, float(factor), 'REPLACE')
        mod = obj.modifiers.new('LP3D_TemplateFit', 'SHRINKWRAP')
        mod.target = target
        mod.wrap_method = method
        mod.vertex_group = vg_name
        with _override(obj):
            bpy.ops.object.modifier_apply(modifier=mod.name)
        if smooth_iters:
            sm = obj.modifiers.new('LP3D_TemplateSmooth', 'SMOOTH')
            sm.factor = 0.5
            sm.iterations = int(smooth_iters)
            with _override(obj):
                bpy.ops.object.modifier_apply(modifier=sm.name)
    # 마지막으로 표면에 완전히 붙인다 — 스무딩이 살짝 띄운 만큼 되돌린다
    mod = obj.modifiers.new('LP3D_TemplateFit', 'SHRINKWRAP')
    mod.target = target
    mod.wrap_method = 'NEAREST_SURFACEPOINT'
    with _override(obj):
        bpy.ops.object.modifier_apply(modifier=mod.name)
    obj.vertex_groups.remove(obj.vertex_groups[vg_name])


def normalize(obj, height: float, face_axis: str = '-Y') -> None:
    """키를 height(m)에 맞추고 발바닥을 z=0, 중심을 X=Y=0에 둔다.

    glTF 임포트 결과는 원점 중심·1m 안팎 크기다. face_axis는 정면이 향하는 축으로,
    셰이프 서버 GLB는 glTF 규약(+Z 정면)이라 Blender에서 -Y로 들어와 기본값이 맞는다."""
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


def _process_glb(path: str, name: str, collection, height: float = 1.8,
                target_faces: int = 12000, voxel_size: float = 0.012,
                method: str = 'QUADRIFLOW', adaptive: bool = False,
                head_frac: float = 0.0, template_id: str = 'HUMANOID',
                preserve_parts: bool = False, normalize_result: bool = True) -> dict:
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
    # 모든 GLB 메시를 월드 변환을 유지하여 합친다.
    if len(meshes) > 1:
        with bpy.context.temp_override(object=obj, active_object=obj,
                                       selected_objects=meshes, selected_editable_objects=meshes):
            bpy.ops.object.join()
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    collection.objects.link(obj)
    obj.name = safe_id_name(name)
    obj.data.name = obj.name
    obj.data.materials.clear()
    raw_tris = len(obj.data.polygons)
    removed = 0 if preserve_parts or len(meshes) > 1 else keep_largest_island(obj)
    reduced = 0
    if str(method).upper() == 'TEMPLATE':
        from ..core.templates import load_template
        hi = obj
        obj = load_template(template_id, collection)
        # 과도한 밀도 증가를 막으면서 사용자 목표에 가까운 두 단계 이하 분할을 적용한다.
        levels = 0
        while levels < 2 and len(obj.data.polygons) * (4 ** (levels + 1)) <= int(target_faces):
            levels += 1
        if levels:
            subdivision = obj.modifiers.new('LP3D_TemplateSubdivision', 'SUBSURF')
            subdivision.levels = levels
            subdivision.render_levels = levels
            with _override(obj):
                bpy.ops.object.modifier_apply(modifier=subdivision.name)
        bpy.context.view_layer.update()
        source_points = [hi.matrix_world @ vertex.co for vertex in hi.data.vertices]
        template_points = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
        if not source_points or not template_points:
            raise ValueError('템플릿 또는 원본 메시가 비어 있습니다')
        lower = Vector(tuple(min(p[a] for p in source_points) for a in range(3)))
        upper = Vector(tuple(max(p[a] for p in source_points) for a in range(3)))
        t_lower = Vector(tuple(min(p[a] for p in template_points) for a in range(3)))
        t_upper = Vector(tuple(max(p[a] for p in template_points) for a in range(3)))
        from mathutils import Matrix
        obj.matrix_world = Matrix.Identity(4)
        scale = (upper.z - lower.z) / max(t_upper.z - t_lower.z, 1e-6)
        source_center = (lower + upper) * .5
        template_center = (t_lower + t_upper) * .5
        for vertex, point in zip(obj.data.vertices, template_points):
            vertex.co = source_center + (point - template_center) * scale
        fit_template(obj, hi)
        remeshed = len(obj.data.polygons)
        bpy.data.objects.remove(hi, do_unlink=True)
        method = 'template'
        obj.name = safe_id_name(name)
    elif str(method).upper() == 'QUADRIFLOW':
        # 하이폴리 사본을 남겨 두고 저폴리를 만든 뒤 슈링크랩으로 표면 디테일을 되찾는다 —
        # 복셀 리메시·QuadriFlow가 뭉갠 얼굴·털 굴곡이 돌아온다 (실측: 15k 쿼드에서 원본과 구분 어려움)
        hi = obj.copy()
        hi.data = obj.data.copy()
        collection.objects.link(hi)
        zs = [v.co.z for v in obj.data.vertices]
        height_units = max(max(zs) - min(zs), 1e-6)
        # 부품 보존은 GLB 안에 메시가 여러 개일 때만 복셀 리메시를 건너뛴다(합쳐진 셸이 녹아
        # 붙는다). 3분할 생성처럼 부품이 각각 한 GLB로 오면 셸 하나라 리메시해도 된다 —
        # 건너뛰면 마칭큐브 원본이 매니폴드가 아니라 QuadriFlow가 조용히 실패해 삼각형이 남는다
        # 밀도 분포: 균일 고밀도(목표 x2.5)로 뽑은 뒤 평평한 영역만 un-subdivide(4면→1면).
        # 얼굴·손·곡률 높은 곳은 촘촘하게, 팔·다리·갑옷 판은 성기게 — 리깅용 분포
        dense_target = int(target_faces * 2.5) if adaptive else int(target_faces)
        reduced = 0
        if preserve_parts:
            # 부품 GLB 는 여러 셸(몸통+부츠, 검+방패)로 온다 — 셸별로 처리해 작은 셸이 사라지지 않게 한다
            obj, method, remeshed = _retopo_islands(obj, hi, collection, dense_target, height_units, name)
        else:
            voxel_remesh(obj, height_units / 350.0)  # 복셀 상자 350칸 — 눈·주둥이가 살아남는 해상도
            method, remeshed = retopo_robust(obj, hi, dense_target, height_units)
            if adaptive and method == "quadriflow":
                weights = importance_weights(obj, head_frac=head_frac)
                reduced = adaptive_unsubdivide(obj, weights)
                method = "quadriflow+adaptive"
            _shrinkwrap(obj, hi)
            bpy.data.objects.remove(hi, do_unlink=True)
    else:
        remeshed = len(obj.data.polygons)
        decimate(obj, int(target_faces) * 2)  # target_faces는 쿼드 기준 — 트라이는 2배
        method = "decimate"
    for p in obj.data.polygons:
        p.use_smooth = True
    if normalize_result:
        normalize(obj, height)
    obj.data.calc_loop_triangles()
    quads = sum(1 for p in obj.data.polygons if len(p.vertices) == 4)
    return {"obj": obj, "raw_faces": raw_tris, "floaters_removed": removed,
            "remeshed_faces": remeshed, "method": method, "reduced_faces": reduced,
            "faces": len(obj.data.polygons), "quads": quads,
            "tris": len(obj.data.loop_triangles)}


def process_glb(path: str, name: str, collection, height: float = 1.8,
                target_faces: int = 12000, voxel_size: float = 0.012,
                method: str = 'QUADRIFLOW', adaptive: bool = False,
                head_frac: float = 0.0, template_id: str = 'HUMANOID',
                preserve_parts: bool = False, normalize_result: bool = True) -> dict:
    """GLB 전체를 처리하고 실패 시 이번 호출이 생성한 데이터만 정리한다.

    분리된 장비를 보존할 때는 복셀 결합 없이 선택한 리토폴로지를 시도한다.
    템플릿 맞춤은 비율 정렬과 부분 투영이며 완성된 자동 리깅이 아니다.
    """
    if str(method).upper() not in ('TEMPLATE', 'QUADRIFLOW', 'DECIMATE'):
        raise ValueError('지원하지 않는 리토폴로지 방법: ' + str(method))
    before_objects = set(bpy.data.objects)
    before_meshes = set(bpy.data.meshes)
    other_data = [(group, set(group)) for group in
                  (bpy.data.materials, bpy.data.images, bpy.data.armatures, bpy.data.cameras,
                   bpy.data.lights, bpy.data.actions)]
    try:
        result = _process_glb(path, name, collection, height, target_faces, voxel_size,
                              method, adaptive, head_frac, template_id, preserve_parts, normalize_result)
        result['requested_method'] = str(method).upper()
        result['fallback_reason'] = ('QuadriFlow가 결과를 만들지 못해 DECIMATE로 처리했습니다'
                                     if str(method).upper() == 'QUADRIFLOW' and result['method'] == 'decimate'
                                     else '')
        return result
    except Exception:
        for obj in set(bpy.data.objects) - before_objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        raise
    finally:
        for mesh in set(bpy.data.meshes) - before_meshes:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for group, previous in other_data:
            for item in set(group) - previous:
                if item.users == 0:
                    group.remove(item)
