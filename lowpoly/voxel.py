# 복셀 헬퍼 — 격자에 맞춘 정육면체 덩어리 (VOXEL 스타일 전용)
#
# 복셀 스타일은 프롬프트만으로는 성립하지 않는다. "격자에 맞춰라"고 지시해도
# 에이전트는 box의 크기·위치를 임의의 실수로 잡아 살짝씩 어긋난 덩어리를 만든다.
# 격자 스냅을 코드로 강제해야 로블록스·마인크래프트류의 성격이 나온다.
#
# 내부에 파묻히는 면은 만들지 않는다 — cleanup의 은면 컬링에 맡기면 인접 큐브가
# 맞닿은 면까지 개별 폴리곤으로 한 번 만들어져 트라이가 몇 배로 튄다.
import bmesh
import bpy
from mathutils import Vector

from .names import safe_id_name

# 면 방향 6종: (이웃 오프셋, 그 면을 이루는 코너 4개의 단위 좌표)
_FACES = (
    ((1, 0, 0), ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1))),
    ((-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0))),
    ((0, 1, 0), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0))),
    ((0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1))),
    ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))),
    ((0, 0, -1), ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0))),
)


def _cell_set(cells):
    """(i,j,k) 정수 좌표 집합으로 정규화한다. 실수가 와도 내림해 격자에 붙인다."""
    out = set()
    for cell in cells or ():
        try:
            i, j, k = cell
        except (TypeError, ValueError):
            raise ValueError("복셀 셀은 (i, j, k) 정수 3개여야 한다: %r" % (cell,))
        out.add((int(i // 1), int(j // 1), int(k // 1)))
    return out


def voxel(name="Voxel", cells=(), size=0.25, origin=(0.0, 0.0, 0.0)) -> bpy.types.Object:
    """격자 셀 목록을 정육면체 덩어리 하나로 만든다. 만든 오브젝트를 반환.

    cells는 [(i,j,k), ...] 정수 격자 좌표이고 size는 셀 한 변의 길이(m)다.
    셀 (i,j,k)는 월드 좌표 origin + (i,j,k)*size 에서 시작하는 정육면체를 차지한다.
    **셀끼리 맞닿은 안쪽 면은 만들지 않는다** — 붙여 쌓아도 트라이가 늘지 않는다.

    복셀 스타일에서 모든 파트는 이 헬퍼로 만들어라. box를 임의 좌표에 놓으면
    격자가 어긋나 복셀로 읽히지 않는다.
    예: lp.voxel("Body", [(x, y, z) for x in range(4) for y in range(3) for z in range(5)], size=0.2)"""
    filled = _cell_set(cells)
    if not filled:
        raise ValueError("복셀에 채워진 셀이 하나도 없다")
    step = float(size)
    if step <= 0:
        raise ValueError("복셀 셀 크기는 0보다 커야 한다")
    base = Vector(origin)

    bm = bmesh.new()
    vert_cache = {}

    def _vert(key):
        vert = vert_cache.get(key)
        if vert is None:
            vert = bm.verts.new(base + Vector(key) * step)
            vert_cache[key] = vert
        return vert

    for (i, j, k) in sorted(filled):
        for (di, dj, dk), corners in _FACES:
            if (i + di, j + dj, k + dk) in filled:
                continue  # 이웃이 막고 있는 면은 만들지 않는다
            bm.faces.new([_vert((i + cx, j + cy, k + cz)) for cx, cy, cz in corners])
    bm.normal_update()

    name = safe_id_name(name)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    from . import link_to_root
    link_to_root(obj)
    return obj


def voxel_box(name="VoxelBox", dims=(1, 1, 1), size=0.25, origin=(0.0, 0.0, 0.0),
              hollow=False) -> bpy.types.Object:
    """속이 찬(또는 빈) 직육면체 복셀 덩어리. dims=(폭, 깊이, 높이) 셀 개수.

    hollow=True면 껍데기 한 겹만 남긴다 — 방·상자 안쪽을 만들 때 쓴다.
    예: lp.voxel_box("Wall", dims=(10, 1, 6), size=0.2)"""
    nx, ny, nz = (max(1, int(d)) for d in dims)
    cells = []
    for i in range(nx):
        for j in range(ny):
            for k in range(nz):
                if hollow and 0 < i < nx - 1 and 0 < j < ny - 1 and 0 < k < nz - 1:
                    continue
                cells.append((i, j, k))
    return voxel(name, cells, size=size, origin=origin)


def voxel_column(name="VoxelColumn", height=4, size=0.25,
                 origin=(0.0, 0.0, 0.0)) -> bpy.types.Object:
    """한 칸 굵기의 세로 기둥 — 다리·나무줄기·기둥용.

    예: lp.voxel_column("Trunk", height=6, size=0.25)"""
    return voxel(name, [(0, 0, k) for k in range(max(1, int(height)))],
                 size=size, origin=origin)
