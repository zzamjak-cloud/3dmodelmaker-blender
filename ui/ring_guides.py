# 링 가이드 — 표면을 클릭해 팔·다리·목 단면 링을 만들고, 리토폴로지가 그 위치를 절단 링으로 쓴다
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
PROXY_FACES = 32000          # 단면용 복셀 프록시 면수 — 리토폴로지 입력 사다리의 복셀 밀도와 같다
LIST_LIMIT = 8               # 패널은 리드로우마다 그려지므로 목록을 이 개수까지만 보여 준다

_OVERLAY_REGIONS = {'UI', 'TOOLS', 'HEADER', 'TOOL_HEADER', 'ASSET_SHELF', 'ASSET_SHELF_HEADER'}

_refreshing = False
_pending_refresh = set()
_proxy_cache = {}            # 원본 이름 → (메시 키, SectionMesh)


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


def section_source(collection):
    """단면을 잴 메시 — 보존된 원본이 있으면 그것(리토폴로지 결과는 다시 깔 때 사라진다), 없으면 대상 메시."""
    from ..lowpoly import quadretopo
    return quadretopo.find_retopo_source(collection) or quadretopo.find_retopo_target(collection)


def _retopo_target(collection):
    from ..lowpoly import quadretopo
    return quadretopo.find_retopo_target(collection)


def _guide_collection(guide):
    return guide.users_collection[0] if guide.users_collection else None


def _section_mesh(source):
    """source 로컬 좌표의 복셀 프록시 단면 메시. 클릭마다 수백 번 자르므로 원본별로 한 번만 만든다."""
    from ..lowpoly import quadretopo
    key = (source.data.session_uid, len(source.data.vertices), len(source.data.polygons))
    cached = _proxy_cache.get(source.name)
    if cached is not None and cached[0] == key:
        return cached[1]
    # 리토폴로지가 QuadriFlow 에 넣는 메시와 같은 절차로 굽는다 — 셰이프 서버 메시는 복셀 리메시만으로는 구멍투성이라
    # (Remesh 모디파이어는 실측에서 형상 대부분이 사라졌다) 매니폴드 수리로 구멍까지 메워야 단면이 닫힌다
    proxy = quadretopo._duplicate(source, "LP3D_RingProxy", bpy.context.scene.collection)
    try:
        quadretopo._set_hidden(proxy, False)
        quadretopo._voxel_remesh(proxy, PROXY_FACES, wanted=PROXY_FACES)
        quadretopo.remove_fragments(proxy)
        quadretopo.make_manifold(proxy)
        mesh = proxy.data
        mesh.calc_loop_triangles()
        vertices = [tuple(v.co) for v in mesh.vertices]
        triangles = [tuple(t.vertices) for t in mesh.loop_triangles]
    finally:
        quadretopo._discard(proxy)
    geometry = _geometry()
    section = geometry.SectionMesh(geometry.MeshData(vertices, triangles))
    _proxy_cache[source.name] = (key, section)
    return section


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
    global _refreshing
    geometry = _geometry()
    section = _section_mesh(source)
    to_local = source.matrix_world.inverted()
    hit = tuple(to_local @ Vector(hit_world))
    normal = tuple((to_local.to_3x3().transposed().inverted() @ Vector(normal_world)).normalized())
    estimate = geometry.estimate_ring(section, hit, normal, _local_scale(source))
    if estimate is None:
        return None, "클릭 지점 주변에서 닫힌 단면을 찾지 못했습니다"
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
        ring.center = estimate.center
        ring.axis = estimate.axis
        ring.hit = hit
        ring.radius = estimate.radius
        ring.offset = 0.0
    finally:
        _refreshing = False
    refresh_ring_guide(guide, source=source, section=section)
    return guide, ""


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
    loop = geometry.slice_ring(section, center, axis, hit, ring.radius)
    if loop is None:
        ring.status = "이 위치에는 닫힌 단면이 없습니다"
        ring.ratio = 0.0
        return False
    # 가이드는 원본과 같은 행렬을 쓰지만 원본을 옮겼을 수 있으므로 매번 맞춘다
    guide.matrix_world = source.matrix_world.copy()
    _write_points(guide.data, geometry.resample_loop(loop))
    ring.ratio = geometry.rim_ratio(section, center, axis, hit, ring.radius,
                                     half_width_for(source, bpy.context.scene))
    ring.status = "사용 가능" if ring.ratio >= geometry.RIM_OK_RATIO else "단면 급변: 위치를 옮겨 주세요"
    return True


def _write_points(curve, points) -> None:
    curve.splines.clear()
    spline = curve.splines.new('POLY')
    spline.points.add(len(points) - 1)
    for point, target in zip(points, spline.points):
        target.co = (point[0], point[1], point[2], 1.0)
    spline.use_cyclic_u = True


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


class LP3D_OT_ring_guide_add(bpy.types.Operator):
    bl_idname = "lp3d.ring_guide_add"
    bl_label = "클릭으로 링 가이드 추가"
    bl_description = ("메시 표면을 클릭한 자리에 축에 수직인 단면 링 가이드를 만든다. 리토폴로지가 그 위치에 "
                      "나선 대신 링 루프를 깐다. Ctrl+Z 로 마지막 링을 취소하고 우클릭이나 ESC 로 끝낸다")
    bl_options = {'REGISTER', 'UNDO'}

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
        undo_key = (event.type == 'Z' and (event.ctrl or event.oskey)) or event.type == 'BACK_SPACE'
        if undo_key and event.value == 'PRESS':
            # 모달 중 전역 언도가 끼어들면 원본까지 되돌아갈 수 있어 여기서 삼키고, 이 세션의 마지막 링만 지운다
            while self._created:
                guide = bpy.data.objects.get(self._created.pop())
                if guide is not None:
                    remove_guide(guide)
                    self.report({'INFO'}, "마지막 링 가이드를 취소했습니다")
                    break
            self._update_header()
            return {'RUNNING_MODAL'}
        if undo_key:
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            collection = bpy.data.collections.get(self._collection_name)
            target = bpy.data.objects.get(self._target_name)
            # 모달 중 리토폴로지를 돌리면 보존본이 새로 생기고 대상 이름이 바뀌므로 클릭마다 다시 찾는다
            source = section_source(collection) if collection is not None else None
            target = _retopo_target(collection) or target
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
                ring = guide.lp3d_ring_guide
                self.report({'INFO'}, f"{guide.name}: 반지름 {ring.radius:.3f}, 둘레 비율 {ring.ratio:.2f} "
                                      f"— {ring.status}")
            self._update_header()
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}


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
        if not is_guide(guide):
            self.report({'WARNING'}, f"링 가이드를 찾을 수 없습니다: {self.name}")
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
    row.operator("lp3d.ring_guide_add", icon='ADD')
    row.operator("lp3d.ring_guide_check", text="", icon='FILE_REFRESH')
    row.operator("lp3d.ring_guide_clear", text="", icon='TRASH')
    guides = ring_guides(collection)
    if not guides:
        box.label(text="좌클릭 추가 · Ctrl+Z 마지막 취소 · 우클릭 종료")
        return
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


_CLASSES = (
    LP3DRingGuideProps,
    LP3D_OT_ring_guide_add, LP3D_OT_ring_guide_check,
    LP3D_OT_ring_guide_remove, LP3D_OT_ring_guide_clear,
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
