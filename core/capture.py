# 멀티앵글 뷰포트 캡처: WORKBENCH 렌더 + 임시 카메라
import math
import os

import bpy
from mathutils import Vector

# (이름, 방향 벡터, 카메라 높이 비율) — 앞쪽 iso가 가장 정보량이 많아 우선
_ANGLES = [
    ("iso_front", Vector((1, -1, 0.8))),
    ("iso_back", Vector((-1, 1, 0.8))),
    ("front", Vector((0, -1, 0.15))),
    ("side", Vector((1, 0, 0.15))),
    ("top", Vector((0.01, -0.01, 1))),
    ("iso_left", Vector((-1, -1, 0.8))),
]

# 원상복구할 렌더/디스플레이 설정 키
_RENDER_KEYS = ("engine", "resolution_x", "resolution_y", "resolution_percentage",
                "filepath", "film_transparent")


def _collection_bounds(coll):
    """컬렉션 내 메시 오브젝트들의 월드 바운딩 박스 (중심, 반경)."""
    points = []
    for obj in coll.objects:
        if obj.type != 'MESH':
            continue
        for corner in obj.bound_box:
            points.append(obj.matrix_world @ Vector(corner))
    if not points:
        return Vector((0, 0, 0)), 1.0
    lo = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    hi = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    center = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 0.1)
    return center, radius


def capture_collection(collection_name: str, out_dir: str, count: int = 4, resolution: int = 512):
    """세션 컬렉션을 여러 앵글로 렌더해 PNG 경로 리스트와 통계를 반환한다."""
    scene = bpy.context.scene
    coll = bpy.data.collections.get(collection_name)
    if not coll or not any(o.type == 'MESH' for o in coll.objects):
        return [], {}

    # 최신 트랜스폼이 matrix_world에 반영되도록 갱신 후 바운드 계산
    bpy.context.view_layer.update()
    center, radius = _collection_bounds(coll)

    # 임시 카메라 생성
    cam_data = bpy.data.cameras.new("LP3D_CaptureCam")
    # FOV 기반 거리: 모델이 프레임의 ~70%를 차지하도록 여유를 둔다
    distance = radius / math.tan(cam_data.angle / 2) * 1.4
    cam_obj = bpy.data.objects.new("LP3D_CaptureCam", cam_data)
    scene.collection.objects.link(cam_obj)

    # 렌더 설정 백업 및 오버라이드 (WORKBENCH: 빠르고 결정적)
    saved_render = {k: getattr(scene.render, k) for k in _RENDER_KEYS}
    saved_camera = scene.camera
    shading = scene.display.shading
    saved_shading = (shading.light, shading.color_type, shading.show_object_outline)
    # 세션 컬렉션 외 오브젝트는 렌더에서 숨긴다 (씬의 기존 오브젝트가 찍히지 않도록)
    session_objects = set(coll.objects)
    saved_hidden = [(obj, obj.hide_render) for obj in scene.collection.all_objects]
    try:
        for obj, _ in saved_hidden:
            if obj not in session_objects:
                obj.hide_render = True
        scene.render.engine = 'BLENDER_WORKBENCH'
        scene.render.resolution_x = resolution
        scene.render.resolution_y = resolution
        scene.render.resolution_percentage = 100
        scene.render.film_transparent = False
        shading.light = 'STUDIO'
        shading.color_type = 'TEXTURE'  # 팔레트 텍스처가 보이도록
        shading.show_object_outline = True  # 실루엣 파악에 도움
        scene.camera = cam_obj

        paths = []
        for name, direction in _ANGLES[:count]:
            d = direction.normalized()
            cam_obj.location = center + d * distance
            look = center - cam_obj.location
            cam_obj.rotation_euler = look.to_track_quat('-Z', 'Y').to_euler()
            path = os.path.join(out_dir, f"capture_{name}.png")
            scene.render.filepath = path
            bpy.ops.render.render(write_still=True)
            paths.append(path)
    finally:
        # 원상복구
        for obj, hidden in saved_hidden:
            try:
                obj.hide_render = hidden
            except ReferenceError:
                pass  # 렌더 중 제거된 오브젝트는 무시
        for k, v in saved_render.items():
            setattr(scene.render, k, v)
        scene.camera = saved_camera
        shading.light, shading.color_type, shading.show_object_outline = saved_shading
        bpy.data.objects.remove(cam_obj)
        bpy.data.cameras.remove(cam_data)

    from ..lowpoly.cleanup import collection_tri_count
    stats = {
        "트라이앵글 수": collection_tri_count(coll),
        "오브젝트 수": len([o for o in coll.objects if o.type == 'MESH']),
        "바운딩 박스 크기(m)": f"{radius * 2:.2f}",
    }
    return paths, stats
