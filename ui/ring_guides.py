# 링 가이드 — 표면을 클릭하거나 뷰에 직선을 그어 팔·다리·목 단면 링을 만들고, 리토폴로지가 그 위치를 절단 링으로 쓴다
#
# 손으로 원을 그리면 크기·기울기가 맞지 않아 절단 링이 조각으로 잡히거나 발등·가슴을 함께 지난다.
# 클릭 점을 지나는 평면들 중 단면 둘레가 짧고 안정적인 방향을 축으로 골라(lowpoly/ring_geometry.py)
# 실제 단면 곡선을 커브로 만들고, 절단면 양쪽 둘레 비율을 바로 계산해 패널에 보여 준다.
# 3DRemesher-Blender(addon/ring_guide.py)의 클릭 가이드를 이 애드온의 잡 컬렉션 구조에 맞춰 옮겼다.
#
# 단면은 원본이 아니라 **복셀 프록시**에서 자른다 — 셰이프 서버 메시는 셸이 수천 개이고 구멍·열린 테두리가
# 있어 단면이 닫힌 루프로 이어지지 않는다. 프록시는 리토폴로지 입력과 같은 절차(복셀 리메시 → 파편 제거 →
# 매니폴드 수리)로 굽으므로 가이드가 실제로 띠를 자를 표면과 일치한다.
import math

import bpy
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty, StringProperty
from bpy_extras import view3d_utils
from mathutils import Vector

GUIDE_PREFIX = "LP3D_Ring"
EDGE_PREFIX = "LP3D_Edge"
PROXY_FACES = 32000          # 단면용 복셀 프록시 면수 — 리토폴로지 입력 사다리의 복셀 밀도와 같다
KNIFE_CLEARANCE_EDGES = 2.0  # 붙은 단면을 옮길 때 이웃 부위와 떨어져야 하는 거리 — 추정 출력 엣지 길이의 배수
LIST_LIMIT = 8               # 패널은 리드로우마다 그려지므로 목록을 이 개수까지만 보여 준다

_OVERLAY_REGIONS = {'UI', 'TOOLS', 'HEADER', 'TOOL_HEADER', 'ASSET_SHELF', 'ASSET_SHELF_HEADER'}

_refreshing = False
_pending_refresh = set()
_proxy_cache = {}            # 원본 이름 → (메시 키, SectionMesh, 바깥 표면적)


def _geometry():
    from ..lowpoly import ring_geometry
    return ring_geometry


def _on_offset_changed(self, _context):
    """update 콜백 안에서 커브 데이터를 다시 쓰면 뎁스그래프 평가와 겹치므로 타이머로 미뤄 적용한다."""
    if _refreshing:
        return
    name = self.id_data.name
    if name in _pending_refresh:
        return
    _pending_refresh.add(name)

    def apply():
        _pending_refresh.discard(name)
        guide = bpy.data.objects.get(name)
        if guide is not None and is_guide(guide):
            refresh_ring_guide(guide)
        return None

    bpy.app.timers.register(apply, first_interval=0.0)


class LP3DRingGuideProps(bpy.types.PropertyGroup):
    is_ring: BoolProperty(name="링 가이드", default=False)
    is_edge: BoolProperty(name="엣지 선 가이드", default=False)   # 열린 선 — 리토폴로지가 이 선을 따라 와이어 엣지를 깐다
    center: FloatVectorProperty(name="중심", size=3, subtype='XYZ')
    axis: FloatVectorProperty(name="축", size=3, subtype='XYZ')
    hit: FloatVectorProperty(name="클릭 지점", size=3, subtype='XYZ')
    radius: FloatProperty(name="반지름", default=0.0)
    offset: FloatProperty(
        name="축 오프셋",
        description="축을 따라 링을 옮기고 그 위치의 단면을 다시 자른다",
        default=0.0, soft_min=-1.0, soft_max=1.0, step=1, precision=3,
        update=_on_offset_changed,
    )
    ratio: FloatProperty(name="둘레 비율", default=0.0)
    status: StringProperty(name="상태", default="")
    # 선 긋기 가이드의 드래그 범위 — 두 끝 광선 옆면(점, 안쪽 법선). 오프셋·재검사 때도 같은 범위로 자른다
    clipped: BoolProperty(name="드래그 범위로 자름", default=False)
    clip_origin_a: FloatVectorProperty(size=3, subtype='XYZ')
    clip_side_a: FloatVectorProperty(size=3, subtype='XYZ')
    clip_origin_b: FloatVectorProperty(size=3, subtype='XYZ')
    clip_side_b: FloatVectorProperty(size=3, subtype='XYZ')


def _bounds(ring):
    if not ring.clipped:
        return ()
    return ((tuple(ring.clip_origin_a), tuple(ring.clip_side_a)), (tuple(ring.clip_origin_b), tuple(ring.clip_side_b)))


def is_guide(obj) -> bool:
    return obj is not None and obj.type == 'CURVE' and obj.lp3d_ring_guide.is_ring


def ring_guides(collection) -> list:
    """컬렉션의 링 가이드 커브들."""
    if collection is None:
        return []
    return [obj for obj in collection.objects if is_guide(obj)]


def guide_world_points(collection) -> list:
    """리토폴로지에 넘길 (이름, 월드 좌표 점 열) 목록."""
    result = []
    for guide in ring_guides(collection):
        points = [guide.matrix_world @ Vector(point.co[:3])
                  for spline in guide.data.splines for point in spline.points]
        if len(points) >= 3:
            result.append((guide.name, [tuple(p) for p in points]))
    return result


def is_edge_guide(obj) -> bool:
    return obj is not None and obj.type == 'CURVE' and obj.lp3d_ring_guide.is_edge


def edge_guides(collection) -> list:
    """컬렉션의 엣지 선 가이드 커브들."""
    if collection is None:
        return []
    return [obj for obj in collection.objects if is_edge_guide(obj)]


def edge_world_points(collection) -> list:
    """리토폴로지에 넘길 엣지 선 (이름, 월드 좌표 열린 점 열) 목록."""
    result = []
    for guide in edge_guides(collection):
        points = [guide.matrix_world @ Vector(point.co[:3])
                  for spline in guide.data.splines for point in spline.points]
        if len(points) >= 2:
            result.append((guide.name, [tuple(p) for p in points]))
    return result


def create_edge_guide(collection, source, points_world):
    """월드 좌표 점 열로 열린 엣지 선 가이드(원본 로컬 좌표 POLY 커브)를 만든다. 점이 2개 미만이면 None."""
    if len(points_world) < 2:
        return None
    to_local = source.matrix_world.inverted()
    curve = bpy.data.curves.new(EDGE_PREFIX, 'CURVE')
    curve.dimensions = '3D'
    guide = bpy.data.objects.new(EDGE_PREFIX, curve)
    collection.objects.link(guide)
    guide.matrix_world = source.matrix_world.copy()
    guide.show_in_front = True
    guide.hide_render = True
    guide.lp3d_ring_guide.is_edge = True
    _write_points(curve, [tuple(to_local @ Vector(p)) for p in points_world], cyclic=False)
    return guide


def surface_segment(source, a_world, b_world, normal_a, normal_b) -> list:
    """두 월드 표면 점을 표면을 따라 잇는 월드 점 열(a 제외, b 포함). 단면을 못 찾으면 직선."""
    geometry = _geometry()
    to_local = source.matrix_world.inverted()
    a, b = tuple(to_local @ Vector(a_world)), tuple(to_local @ Vector(b_world))
    normal = (source.matrix_world.to_3x3().transposed() @ (Vector(normal_a) + Vector(normal_b)))
    normal = tuple(normal.normalized()) if normal.length > 1e-9 else (0.0, 0.0, 1.0)
    span = math.dist(a, b)
    # 프록시는 복셀이라 원본 표면에서 조금 뜬다 — 프록시 엣지 길이 정도는 허용한다
    area = _cached_area(source) or sum(polygon.area for polygon in source.data.polygons)
    tolerance = max(span * 0.3, 2.0 * math.sqrt(area / PROXY_FACES))
    path = geometry.surface_path(_section_mesh(source), a, b, normal, tolerance)
    if path is None:
        path = (a, b)
    # 단면은 복셀 프록시라 실제 표면에서 1cm 가까이 뜬다 — 리토폴로지는 결과를 원본 표면에 붙이므로 선도 원본 위에 둔다
    projected = []
    for point in path[1:]:
        found, location, _normal, _index = source.closest_point_on_mesh(Vector(point))
        projected.append(tuple(source.matrix_world @ (location if found else Vector(point))))
    return projected


def section_source(collection):
    """단면을 잴 메시 — 리토폴로지 원본(결과는 별도 컬렉션에 옆으로 비켜 있어 가이드 기준이 될 수 없다)."""
    from ..lowpoly import quadretopo
    return quadretopo.find_retopo_target(collection)


def _retopo_target(collection):
    from ..lowpoly import quadretopo
    return quadretopo.find_retopo_target(collection)


def _guide_collection(guide):
    return guide.users_collection[0] if guide.users_collection else None


def _section_mesh(source):
    """source 로컬 좌표의 복셀 프록시 단면 메시. 클릭마다 수백 번 자르므로 원본별로 한 번만 만든다."""
    from ..lowpoly import quadretopo, solid_fill
    key = (source.data.session_uid, len(source.data.vertices), len(source.data.polygons))
    cached = _proxy_cache.get(source.name)
    if cached is not None and cached[0] == key:
        return cached[1]
    # 리토폴로지가 QuadriFlow 에 넣는 메시와 같은 절차로 굽는다 — 셰이프 서버 메시는 복셀 리메시만으로는 구멍투성이라
    # (Remesh 모디파이어는 실측에서 형상 대부분이 사라졌다) 매니폴드 수리로 구멍까지 메워야 단면이 닫힌다.
    # 속 빈 이중 껍질을 그대로 리메시하면 껍질이 얇은 곳마다 몸속으로 뚫려 단면에 안쪽 껍질 둘레가 섞인다 —
    # 리토폴로지처럼 먼저 속을 채운다(실측 2026-09-29, 덩치큰 좀비 둘레 비율: 팔뚝 0.44·0.39 → 0.96·0.94,
    # 위팔 0.05 → 0.93, 목 0.57 → 0.99, 프록시 부피 0.053 → 0.161m³)
    proxy = quadretopo._duplicate(source, "LP3D_RingProxy", bpy.context.scene.collection)
    try:
        quadretopo._set_hidden(proxy, False)
        size = quadretopo._local_size(proxy)
        filled = solid_fill.fill_interior(proxy, size / quadretopo.SOLID_PITCH_DIV, size / quadretopo.SOLID_CLOSE_DIV)
        quadretopo._voxel_remesh(proxy, PROXY_FACES, wanted=PROXY_FACES)
        quadretopo.remove_fragments(proxy)
        quadretopo.make_manifold(proxy)
        mesh = proxy.data
        mesh.calc_loop_triangles()
        vertices = [tuple(v.co) for v in mesh.vertices]
        triangles = [tuple(t.vertices) for t in mesh.loop_triangles]
        # 리토폴로지가 띠 폭(엣지 길이)을 잡는 면적과 같은 값 — 패널의 최소 면수가 실행 때와 어긋나지 않게
        area = filled.get("outer_area") or sum(polygon.area for polygon in mesh.polygons)
    finally:
        quadretopo._discard(proxy)
    geometry = _geometry()
    section = geometry.SectionMesh(geometry.MeshData(vertices, triangles))
    _proxy_cache[source.name] = (key, section, area)
    return section


def _cached_area(source):
    """이미 구운 프록시의 바깥 표면적. 패널은 리드로우마다 그려지므로 프록시를 새로 굽지 않는다."""
    cached = _proxy_cache.get(source.name) if source is not None else None
    if cached is None or cached[0] != (source.data.session_uid, len(source.data.vertices), len(source.data.polygons)):
        return None
    return cached[2]


def quad_floor(collection, scene) -> tuple:
    """컬렉션의 링 가이드를 모두 담는 데 필요한 최소 목표 면수와 근거. 리토폴로지가 같은 기준으로 자동 상향한다."""
    from ..lowpoly import ring_cut
    guides = ring_guides(collection)
    source = section_source(collection) if guides else None
    area = _cached_area(source)
    if not area:
        return 0, ""
    edge = math.sqrt(area / max(1, int(scene.lp3d.retopo_faces)))
    to_source = source.matrix_world.inverted()
    loops = [(guide.name, [tuple(to_source @ (guide.matrix_world @ Vector(p.co[:3])))
                           for p in guide.data.splines[0].points])
             for guide in guides if guide.data.splines]
    return ring_cut.guide_quad_floor(ring_cut.ring_cuts(loops, edge), area)


def _local_scale(source) -> float:
    """원본 로컬 경계 상자의 가장 긴 변. 단면 계산이 로컬 좌표라 월드 크기를 쓰면 스케일만큼 어긋난다."""
    corners = [tuple(c) for c in source.bound_box]
    extent = max(max(c[i] for c in corners) - min(c[i] for c in corners) for i in range(3))
    return extent if extent > 0.0 else 1.0


def half_width_for(source, scene) -> float:
    """리토폴로지와 같은 기준: 출력 엣지 길이의 절반 (표면적 / 목표 면수)."""
    area = sum(polygon.area for polygon in source.data.polygons)
    target = max(1, int(scene.lp3d.retopo_faces))
    return 0.5 * math.sqrt(area / target)


def create_ring_guide(collection, source, hit_world, normal_world):
    """월드 좌표 클릭 지점·법선으로 링 가이드 커브를 만든다. 실패하면 (None, 사유)."""
    geometry = _geometry()
    section = _section_mesh(source)
    to_local = source.matrix_world.inverted()
    hit = tuple(to_local @ Vector(hit_world))
    normal = tuple((to_local.to_3x3().transposed().inverted() @ Vector(normal_world)).normalized())
    estimate = geometry.estimate_ring(section, hit, normal, _local_scale(source))
    if estimate is None:
        return None, "클릭 지점 주변에서 닫힌 단면을 찾지 못했습니다"
    return _new_guide(collection, source, section, estimate.center, estimate.axis, hit, estimate.radius), ""


def create_knife_guide(collection, source, ray_a, ray_b, hits_world):
    """선 양 끝의 월드 뷰 광선 (시점, 방향) 둘과 선을 따라 맞은 월드 표면 점들로 링 가이드를 만든다.

    축을 추정하지 않고 두 광선이 이루는 평면을 절단면으로 쓰므로 화면에 그은 기울기는 그대로다. 다만 광선은
    보는 방향에 가장 가까운 월드 축으로 나란히 세운다 — 원근 시점 탓에 화면에서 보이지 않게 기우는 것을 막는다.
    아핀 변환은 평면을 평면으로 보내므로 광선을 원본 로컬로 옮겨 로컬에서 자른다. 실패하면 (None, 사유)."""
    geometry = _geometry()
    to_local = source.matrix_world.inverted()
    rotate = to_local.to_3x3()
    local_rays = [(tuple(to_local @ Vector(origin)), tuple((rotate @ Vector(direction)).normalized()))
                  for origin, direction in (ray_a, ray_b)]
    if not hits_world:
        return None, "선이 메시를 지나지 않습니다"
    section = _section_mesh(source)
    hits = [tuple(to_local @ Vector(hit)) for hit in hits_world]
    # 원근·비스듬한 뷰의 평면은 시점 쪽으로 기운다 — 축 정렬 직교 뷰에서 그은 것처럼 깊이 방향을 월드 축에 맞춘다
    axes = [tuple((rotate @ Vector(axis)).normalized()) for axis in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))]
    aligned = geometry.knife_rays_along_axis(*local_rays[0], *local_rays[1], hits[len(hits) // 2], axes)
    plane = geometry.knife_plane(*aligned[0], *aligned[1])
    if plane is not None:
        local_rays = aligned
    else:
        plane = geometry.knife_plane(*local_rays[0], *local_rays[1])   # 선이 깊이 축과 나란하면 뷰 평면 그대로
    if plane is None:
        return None, "선이 너무 짧습니다"
    bounds = geometry.knife_bounds(*local_rays[0], *local_rays[1], plane[1])
    found = geometry.knife_ring(section, plane[0], plane[1], hits, bounds)
    if found is None:
        return None, "선이 지나는 곳에 닫힌 단면이 없습니다. 팔·다리를 가로질러 그어 주세요"
    loop, hit = found
    normal = plane[1]
    center = tuple(sum(p[i] for p in loop) / len(loop) for i in range(3))
    radius = max(math.dist(p, center) for p in loop)
    guide = _new_guide(collection, source, section, center, normal, hit, radius, bounds)
    # 틈이 추정 출력 엣지 둘 이상은 돼야 리토폴로지 입력 준비(속 채우기 닫힘·복셀)가 틈을 메우지 않는다
    area = _cached_area(source) or sum(polygon.area for polygon in source.data.polygons)
    clearance = KNIFE_CLEARANCE_EDGES * math.sqrt(area / max(1, int(bpy.context.scene.lp3d.retopo_faces)))
    shift = geometry.unfused_offset(section, center, normal, hit, radius, bounds, clearance)
    if shift is None:
        return guide, ("이 자리의 단면이 이웃 부위와 붙어 있어 리토폴로지에서 링이 열릴 수 있습니다 "
                       "— 두 부위가 떨어진 곳에 다시 그어 주세요")
    if shift:
        # 드래그 범위로 잘라 만든 링은 리토폴로지 입력 표면에 없다 — 두 부위가 떨어지는 가장 가까운 높이로 옮긴다
        guide.lp3d_ring_guide.offset = shift   # update 콜백이 타이머로 다시 자르지만 바로 결과를 보이도록 직접 자른다
        refresh_ring_guide(guide, source=source, section=section)
        return guide, f"단면이 이웃 부위와 붙어 있어 축을 따라 {abs(shift):.3f} 옮겼습니다"
    return guide, ""


def _new_guide(collection, source, section, center, axis, hit, radius, bounds=()):
    global _refreshing
    curve = bpy.data.curves.new(GUIDE_PREFIX, 'CURVE')
    curve.dimensions = '3D'
    guide = bpy.data.objects.new(GUIDE_PREFIX, curve)
    collection.objects.link(guide)
    guide.matrix_world = source.matrix_world.copy()
    guide.show_in_front = True
    guide.hide_render = True
    ring = guide.lp3d_ring_guide
    _refreshing = True   # offset 대입도 update 콜백을 부르므로 초기화 동안은 막는다
    try:
        ring.is_ring = True
        ring.center = center
        ring.axis = axis
        ring.hit = hit
        ring.radius = radius
        ring.offset = 0.0
        ring.clipped = bool(bounds)
        if bounds:
            (ring.clip_origin_a, ring.clip_side_a), (ring.clip_origin_b, ring.clip_side_b) = bounds
    finally:
        _refreshing = False
    refresh_ring_guide(guide, source=source, section=section)
    return guide


def refresh_ring_guide(guide, *, source=None, section=None) -> bool:
    """오프셋 위치의 단면을 다시 잘라 커브 점과 둘레 비율을 갱신한다."""
    geometry = _geometry()
    ring = guide.lp3d_ring_guide
    source = source or section_source(_guide_collection(guide))
    if source is None:
        ring.status = "원본 메시를 찾을 수 없습니다"
        return False
    section = section or _section_mesh(source)
    axis = tuple(ring.axis)
    center = tuple(ring.center[i] + axis[i] * ring.offset for i in range(3))
    hit = tuple(ring.hit[i] + axis[i] * ring.offset for i in range(3))
    loop = geometry.slice_ring(section, center, axis, hit, ring.radius, _bounds(ring))
    if loop is None:
        ring.status = "이 위치에는 닫힌 단면이 없습니다"
        ring.ratio = 0.0
        return False
    # 가이드는 원본과 같은 행렬을 쓰지만 원본을 옮겼을 수 있으므로 매번 맞춘다
    guide.matrix_world = source.matrix_world.copy()
    _write_points(guide.data, geometry.resample_loop(loop))
    ring.ratio = geometry.rim_ratio(section, center, axis, hit, ring.radius,
                                     half_width_for(source, bpy.context.scene), _bounds(ring))
    ring.status = "사용 가능" if ring.ratio >= geometry.RIM_OK_RATIO else "단면 급변: 위치를 옮겨 주세요"
    return True


def _write_points(curve, points, cyclic: bool = True) -> None:
    curve.splines.clear()
    spline = curve.splines.new('POLY')
    spline.points.add(len(points) - 1)
    for point, target in zip(points, spline.points):
        target.co = (point[0], point[1], point[2], 1.0)
    spline.use_cyclic_u = cyclic


def remove_guide(guide) -> None:
    """가이드 오브젝트와 다른 사용자가 없는 커브 데이터를 함께 지운다."""
    data = guide.data
    bpy.data.objects.remove(guide, do_unlink=True)
    if data is not None and data.users == 0:
        bpy.data.curves.remove(data)


def _job_collection(context):
    """선택한 잡의 결과 컬렉션과 클릭할 대상 메시. 조건이 안 맞으면 (None, None)."""
    from . import operators
    target = operators._retopo_source(context)
    if target is None:
        return None, None
    job = context.scene.lp3d.active_job()
    return bpy.data.collections.get(job.collection_name), target


def _viewport_under_mouse(context, event):
    """마우스 아래의 3D 뷰포트 WINDOW 영역과 영역 좌표. 사이드바·헤더 위면 None.

    패널 버튼에서 시작한 모달은 context.region 이 사이드바로 남아 있으므로 창 좌표로 직접 찾는다."""
    screen = context.window.screen if context.window else None
    if screen is None:
        return None
    x, y = event.mouse_x, event.mouse_y
    for area in screen.areas:
        if area.type != 'VIEW_3D':
            continue
        if not (area.x <= x < area.x + area.width and area.y <= y < area.y + area.height):
            continue
        # 영역 겹침이 켜져 있으면 사이드바·툴바가 WINDOW 위에 그려진다 — 그 위 클릭은 뷰포트가 아니다
        for region in area.regions:
            if region.type in _OVERLAY_REGIONS and region.width > 1 and region.height > 1 and \
                    region.x <= x < region.x + region.width and region.y <= y < region.y + region.height:
                return None
        for region in area.regions:
            if region.type == 'WINDOW' and region.x <= x < region.x + region.width \
                    and region.y <= y < region.y + region.height:
                return region, region.data, (x - region.x, y - region.y)
    return None


def _raycast(context, event, target):
    """마우스 위치에서 대상 메시로 레이를 쏘아 (월드 히트 점, 월드 면 법선) 을 돌려준다."""
    found = _viewport_under_mouse(context, event)
    if found is None:
        return None
    region, rv3d, coord = found
    if rv3d is None:
        return None
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    inverse = target.matrix_world.inverted()
    hit, location, normal, _index = target.ray_cast(inverse @ origin, (inverse.to_3x3() @ direction).normalized())
    if not hit:
        return None
    normal_world = (target.matrix_world.to_3x3().inverted().transposed() @ normal).normalized()
    return tuple(target.matrix_world @ location), tuple(normal_world)


def detach_guide(guide) -> None:
    """모달 도중 취소용. 데이터 블록을 바로 해제하면 이어지는 뎁스그래프 재구성이 해제된 커브를 따라가 Blender 가
    종료된 적이 있어(3DRemesher 실측 크래시), 씬에서만 떼고 사용자 0 인 블록은 저장·재열기 때 정리되게 둔다."""
    for collection in tuple(guide.users_collection):
        collection.objects.unlink(guide)


def _push_undo(message: str) -> None:
    """모달 세션 전체가 언도 한 단계로 묶이면 끝낸 뒤 Ctrl+Z 한 번에 링이 모두 사라지므로 링마다 단계를 남긴다."""
    try:
        bpy.ops.ed.undo_push(message=message)
    except RuntimeError:
        pass


def _is_undo_key(event) -> bool:
    return (event.type == 'Z' and (event.ctrl or event.oskey) and not event.shift) or event.type == 'BACK_SPACE'


def _undo_last(operator, label: str = "링 가이드") -> None:
    """이 세션에서 만든 마지막 가이드만 지운다. 모달 중 전역 언도가 끼어들면 원본까지 되돌아갈 수 있어 키를 여기서 삼킨다."""
    while operator._created:
        guide = bpy.data.objects.get(operator._created.pop())
        if guide is not None and guide.users_collection:
            detach_guide(guide)
            _push_undo(f"{label} 취소")
            operator.report({'INFO'}, f"마지막 {label}을 취소했습니다")
            break
    try:
        operator._area.tag_redraw()
    except (AttributeError, ReferenceError):
        pass


def _modal_targets(operator):
    """모달 중 리토폴로지를 돌리면 보존본이 새로 생기고 대상 이름이 바뀌므로 입력마다 (컬렉션, 대상, 단면 원본)을 다시 찾는다."""
    collection = bpy.data.collections.get(operator._collection_name)
    target = bpy.data.objects.get(operator._target_name)
    source = section_source(collection) if collection is not None else None
    target = _retopo_target(collection) or target if collection is not None else target
    return collection, target, source


class LP3D_OT_ring_guide_add(bpy.types.Operator):
    bl_idname = "lp3d.ring_guide_add"
    bl_label = "클릭으로 링 가이드 추가"
    bl_description = ("메시 표면을 클릭한 자리에 축에 수직인 단면 링 가이드를 만든다. 리토폴로지가 그 위치에 "
                      "나선 대신 링 루프를 깐다. Ctrl+Z 로 마지막 링을 취소하고 우클릭이나 ESC 로 끝낸다")
    bl_options = {'REGISTER'}   # 링마다 언도 단계를 직접 남긴다

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and _job_collection(context)[1] is not None

    def invoke(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "3D 뷰포트에서 실행해야 합니다")
            return {'CANCELLED'}
        collection, target = _job_collection(context)
        source = section_source(collection)
        self._collection_name = collection.name
        self._target_name = target.name
        self._area = context.area
        self._created = []
        context.window.cursor_modal_set('WAIT')
        try:
            _section_mesh(source)   # 첫 클릭이 멈추지 않도록 프록시를 미리 굽는다
        finally:
            context.window.cursor_modal_restore()
        context.window_manager.modal_handler_add(self)
        self._update_header()
        return {'RUNNING_MODAL'}

    def _update_header(self):
        try:
            self._area.header_text_set(
                f"링 가이드 {len(self._created)}개 추가 — 좌클릭: 추가, Ctrl+Z/Backspace: 마지막 취소, "
                "우클릭/ESC: 종료")
        except (AttributeError, ReferenceError):
            pass

    def _finish(self):
        try:
            self._area.header_text_set(None)
        except (AttributeError, ReferenceError):
            pass

    def modal(self, context, event):
        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self._finish()
            return {'FINISHED'}
        undo_key = _is_undo_key(event)
        if undo_key and event.value == 'PRESS':
            _undo_last(self)
            self._update_header()
            return {'RUNNING_MODAL'}
        if undo_key:
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            collection, target, source = _modal_targets(self)
            if collection is None or target is None or source is None:
                self._finish()
                return {'CANCELLED'}
            hit = _raycast(context, event, target)
            if hit is None:
                return {'PASS_THROUGH'}   # 사이드바 버튼·빈 공간 클릭은 그대로 넘긴다
            guide, reason = create_ring_guide(collection, source, *hit)
            if guide is None:
                self.report({'WARNING'}, reason)
            else:
                self._created.append(guide.name)
                _push_undo("링 가이드 추가")
                ring = guide.lp3d_ring_guide
                self.report({'INFO'}, f"{guide.name}: 반지름 {ring.radius:.3f}, 둘레 비율 {ring.ratio:.2f} "
                                      f"— {ring.status}")
            self._update_header()
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}


KNIFE_SAMPLES = 48          # 선을 따라 이만큼 레이를 쏘아 가로지른 부위를 고른다
KNIFE_MIN_PIXELS = 8.0      # 이보다 짧은 드래그는 클릭 실수로 보고 버린다
KNIFE_COLOR = (1.0, 0.8, 0.1, 1.0)


def _draw_knife_line(operator):
    try:
        _draw_knife_stroke(operator)
    except ReferenceError:
        pass   # 파일을 새로 열어 오퍼레이터가 해제된 뒤 남은 호출


def _draw_knife_stroke(operator):
    region = bpy.context.region
    if operator._start is None or operator._end is None or region is None \
            or region.as_pointer() != operator._region_pointer:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader

    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'LINES', {"pos": [operator._start, operator._end]})
    gpu.state.blend_set('ALPHA')
    shader.uniform_float("viewportSize", (region.width, region.height))
    shader.uniform_float("lineWidth", 2.0)
    shader.uniform_float("color", KNIFE_COLOR)
    batch.draw(shader)
    gpu.state.blend_set('NONE')


def _world_ray(region, rv3d, coord):
    return (tuple(view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)),
            tuple(view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)))


class LP3D_OT_ring_guide_knife(bpy.types.Operator):
    bl_idname = "lp3d.ring_guide_knife"
    bl_label = "선으로 링 가이드 추가"
    bl_description = ("Knife 처럼 뷰에서 팔·다리를 가로지르는 직선을 그으면, 그 선과 보는 방향이 이루는 평면으로 "
                      "단면 링 가이드를 만든다. 축 자동 추정이 어긋나는 자리에서 기울기를 직접 정한다. "
                      "Ctrl+Z 로 마지막 링을 취소하고 우클릭이나 ESC 로 끝낸다")
    bl_options = {'REGISTER'}   # 링마다 언도 단계를 직접 남긴다

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and _job_collection(context)[1] is not None

    def invoke(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "3D 뷰포트에서 실행해야 합니다")
            return {'CANCELLED'}
        collection, target = _job_collection(context)
        source = section_source(collection)
        self._collection_name = collection.name
        self._target_name = target.name
        self._area = context.area
        self._created = []
        self._region = None
        self._region_pointer = 0
        self._start = None
        self._end = None
        context.window.cursor_modal_set('WAIT')
        try:
            _section_mesh(source)   # 첫 선이 멈추지 않도록 프록시를 미리 굽는다
        finally:
            context.window.cursor_modal_restore()
        self._handle = bpy.types.SpaceView3D.draw_handler_add(_draw_knife_line, (self,), 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        self._update_header()
        return {'RUNNING_MODAL'}

    def _update_header(self):
        try:
            self._area.header_text_set(
                f"링 가이드 {len(self._created)}개 추가 — 드래그: 가로지르는 선, Ctrl: 15° 스냅, "
                "Ctrl+Z/Backspace: 마지막 취소, 우클릭/ESC: 종료")
        except (AttributeError, ReferenceError):
            pass

    def _redraw(self):
        try:
            self._area.tag_redraw()
        except (AttributeError, ReferenceError):
            pass

    def _finish(self):
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        try:
            self._area.header_text_set(None)
        except (AttributeError, ReferenceError):
            pass
        self._redraw()

    def cancel(self, _context):
        # 파일 열기·영역 닫기처럼 모달 밖에서 끝날 때도 그리기 핸들러를 떼어야 한다
        self._finish()

    def _mouse(self, event, snap: bool):
        """선을 시작한 영역 기준 좌표. 드래그가 영역 밖으로 나가도 같은 영역의 광선으로 계산하려고 창 좌표에서 직접 뺀다."""
        point = (event.mouse_x - self._region.x, event.mouse_y - self._region.y)
        if not snap or self._start is None:
            return point
        dx, dy = point[0] - self._start[0], point[1] - self._start[1]
        length = math.hypot(dx, dy)
        step = math.radians(15.0)
        angle = round(math.atan2(dy, dx) / step) * step
        return (self._start[0] + length * math.cos(angle), self._start[1] + length * math.sin(angle))

    def modal(self, context, event):
        if event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            if self._start is not None:   # 긋던 선만 버리고 도구는 유지한다
                self._start = self._end = None
                self._redraw()
                return {'RUNNING_MODAL'}
            self._finish()
            return {'FINISHED'}
        undo_key = _is_undo_key(event)
        if undo_key and event.value == 'PRESS':
            _undo_last(self)
            self._update_header()
            return {'RUNNING_MODAL'}
        if undo_key:
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            found = _viewport_under_mouse(context, event)
            if found is None or found[1] is None:
                return {'PASS_THROUGH'}   # 사이드바 버튼 클릭은 그대로 넘긴다
            self._region, self._rv3d, coord = found
            self._region_pointer = self._region.as_pointer()
            self._start = self._end = coord
            self._redraw()
            return {'RUNNING_MODAL'}
        if self._start is not None and event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'LEFT_CTRL', 'RIGHT_CTRL'}:
            self._end = self._mouse(event, event.ctrl)
            self._redraw()
            return {'RUNNING_MODAL'}
        if self._start is not None and event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._end = self._mouse(event, event.ctrl)
            start, end = self._start, self._end
            self._start = self._end = None
            self._redraw()
            if math.hypot(end[0] - start[0], end[1] - start[1]) < KNIFE_MIN_PIXELS:
                return {'RUNNING_MODAL'}
            collection, target, source = _modal_targets(self)
            if collection is None or target is None or source is None:
                self._finish()
                return {'CANCELLED'}
            self._cut(collection, target, source, start, end)
            self._update_header()
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}

    def _cut(self, collection, target, source, start, end):
        try:
            region, rv3d = self._region, self._rv3d
            ray_a = _world_ray(region, rv3d, start)
            ray_b = _world_ray(region, rv3d, end)
            samples = [_world_ray(region, rv3d, (start[0] + (end[0] - start[0]) * k / KNIFE_SAMPLES,
                                                 start[1] + (end[1] - start[1]) * k / KNIFE_SAMPLES))
                       for k in range(KNIFE_SAMPLES + 1)]
        except ReferenceError:
            return   # 드래그 도중 영역이 닫혔다
        inverse = target.matrix_world.inverted()
        hits = []
        for origin, direction in samples:
            hit, location, _normal, _index = target.ray_cast(
                inverse @ Vector(origin), (inverse.to_3x3() @ Vector(direction)).normalized())
            if hit:
                hits.append(tuple(target.matrix_world @ location))
        guide, reason = create_knife_guide(collection, source, ray_a, ray_b, hits)
        if guide is None:
            self.report({'WARNING'}, reason)
            return
        self._created.append(guide.name)
        _push_undo("링 가이드 추가")
        ring = guide.lp3d_ring_guide
        self.report({'WARNING'} if reason else {'INFO'},
                    f"{guide.name}: 반지름 {ring.radius:.3f}, 둘레 비율 {ring.ratio:.2f} — {ring.status}"
                    + (f" · {reason}" if reason else ""))


class LP3D_OT_ring_guide_check(bpy.types.Operator):
    bl_idname = "lp3d.ring_guide_check"
    bl_label = "링 가이드 다시 검사"
    bl_description = "모든 링 가이드의 단면과 둘레 비율을 현재 목표 면수로 다시 계산한다"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(ring_guides(_job_collection(context)[0]))

    def execute(self, context):
        guides = ring_guides(_job_collection(context)[0])
        ratio_ok = _geometry().RIM_OK_RATIO
        bad = [guide.name for guide in guides
               if not refresh_ring_guide(guide) or guide.lp3d_ring_guide.ratio < ratio_ok]
        if bad:
            self.report({'WARNING'}, f"위치 조정이 필요한 가이드: {', '.join(bad)}")
        else:
            self.report({'INFO'}, f"링 가이드 {len(guides)}개 모두 사용 가능합니다")
        return {'FINISHED'}


class LP3D_OT_ring_guide_remove(bpy.types.Operator):
    bl_idname = "lp3d.ring_guide_remove"
    bl_label = "링 가이드 삭제"
    bl_description = "이 링 가이드를 지운다"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(name="가이드 이름", options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        guide = bpy.data.objects.get(self.name)
        if not (is_guide(guide) or is_edge_guide(guide)):
            self.report({'WARNING'}, f"가이드를 찾을 수 없습니다: {self.name}")
            return {'CANCELLED'}
        remove_guide(guide)
        return {'FINISHED'}


class LP3D_OT_ring_guide_clear(bpy.types.Operator):
    bl_idname = "lp3d.ring_guide_clear"
    bl_label = "링 가이드 모두 삭제"
    bl_description = "선택한 항목의 링 가이드를 모두 지운다"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(ring_guides(_job_collection(context)[0]))

    def execute(self, context):
        guides = ring_guides(_job_collection(context)[0])
        for guide in guides:
            remove_guide(guide)
        self.report({'INFO'}, f"링 가이드 {len(guides)}개를 지웠습니다")
        return {'FINISHED'}


def draw_ring_guides(layout, context, collection) -> None:
    """리토폴로지 상자 안의 링 가이드 섹션."""
    ratio_ok = _geometry().RIM_OK_RATIO
    box = layout.box()
    box.label(text="링 가이드 (팔·다리·목 나선 방지)", icon='CURVE_NCIRCLE')
    row = box.row(align=True)
    row.operator("lp3d.ring_guide_add", text="클릭", icon='ADD')
    row.operator("lp3d.ring_guide_knife", text="선 긋기", icon='IPO_LINEAR')
    row.operator("lp3d.ring_guide_check", text="", icon='FILE_REFRESH')
    row.operator("lp3d.ring_guide_clear", text="", icon='TRASH')
    guides = ring_guides(collection)
    if not guides:
        box.label(text="클릭: 축 자동 · 선 긋기: 그은 선이 절단면")
        box.label(text="Ctrl+Z 마지막 취소 · 우클릭 종료")
        return
    floor, reason = quad_floor(collection, context.scene)
    if floor:
        low = context.scene.lp3d.retopo_faces < floor
        box.label(text=f"가이드 기준 최소 면수: {floor:,}", icon='ERROR' if low else 'CHECKMARK')
        if low:
            from ..lowpoly.quadretopo import MAX_TARGET_FACES
            box.label(text=f"실행하면 {min(floor, MAX_TARGET_FACES):,} 로 올려서 깝니다")
        if reason:
            box.label(text=f"근거: {reason}")
    active = context.active_object
    if is_guide(active) and active in guides:
        ring = active.lp3d_ring_guide
        box.prop(ring, "offset")
        row = box.row(align=True)
        row.label(text=f"{ring.status} (둘레 비율 {ring.ratio:.2f})",
                  icon='CHECKMARK' if ring.ratio >= ratio_ok else 'ERROR')
        row.operator("lp3d.ring_guide_remove", text="", icon='X').name = active.name
    others = [guide for guide in guides if guide is not active]
    for guide in others[:LIST_LIMIT]:
        ring = guide.lp3d_ring_guide
        row = box.row(align=True)
        row.label(text=f"{guide.name}  {ring.ratio:.2f}",
                  icon='CHECKMARK' if ring.ratio >= ratio_ok else 'ERROR')
        row.operator("lp3d.ring_guide_remove", text="", icon='X').name = guide.name
    if len(others) > LIST_LIMIT:
        box.label(text=f"외 {len(others) - LIST_LIMIT}개")


EDGE_COLOR = (0.2, 0.9, 1.0, 1.0)
EDGE_POINT_PIXELS = 4.0


def _draw_edge_preview(operator):
    try:
        _draw_edge_stroke(operator)
    except ReferenceError:
        pass


def _draw_edge_stroke(operator):
    region = bpy.context.region
    rv3d = bpy.context.region_data
    if not operator._path or region is None or rv3d is None or region.as_pointer() != operator._region_pointer:
        return
    import gpu
    from gpu_extras.batch import batch_for_shader

    projected = [view3d_utils.location_3d_to_region_2d(region, rv3d, Vector(p)) for p in operator._path]
    projected = [tuple(p) for p in projected if p is not None]
    lines = [(projected[i], projected[i + 1]) for i in range(len(projected) - 1)]
    if operator._mouse is not None and projected:
        lines.append((projected[-1], operator._mouse))   # 다음 점까지 고무줄
    clicked = [view3d_utils.location_3d_to_region_2d(region, rv3d, Vector(p)) for p in operator._clicks]
    size = EDGE_POINT_PIXELS
    for point in clicked:
        if point is not None:   # 찍은 점은 작은 십자로
            lines.append(((point[0] - size, point[1]), (point[0] + size, point[1])))
            lines.append(((point[0], point[1] - size), (point[0], point[1] + size)))
    if not lines:
        return
    shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'LINES', {"pos": [p for pair in lines for p in pair]})
    gpu.state.blend_set('ALPHA')
    shader.uniform_float("viewportSize", (region.width, region.height))
    shader.uniform_float("lineWidth", 2.0)
    shader.uniform_float("color", EDGE_COLOR)
    batch.draw(shader)
    gpu.state.blend_set('NONE')


class LP3D_OT_edge_guide_draw(bpy.types.Operator):
    bl_idname = "lp3d.edge_guide_draw"
    bl_label = "엣지 선 그리기"
    bl_description = ("표면을 클릭해 점을 찍으면 점 사이를 표면을 따라 잇는 선을 그린다. 리토폴로지가 이 선을 따라 "
                      "와이어 엣지를 깐다 — 메카닉 하드 엣지나 원하는 와이어 흐름에 쓴다. Enter·Space·우클릭으로 선을 "
                      "확정하고, Backspace·Ctrl+Z 로 마지막 점(점이 없으면 마지막 선)을 취소, ESC 로 긋던 선을 버리거나 끝낸다")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and _job_collection(context)[1] is not None

    def invoke(self, context, event):
        if context.area is None or context.area.type != 'VIEW_3D':
            self.report({'ERROR'}, "3D 뷰포트에서 실행해야 합니다")
            return {'CANCELLED'}
        collection, target = _job_collection(context)
        source = section_source(collection)
        self._collection_name = collection.name
        self._target_name = target.name
        self._area = context.area
        self._created = []
        self._region_pointer = 0
        self._clicks = []      # 찍은 월드 점
        self._normals = []
        self._path = []        # 표면을 따라 이은 월드 점 열
        self._marks = []       # 점마다 그때까지의 경로 길이 — 마지막 점 취소용
        self._mouse = None
        context.window.cursor_modal_set('WAIT')
        try:
            _section_mesh(source)   # 첫 구간이 멈추지 않도록 프록시를 미리 굽는다
        finally:
            context.window.cursor_modal_restore()
        self._handle = bpy.types.SpaceView3D.draw_handler_add(_draw_edge_preview, (self,), 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        self._update_header()
        return {'RUNNING_MODAL'}

    def _update_header(self):
        try:
            self._area.header_text_set(
                f"엣지 선 {len(self._created)}개 · 점 {len(self._clicks)}개 — 클릭: 점 추가(표면을 따라 이음), "
                "Enter/Space/우클릭: 선 확정, Backspace/Ctrl+Z: 마지막 점 취소, ESC: 버리기/종료")
        except (AttributeError, ReferenceError):
            pass

    def _redraw(self):
        try:
            self._area.tag_redraw()
        except (AttributeError, ReferenceError):
            pass

    def _finish(self):
        if self._handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            self._handle = None
        try:
            self._area.header_text_set(None)
        except (AttributeError, ReferenceError):
            pass
        self._redraw()

    def cancel(self, _context):
        self._finish()

    def _reset_line(self):
        self._clicks, self._normals, self._path, self._marks = [], [], [], []

    def _commit(self) -> None:
        collection, _target, source = _modal_targets(self)
        if len(self._clicks) >= 2 and collection is not None and source is not None:
            guide = create_edge_guide(collection, source, self._path)
            if guide is not None:
                self._created.append(guide.name)
                _push_undo("엣지 선 추가")
                self.report({'INFO'}, f"{guide.name}: 점 {len(self._clicks)}개")
        self._reset_line()

    def modal(self, context, event):
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            found = _viewport_under_mouse(context, event)
            if found is not None and found[0].as_pointer() == self._region_pointer:
                self._mouse = found[2]
                self._redraw()
            return {'PASS_THROUGH'}
        if event.value != 'PRESS':
            return {'RUNNING_MODAL'} if _is_undo_key(event) else {'PASS_THROUGH'}
        if event.type in {'RET', 'NUMPAD_ENTER', 'SPACE'} or event.type == 'RIGHTMOUSE':
            if self._clicks:
                self._commit()
            elif event.type == 'RIGHTMOUSE':
                self._finish()
                return {'FINISHED'}
            self._update_header()
            self._redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'ESC':
            if self._clicks:
                self._reset_line()
                self._update_header()
                self._redraw()
                return {'RUNNING_MODAL'}
            self._finish()
            return {'FINISHED'}
        if _is_undo_key(event):
            if self._clicks:
                self._clicks.pop()
                self._normals.pop()
                del self._path[self._marks.pop():]
            else:
                _undo_last(self, "엣지 선")
            self._update_header()
            self._redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE':
            collection, target, source = _modal_targets(self)
            if collection is None or target is None or source is None:
                self._finish()
                return {'CANCELLED'}
            hit = _raycast(context, event, target)
            if hit is None:
                return {'PASS_THROUGH'}   # 사이드바 버튼·빈 공간 클릭은 그대로 넘긴다
            found = _viewport_under_mouse(context, event)
            self._region_pointer = found[0].as_pointer()
            if self._clicks:
                try:
                    segment = surface_segment(source, self._clicks[-1], hit[0], self._normals[-1], hit[1])
                except (RuntimeError, ValueError, ReferenceError):
                    segment = [hit[0]]   # 단면 계산이 실패해도 모달은 살린다 — 직선으로 잇는다
            else:
                segment = [hit[0]]
            self._marks.append(len(self._path))
            self._path.extend(segment)
            self._clicks.append(hit[0])
            self._normals.append(hit[1])
            self._update_header()
            self._redraw()
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}


class LP3D_OT_edge_guide_clear(bpy.types.Operator):
    bl_idname = "lp3d.edge_guide_clear"
    bl_label = "엣지 선 모두 삭제"
    bl_description = "선택한 항목의 엣지 선 가이드를 모두 지운다"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(edge_guides(_job_collection(context)[0]))

    def execute(self, context):
        guides = edge_guides(_job_collection(context)[0])
        for guide in guides:
            remove_guide(guide)
        self.report({'INFO'}, f"엣지 선 {len(guides)}개를 지웠습니다")
        return {'FINISHED'}


def draw_edge_guides(layout, context, collection) -> None:
    """리토폴로지 상자 안의 엣지 선 섹션."""
    box = layout.box()
    box.label(text="엣지 선 (하드 엣지·와이어 흐름)", icon='IPO_LINEAR')
    row = box.row(align=True)
    row.operator("lp3d.edge_guide_draw", text="엣지 선 그리기", icon='GREASEPENCIL')
    row.operator("lp3d.edge_guide_clear", text="", icon='TRASH')
    guides = edge_guides(collection)
    if not guides:
        box.label(text="클릭으로 점 · 점 사이는 표면을 따라 이음")
        box.label(text="Enter/우클릭 확정 · Backspace 마지막 점 취소")
        return
    for guide in guides[:LIST_LIMIT]:
        row = box.row(align=True)
        count = sum(len(spline.points) for spline in guide.data.splines)
        row.label(text=f"{guide.name}  점 {count}")
        row.operator("lp3d.ring_guide_remove", text="", icon='X').name = guide.name
    if len(guides) > LIST_LIMIT:
        box.label(text=f"외 {len(guides) - LIST_LIMIT}개")


_CLASSES = (
    LP3DRingGuideProps,
    LP3D_OT_ring_guide_add, LP3D_OT_ring_guide_knife, LP3D_OT_ring_guide_check,
    LP3D_OT_ring_guide_remove, LP3D_OT_ring_guide_clear,
    LP3D_OT_edge_guide_draw, LP3D_OT_edge_guide_clear,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.lp3d_ring_guide = bpy.props.PointerProperty(type=LP3DRingGuideProps)


def unregister():
    _proxy_cache.clear()
    if hasattr(bpy.types.Object, "lp3d_ring_guide"):
        del bpy.types.Object.lp3d_ring_guide
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
