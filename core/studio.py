# 결과 .blend 에 넣는 스튜디오 — 3점 면광원 + 곡면 호리존트 + 스튜디오 HDRI + 3/4 구도 카메라.
#
# autosave.write_blend 가 결과만 담은 임시 씬을 쓸 때 그 씬에만 세우고, 쓰고 나면 지운다(작업 중인 파일에는
# 아무것도 남지 않는다). 파일을 열면 load_post 에서 뷰포트를 머티리얼 미리보기 + 씬 조명·월드 + 카메라 뷰로
# 바꿔 F12 렌더와 같은 그림이 바로 보이게 한다. 크기·거리·광량은 모델 키에 비례한다.
import math
import os

import bpy
from mathutils import Vector

STUDIO_MARK = "lp3d_studio"          # 이 씬 속성이 있으면 열 때 뷰포트를 스튜디오 보기로 바꾼다
REFERENCE_HEIGHT = 1.8               # 광량을 맞춘 기준 키(m) — 크기가 다르면 키² 에 비례해 키운다
HDRI_NAME = "studio.exr"             # Blender 번들 studiolights/world (라이선스: CC0)


def model_bounds(objects):
    """메시 오브젝트들의 월드 경계 (lo, hi). 없으면 None."""
    pts = [o.matrix_world @ Vector(c) for o in objects if o.type == 'MESH' for c in o.bound_box]
    if not pts:
        return None
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def _look_at(obj, target):
    obj.rotation_euler = (target - obj.location).to_track_quat('-Z', 'Y').to_euler()


def _engine_id():
    items = [i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
    return 'BLENDER_EEVEE_NEXT' if 'BLENDER_EEVEE_NEXT' in items else 'BLENDER_EEVEE'


def _try_set(owner, prop, *candidates):
    """후보 값을 차례로 넣어 본다 — 색 관리 enum 은 동적이라 enum_items 로 목록을 읽을 수 없다(백그라운드에서 'NONE')."""
    for value in candidates:
        try:
            setattr(owner, prop, value)
            return value
        except TypeError:
            continue
    return None


def _cyclorama(name, size, floor_z, wall_y, radius):
    """바닥에서 둥글게 말려 올라가는 배경판 — 바닥·벽 경계 선이 보이지 않는다."""
    profile = [(-size, floor_z), (wall_y - radius, floor_z)]
    for i in range(1, 12):
        a = math.pi / 2 * i / 12
        profile.append((wall_y - radius + radius * math.sin(a), floor_z + radius - radius * math.cos(a)))
    profile += [(wall_y, floor_z + radius), (wall_y, floor_z + size)]
    verts, faces = [], []
    for x in (-size, size):
        verts += [(x, y, z) for y, z in profile]
    n = len(profile)
    for i in range(n - 1):
        faces.append((i, i + 1, n + i + 1, n + i))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    for poly in mesh.polygons:
        poly.use_smooth = True
    mesh.update()
    return mesh


def _material(name, color, roughness):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    mat.diffuse_color = (*color, 1.0)
    return mat


def _hdri_path():
    """번들 스튜디오 HDRI 경로. system_resource(path=...) 는 5.2 에서 빈 문자열을 돌려줘 직접 잇는다."""
    for light in bpy.context.preferences.studio_lights:
        if light.type == 'WORLD' and os.path.basename(light.path) == HDRI_NAME:
            return light.path
    base = bpy.utils.system_resource('DATAFILES')
    path = os.path.join(base, "studiolights", "world", HDRI_NAME) if base else ""
    return path if os.path.isfile(path) else ""


def _world(name, created):
    world = bpy.data.worlds.new(name)
    world.use_nodes = True
    nt = world.node_tree
    bg = next(n for n in nt.nodes if n.type == 'BACKGROUND')
    bg.inputs["Strength"].default_value = 0.25
    path = _hdri_path()
    if path:
        image = bpy.data.images.load(path, check_existing=False)
        image.pack()                                   # 다른 PC 에서 열어도 같은 조명이 나오게 파일에 담는다
        created.append(image)
        env = nt.nodes.new("ShaderNodeTexEnvironment")
        env.image = image
        nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    else:
        bg.inputs["Color"].default_value = (0.8, 0.8, 0.8, 1.0)
    return world


def _frame_distance(direction, target, corners, lens, sensor, aspect, margin=0.1):
    """카메라가 target 을 direction 쪽에서 볼 때 corners 가 화면 안(여백 margin)에 드는 최소 거리."""
    forward = -direction.normalized()
    right = forward.cross(Vector((0, 0, 1))).normalized()
    up = right.cross(forward).normalized()
    half_w = sensor / 2 / lens * (1 - margin)          # 센서 맞춤 AUTO: 긴 변이 sensor
    if aspect >= 1:
        tan_x, tan_y = half_w, half_w / aspect
    else:
        tan_x, tan_y = half_w * aspect, half_w
    need = 0.0
    for c in corners:
        rel = c - target
        depth = rel.dot(forward)
        need = max(need, abs(rel.dot(right)) / tan_x - depth, abs(rel.dot(up)) / tan_y - depth)
    return need


def build(scene, objects, kind='CHARACTER'):
    """scene 에 스튜디오를 세운다. 만든 데이터블록 목록을 돌려준다(remove 로 지운다). 메시가 없으면 빈 목록."""
    bounds = model_bounds(objects)
    if bounds is None:
        return []
    lo, hi = bounds
    size = hi - lo
    height = max(size.z, 1e-3)
    span = max(size.x, size.y, size.z)
    center = (lo + hi) / 2
    scale = span / REFERENCE_HEIGHT
    power = scale * scale
    created = []

    coll = bpy.data.collections.new("LP3D_Studio")
    created.append(coll)
    scene.collection.children.link(coll)

    def add(name, data):
        obj = bpy.data.objects.new(name, data)
        coll.objects.link(obj)
        created.extend([obj, data])
        return obj

    # 배경 — 모델 뒤쪽(+Y)으로 말려 올라가는 호리존트. 중성 그레이가 파스텔·원색 모두를 받쳐 준다
    backdrop = add("Studio_Backdrop", _cyclorama("Studio_Backdrop", span * 6, lo.z, hi.y + span * 0.9, span * 0.8))
    mat = _material("Studio_Backdrop", (0.26, 0.26, 0.27), 0.85)
    created.append(mat)
    backdrop.data.materials.append(mat)
    backdrop.visible_shadow = True

    # 3점 조명 — 정면은 -Y. 키(앞 왼쪽 위·큰 소프트박스·약간 따뜻) · 필(앞 오른쪽·차갑고 약하게) · 림(뒤 위)
    def area(name, azimuth, elevation, dist, energy, light_size, color):
        data = bpy.data.lights.new(name, 'AREA')
        data.energy = energy * power
        data.size = light_size * scale
        data.color = color
        obj = add(name, data)
        a, e = math.radians(azimuth), math.radians(elevation)
        offset = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e))) * dist * scale
        obj.location = center + offset
        _look_at(obj, center)
        return obj

    area("Studio_Key", -40, 40, 2.6, 300, 1.0, (1.0, 0.98, 0.95))
    area("Studio_Fill", 55, 15, 2.8, 90, 2.2, (0.9, 0.95, 1.0))
    area("Studio_Rim", 165, 45, 2.4, 350, 1.0, (1.0, 1.0, 1.0))

    world = _world("Studio_World", created)
    created.append(world)
    scene.world = world

    # 카메라 — 정면에서 25° 돌린 3/4 구도, 살짝 내려다본다. 캐릭터는 세로(4:5), 오브젝트는 정사각
    portrait = kind == 'CHARACTER' or height > max(size.x, size.y) * 1.3
    scene.render.resolution_x, scene.render.resolution_y = (1080, 1350) if portrait else (1200, 1200)
    scene.render.resolution_percentage = 100
    cam_data = bpy.data.cameras.new("Studio_Camera")
    cam_data.lens = 85 if portrait else 70
    cam_data.sensor_fit = 'AUTO'
    cam = add("Studio_Camera", cam_data)
    target = center.copy()
    a, e = math.radians(-25), math.radians(10)
    direction = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
    corners = [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
    aspect = scene.render.resolution_x / scene.render.resolution_y
    dist = _frame_distance(direction, target, corners, cam_data.lens, cam_data.sensor_width, aspect, 0.12)
    cam.location = target + direction * dist
    _look_at(cam, target)
    cam_data.clip_start = max(0.01, dist * 0.01)
    cam_data.clip_end = dist + span * 20
    scene.camera = cam

    _render_settings(scene)
    scene[STUDIO_MARK] = 1
    return created


def _render_settings(scene):
    try:
        scene.render.engine = _engine_id()
    except TypeError:
        pass
    eevee = scene.eevee
    for attr, value in (("taa_render_samples", 64), ("taa_samples", 16), ("use_raytracing", True),
                        ("use_shadows", True), ("shadow_ray_count", 2), ("shadow_step_count", 8),
                        ("use_gtao", True)):
        if hasattr(eevee, attr):
            try:
                setattr(eevee, attr, value)
            except (TypeError, AttributeError):
                pass
    view = scene.view_settings
    # Khronos PBR Neutral 은 베이스컬러를 원화 색 그대로 보여 준다 — AgX 는 파스텔을 탁하게, Standard 는 하이라이트를
    # 날린다(실측 2026-10-10, 같은 조명 4종 비교). 4.2 미만에는 없어 AgX → Filmic 순으로 물러선다
    if _try_set(view, "view_transform", "Khronos PBR Neutral", "AgX", "Filmic"):
        _try_set(view, "look", "None")
    view.exposure = -0.4
    view.gamma = 1.0


def remove(created):
    """build 가 만든 데이터블록을 지운다 — 오브젝트를 먼저 지워야 데이터 사용자가 0 이 된다."""
    order = (bpy.types.Object, bpy.types.Collection, bpy.types.Light, bpy.types.Camera, bpy.types.Mesh,
             bpy.types.Material, bpy.types.World, bpy.types.Image)
    collections = {bpy.types.Object: bpy.data.objects, bpy.types.Collection: bpy.data.collections,
                   bpy.types.Light: bpy.data.lights, bpy.types.Camera: bpy.data.cameras,
                   bpy.types.Mesh: bpy.data.meshes, bpy.types.Material: bpy.data.materials,
                   bpy.types.World: bpy.data.worlds, bpy.types.Image: bpy.data.images}
    for kind in order:
        for block in [b for b in created if isinstance(b, kind)]:
            try:
                collections[kind].remove(block)
            except (ReferenceError, RuntimeError):
                pass


# ---------- 파일을 열 때 뷰포트 ----------

def show_in_viewports():
    """스튜디오 씬이면 3D 뷰를 머티리얼 미리보기 + 씬 조명·월드 + 카메라 뷰로 바꾼다."""
    scene = bpy.context.scene
    if scene is None or not scene.get(STUDIO_MARK):
        return None
    wm = bpy.context.window_manager
    for window in (wm.windows if wm else []):
        for area in (window.screen.areas if window.screen else []):
            if area.type != 'VIEW_3D':
                continue
            for space in area.spaces:
                if space.type != 'VIEW_3D':
                    continue
                space.shading.type = 'MATERIAL'
                space.shading.use_scene_lights = True
                space.shading.use_scene_world = True
                if scene.camera is not None and space.region_3d is not None:
                    space.region_3d.view_perspective = 'CAMERA'
            area.tag_redraw()
    return None


def on_load_post():
    # 파일을 연 직후에는 창·영역이 아직 갱신 전일 수 있어 한 틱 미룬다
    bpy.app.timers.register(show_in_viewports, first_interval=0.1)
