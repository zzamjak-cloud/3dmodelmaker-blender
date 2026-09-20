# QuadriFlow 자식 프로세스 — 부모 Blender 가 얼지 않도록 별도 프로세스에서 돌린다
#
# 부모가 넘긴 .blend 에서 메시 하나를 읽어 QuadriFlow 를 걸고 결과를 다른 .blend 로 쓴다.
# 애드온 없이 `blender --background --factory-startup --python 이파일 -- 입력 출력 목표면수 대칭`
# 으로 실행되므로 이 파일은 패키지 상대 임포트를 쓰지 않는다. 마지막 인자는 시드다.
import sys

import bpy


def main(argv) -> int:
    src, dst, target, symmetry, seed = argv[0], argv[1], int(argv[2]), argv[3] == '1', int(argv[4])
    with bpy.data.libraries.load(src) as (data_from, data_to):
        data_to.meshes = data_from.meshes[:1]
    if not data_to.meshes:
        return 2
    obj = bpy.data.objects.new("LP3D_QuadriFlow", data_to.meshes[0])
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    # use_mesh_symmetry 는 메시 자체의 대칭 플래그를 읽는다 — 새 메시는 전부 꺼져 있어(use_mirror_x False)
    # 켜 달라고 해도 조용히 무시된다(실측 2026-09-21: 결과 대칭 정점 비율 0.37, 켠 뒤 0.99)
    obj.data.use_mirror_x = symmetry
    obj.data.use_mirror_y = False
    obj.data.use_mirror_z = False
    before = len(obj.data.polygons)
    bpy.ops.object.quadriflow_remesh(mode='FACES', target_faces=target,
                                     use_mesh_symmetry=symmetry, use_preserve_sharp=True,
                                     use_preserve_boundary=False, seed=seed)
    if len(obj.data.polygons) == before:
        return 3   # 조용히 아무것도 안 했다 — 부모가 데시메이트로 폴백한다
    bpy.data.libraries.write(dst, {obj.data}, fake_user=True)
    return 0


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:]
    sys.exit(main(arguments))
