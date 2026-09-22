# QuadriFlow 자식 프로세스 — 부모 Blender 가 얼지 않도록 별도 프로세스에서 돌린다
#
# 부모가 넘긴 .blend 에서 메시 하나를 읽어 QuadriFlow 를 걸고 결과를 다른 .blend 로 쓴다.
# 애드온 없이 `blender --background --factory-startup --python 이파일 -- 입력 출력 목표면수 경계보존 시드`
# 로 실행되므로 이 파일은 패키지 상대 임포트를 쓰지 않는다.
import sys

import bpy


def main(argv) -> int:
    src, dst, target, preserve_boundary, seed = argv[0], argv[1], int(argv[2]), argv[3] == '1', int(argv[4])
    with bpy.data.libraries.load(src) as (data_from, data_to):
        data_to.meshes = data_from.meshes[:1]
    if not data_to.meshes:
        return 2
    obj = bpy.data.objects.new("LP3D_QuadriFlow", data_to.meshes[0])
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    # QuadriFlow 자체의 대칭 모드(use_mesh_symmetry)는 쓰지 않는다 — 어떤 시드·옵션에서도 대칭면을 따라
    # 수십 엣지짜리 구멍을 남기거나 정점이 NaN 인 메시를 내놓는다. 부모가 양의 반쪽만 잘라 보내고
    # 경계를 보존해(use_preserve_boundary) 깐 뒤 미러 모디파이어로 용접한다.
    before = len(obj.data.polygons)
    bpy.ops.object.quadriflow_remesh(mode='FACES', target_faces=target,
                                     use_mesh_symmetry=False, use_preserve_sharp=True,
                                     use_preserve_boundary=preserve_boundary, seed=seed)
    if len(obj.data.polygons) == before:
        return 3   # 조용히 아무것도 안 했다 — 부모가 데시메이트로 폴백한다
    bpy.data.libraries.write(dst, {obj.data}, fake_user=True)
    return 0


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:]
    sys.exit(main(arguments))
