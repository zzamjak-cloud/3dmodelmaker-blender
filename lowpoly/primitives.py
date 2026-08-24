# 프리미티브 생성 헬퍼 — bpy.ops 대신 bmesh/data API 사용 (컨텍스트 의존 오류 방지)
import math

import bmesh
import bpy
from mathutils import Matrix, Vector


def _new_object(name: str, bm: bmesh.types.BMesh, location, rotation, scale) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = Vector(location)
    obj.rotation_euler = rotation
    obj.scale = Vector(scale)
    from . import link_to_root
    link_to_root(obj)
    return obj


def box(name="Box", size=(1, 1, 1), location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """직육면체를 만든다. size=(x,y,z) 미터 단위, 원점은 중심."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    return _new_object(name, bm, location, rotation, (1, 1, 1))


def cylinder(name="Cylinder", radius=0.5, depth=1.0, segments=8,
             location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """원기둥. 로우폴리는 segments 6~12 권장. depth는 Z축 높이, 원점은 중심."""
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=segments,
        radius1=radius, radius2=radius, depth=depth,
    )
    return _new_object(name, bm, location, rotation, (1, 1, 1))


def cone(name="Cone", radius_bottom=0.5, radius_top=0.0, depth=1.0, segments=8,
         location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """원뿔/절두체. radius_top>0이면 절두체(테이퍼 기둥)가 된다."""
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=segments,
        radius1=radius_bottom, radius2=radius_top, depth=depth,
    )
    return _new_object(name, bm, location, rotation, (1, 1, 1))


def sphere(name="Sphere", radius=0.5, subdivisions=1,
           location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """아이코스피어. 로우폴리는 subdivisions 1~2만 사용할 것 (1=80트라이)."""
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=radius)
    return _new_object(name, bm, location, rotation, (1, 1, 1))


def plane(name="Plane", size=(1, 1), location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """XY 평면 사각형. size=(x,y)."""
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=0.5)
    bmesh.ops.scale(bm, vec=Vector((size[0], size[1], 1.0)), verts=bm.verts)
    return _new_object(name, bm, location, rotation, (1, 1, 1))


def lathe(name="Lathe", profile=((0.3, 0.0), (0.4, 0.5), (0.3, 1.0)), segments=8,
          location=(0, 0, 0), rotation=(0, 0, 0)) -> bpy.types.Object:
    """회전체(선반 성형). profile은 아래→위 순서의 (반지름, z높이) 목록.

    배럴/항아리/병/나무줄기/탑 지붕처럼 곡률 실루엣이 필요한 형태에 최적.
    반지름 0이면 뾰족한 극점(원뿔 끝)이 된다. segments 6~12 권장.
    예: 배럴 = [(0.30,0), (0.40,0.45), (0.30,0.9)]"""
    bm = bmesh.new()
    rings = []
    for r, z in profile:
        if r <= 1e-4:
            rings.append(bm.verts.new((0.0, 0.0, z)))  # 극점(단일 버텍스)
            continue
        ring = []
        for i in range(segments):
            angle = math.tau * i / segments
            ring.append(bm.verts.new((r * math.cos(angle), r * math.sin(angle), z)))
        rings.append(ring)
    for lower, upper in zip(rings, rings[1:]):
        if isinstance(lower, list) and isinstance(upper, list):
            for i in range(segments):
                bm.faces.new((lower[i], lower[(i + 1) % segments],
                              upper[(i + 1) % segments], upper[i]))
        elif isinstance(lower, list):  # 위가 극점
            for i in range(segments):
                bm.faces.new((lower[i], lower[(i + 1) % segments], upper))
        elif isinstance(upper, list):  # 아래가 극점
            for i in range(segments):
                bm.faces.new((lower, upper[(i + 1) % segments], upper[i]))
    # 끝이 극점이 아니면 n각형으로 캡
    if isinstance(rings[0], list):
        bm.faces.new(tuple(reversed(rings[0])))
    if isinstance(rings[-1], list):
        bm.faces.new(tuple(rings[-1]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _new_object(name, bm, location, rotation, (1, 1, 1))


def join(objects, name="Joined") -> bpy.types.Object:
    """여러 오브젝트를 하나의 메시로 합친다(트랜스폼 적용됨). 합쳐진 오브젝트를 반환."""
    # 방금 설정한 location/rotation이 matrix_world에 반영되도록 depsgraph 갱신
    bpy.context.view_layer.update()
    bm = bmesh.new()
    merged_mesh = bpy.data.meshes.new(name)
    materials = []  # 머티리얼 슬롯 병합 목록
    for obj in objects:
        mesh_copy = obj.data.copy()
        mesh_copy.transform(obj.matrix_world)
        # 소스 슬롯을 병합 목록 인덱스로 리매핑
        index_map = []
        for mat in mesh_copy.materials:
            if mat not in materials:
                materials.append(mat)
            index_map.append(materials.index(mat))
        if index_map:
            for poly in mesh_copy.polygons:
                poly.material_index = index_map[poly.material_index]
        bm.from_mesh(mesh_copy)
        bpy.data.meshes.remove(mesh_copy)
    bm.to_mesh(merged_mesh)
    bm.free()
    for mat in materials:
        merged_mesh.materials.append(mat)
    result = bpy.data.objects.new(name, merged_mesh)
    from . import link_to_root
    link_to_root(result)
    # 원본 제거
    for obj in list(objects):
        mesh = obj.data
        bpy.data.objects.remove(obj)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    return result
