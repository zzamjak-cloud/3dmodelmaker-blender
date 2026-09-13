# 6면도 가이드 렌더·contact sheet 합성·AI 결과 크롭 (bpy 의존)
#
# 카메라 축 배치는 bake.VIEW_SPECS와 일치해야 한다 — 투영 베이크가 같은 축으로
# 생성 이미지를 모델에 다시 씌운다.
import math
import os
from array import array

import bpy
from mathutils import Vector

from .layout import LAYOUT, SheetLayout

CAPTURE_RESOLUTION = 1024        # 시점당 캡처 크기 — 실루엣이 또렷해야 AI가 따라 그린다
BACKGROUND = (1.0, 1.0, 1.0)

# 시점별 카메라 위치(단위 방향)
_CAMERA_DIRECTIONS = {
    "FRONT": Vector((0.0, -1.0, 0.0)),
    "RIGHT": Vector((1.0, 0.0, 0.0)),
    "BACK": Vector((0.0, 1.0, 0.0)),
    "LEFT": Vector((-1.0, 0.0, 0.0)),
    "TOP": Vector((0.0, 0.0, 1.0)),
    "BOTTOM": Vector((0.0, 0.0, -1.0)),
}


def projection(context, objects) -> dict:
    """모델 캡처가 쓰는 직교 카메라 계약 (중심·크기·거리)."""
    depsgraph = context.evaluated_depsgraph_get()
    points = []
    for obj in objects:
        ev = obj.evaluated_get(depsgraph)
        points.extend(ev.matrix_world @ Vector(c) for c in ev.bound_box)
    lo = Vector(tuple(min(p[a] for p in points) for a in range(3)))
    hi = Vector(tuple(max(p[a] for p in points) for a in range(3)))
    extent = hi - lo
    return {
        "center": (lo + hi) * 0.5,
        "extent": extent,
        "ortho_scale": max(extent.x, extent.y, extent.z, 0.01) * 1.2,
        "camera_distance": max(extent.length, 1.0) * 2.0,
    }


def render_views(context, objects, out_dir: str, resolution: int = CAPTURE_RESOLUTION,
                 views=LAYOUT.views) -> dict:
    """모델을 임시 직교 카메라로 시점별 렌더한다. 반환은 시점 이름(대문자) → PNG 경로.

    Workbench + Standard 뷰 트랜스폼: AgX는 흰 배경을 회색으로 눌러 실루엣 대비를
    깎으므로 색 변환 없이 저장한다. 스튜디오 조명은 AI가 입체를 읽도록 남기고,
    색은 현재 머티리얼(팔레트) 텍스처를 그대로 쓴다."""
    scene = context.scene
    proj = projection(context, objects)
    center, extent = proj["center"], proj["extent"]
    scale, distance = proj["ortho_scale"], proj["camera_distance"]
    os.makedirs(out_dir, exist_ok=True)

    cam_data = bpy.data.cameras.new("LP3D_TexCaptureCam")
    camera = bpy.data.objects.new("LP3D_TexCaptureCam", cam_data)
    scene.collection.objects.link(camera)
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = scale
    cam_data.clip_start = max(scale * 1.0e-5, 1.0e-4)
    cam_data.clip_end = max(1000.0, distance + extent.length * 3.0)

    render, shading, vs = scene.render, scene.display.shading, scene.view_settings
    saved = {
        "view_transform": vs.view_transform, "look": vs.look,
        "exposure": vs.exposure, "gamma": vs.gamma,
        "camera": scene.camera, "engine": render.engine, "filepath": render.filepath,
        "resolution_x": render.resolution_x, "resolution_y": render.resolution_y,
        "resolution_percentage": render.resolution_percentage,
        "film_transparent": render.film_transparent,
        "file_format": render.image_settings.file_format,
        "light": shading.light, "color_type": shading.color_type,
        "background_type": shading.background_type,
        "background_color": tuple(shading.background_color),
        "outline": shading.show_object_outline,
    }
    hidden = {obj: obj.hide_render for obj in scene.objects if obj != camera}
    selected = set(objects)
    paths = {}
    try:
        for obj in hidden:
            obj.hide_render = obj not in selected
        render.engine = 'BLENDER_WORKBENCH'
        vs.view_transform = 'Standard'
        vs.look = 'None'
        vs.exposure = 0.0
        vs.gamma = 1.0
        shading.light = 'STUDIO'
        shading.color_type = 'TEXTURE'
        shading.show_object_outline = False
        shading.background_type = 'VIEWPORT'
        shading.background_color = BACKGROUND
        scene.camera = camera
        render.resolution_x = render.resolution_y = int(resolution)
        render.resolution_percentage = 100
        render.film_transparent = False
        render.image_settings.file_format = 'PNG'
        for view in views:
            camera.location = center + _CAMERA_DIRECTIONS[view] * distance
            if view == "TOP":
                # 시선과 up이 평행해 track이 불안정 — 화면 오른쪽=+X, 위=+Y로 직접 지정
                camera.rotation_euler = (0.0, 0.0, 0.0)
            elif view == "BOTTOM":
                camera.rotation_euler = (math.pi, 0.0, 0.0)  # 오른쪽=+X, 위=-Y
            else:
                camera.rotation_euler = (center - camera.location).to_track_quat('-Z', 'Y').to_euler()
            path = os.path.join(out_dir, f"guide_{view.lower()}.png")
            render.filepath = path
            bpy.ops.render.render(write_still=True)
            paths[view] = path
    finally:
        for obj, hide in hidden.items():
            obj.hide_render = hide
        scene.camera = saved["camera"]
        render.engine = saved["engine"]
        render.filepath = saved["filepath"]
        render.resolution_x = saved["resolution_x"]
        render.resolution_y = saved["resolution_y"]
        render.resolution_percentage = saved["resolution_percentage"]
        render.film_transparent = saved["film_transparent"]
        render.image_settings.file_format = saved["file_format"]
        vs.view_transform = saved["view_transform"]
        vs.look = saved["look"]
        vs.exposure = saved["exposure"]
        vs.gamma = saved["gamma"]
        shading.light = saved["light"]
        shading.color_type = saved["color_type"]
        shading.show_object_outline = saved["outline"]
        shading.background_type = saved["background_type"]
        shading.background_color = saved["background_color"]
        bpy.data.objects.remove(camera, do_unlink=True)
        bpy.data.cameras.remove(cam_data)
    return paths


def join_sheet(view_paths: dict, output_path: str, layout: SheetLayout = LAYOUT) -> str:
    """시점 렌더를 레이아웃 격자 한 장으로 잇는다 (첫 행이 위쪽)."""
    paths = [view_paths[v] for v in layout.views]
    sources = [bpy.data.images.load(p, check_existing=False) for p in paths]
    target = None
    try:
        width, height = sources[0].size
        if any(tuple(img.size) != (width, height) for img in sources) or width != height:
            raise RuntimeError("시점 렌더는 모두 같은 크기의 정사각이어야 합니다")
        ch = 4
        tw, th = layout.canvas_size(width)
        pixels = array("f", [*BACKGROUND, 1.0]) * (tw * th)
        buf = array("f", [0.0]) * (width * height * ch)
        for index, source in enumerate(sources):
            ox, oy = layout.cell_origin(tw, th, index, width)
            source.pixels.foreach_get(buf)
            for row in range(height):
                s = row * width * ch
                t = ((oy + row) * tw + ox) * ch
                pixels[t:t + width * ch] = buf[s:s + width * ch]
        target = bpy.data.images.new("LP3D_TexGuideSheet", width=tw, height=th, alpha=True)
        target.pixels.foreach_set(pixels)
        target.filepath_raw = output_path
        target.file_format = 'PNG'
        target.save()
        return output_path
    finally:
        if target is not None:
            bpy.data.images.remove(target)
        for img in sources:
            bpy.data.images.remove(img)


def split_sheet(path: str, layout: SheetLayout = LAYOUT) -> dict:
    """AI 결과 한 장을 셀 단위로 나눈다. 반환은 시점 이름(대문자) → PNG 경로."""
    source = bpy.data.images.load(path, check_existing=False)
    outputs = {}
    try:
        width, height = source.size
        ch = 4
        buf = array("f", [0.0]) * (width * height * ch)
        source.pixels.foreach_get(buf)
        stem = os.path.splitext(path)[0]
        for index, view in enumerate(layout.views):
            left, bottom, right, top = layout.cell_bounds(width, height, index)
            cw, chh = right - left, top - bottom
            crop = array("f", [0.0]) * (cw * chh * ch)
            for row in range(chh):
                s = ((bottom + row) * width + left) * ch
                t = row * cw * ch
                crop[t:t + cw * ch] = buf[s:s + cw * ch]
            image = bpy.data.images.new(f"LP3D_TexView_{view}", width=cw, height=chh, alpha=True)
            try:
                image.pixels.foreach_set(crop)
                out = f"{stem}_{view.lower()}.png"
                image.filepath_raw = out
                image.file_format = 'PNG'
                image.save()
                outputs[view] = out
            finally:
                bpy.data.images.remove(image)
    finally:
        bpy.data.images.remove(source)
    return outputs
