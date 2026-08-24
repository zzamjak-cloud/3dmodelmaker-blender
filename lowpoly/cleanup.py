# 게임레디 정리 패스: 트랜스폼 적용, 원점 정규화, 노멀 재계산, 플랫 셰이딩
import bmesh
import bpy
from mathutils import Matrix, Vector


def game_ready(obj, origin='BOTTOM'):
    """오브젝트를 게임엔진 임포트에 적합하게 정리한다.

    트랜스폼을 메시에 굽고, 원점을 바닥 중앙(origin='BOTTOM') 또는
    중심(origin='CENTER')으로 옮기고, 노멀 재계산 + 플랫 셰이딩을 적용한다."""
    mesh = obj.data
    # 1) 트랜스폼 적용 (최신 matrix_world 보장을 위해 depsgraph 갱신)
    bpy.context.view_layer.update()
    mesh.transform(obj.matrix_world)
    obj.matrix_world = Matrix.Identity(4)
    # 2) 원점 정규화
    if mesh.vertices:
        xs = [v.co.x for v in mesh.vertices]
        ys = [v.co.y for v in mesh.vertices]
        zs = [v.co.z for v in mesh.vertices]
        center = Vector(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, 0.0))
        center.z = min(zs) if origin == 'BOTTOM' else (min(zs) + max(zs)) / 2
        mesh.transform(Matrix.Translation(-center))
        obj.location = center
    # 3) 노멀 재계산(바깥 방향)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    # 4) 플랫 셰이딩
    for poly in mesh.polygons:
        poly.use_smooth = False
    mesh.update()
    return obj


def tri_count(obj) -> int:
    """트라이앵글 수를 센다 (폴리 버짓 검사용)."""
    return sum(max(len(p.vertices) - 2, 0) for p in obj.data.polygons)


def collection_tri_count(coll) -> int:
    return sum(tri_count(o) for o in coll.objects if o.type == 'MESH')
