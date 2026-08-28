# 에이전트 코드 실행: AST 안전검사 + 스냅샷 롤백 exec 샌드박스
#
# 완전한 샌드박스가 아니라 "사고 방지" 수준의 안전장치다.
# 위험 모듈 import와 파일/프로세스 조작 빌트인 호출을 거부한다.
import ast
import math
import os
import random
import runpy
import traceback

import bmesh
import bpy

_FORBIDDEN_IMPORTS = {
    "os", "sys", "subprocess", "shutil", "socket", "pathlib", "ctypes",
    "urllib", "http", "importlib", "pickle", "tempfile",
}
_FORBIDDEN_CALLS = {"open", "exec", "eval", "__import__", "compile", "input", "getattr", "setattr"}

# 스냅샷/롤백 대상 데이터블록 컬렉션
_DATA_KINDS = ("objects", "meshes", "materials", "images", "collections", "node_groups")


def check_code(code: str):
    """금지 패턴이 있으면 오류 메시지, 없으면 None 반환."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"문법 오류: {e}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _FORBIDDEN_IMPORTS:
                    return f"금지된 import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in _FORBIDDEN_IMPORTS:
                return f"금지된 import: {node.module}"
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _FORBIDDEN_CALLS:
                return f"금지된 호출: {func.id}()"
    return None


def snapshot():
    # 팔레트는 읽기 전용 고정 텍스처이므로 별도로 보존할 상태가 없다
    return {kind: set(getattr(bpy.data, kind).keys()) for kind in _DATA_KINDS}


def rollback(snap):
    """스냅샷 이후 생긴 데이터블록을 제거한다. 오브젝트 → 컬렉션 → 데이터 순."""
    from ..lowpoly.palette import PALETTE_IMAGE

    for name in set(bpy.data.objects.keys()) - snap["objects"]:
        obj = bpy.data.objects.get(name)
        if obj:
            bpy.data.objects.remove(obj)
    for name in set(bpy.data.collections.keys()) - snap["collections"]:
        coll = bpy.data.collections.get(name)
        if coll:
            bpy.data.collections.remove(coll)
    for kind in ("meshes", "materials", "images", "node_groups"):
        data = getattr(bpy.data, kind)
        for name in set(data.keys()) - snap[kind]:
            # 실행이 실패해도 고정 팔레트 이미지는 지우지 않는다
            if kind == "images" and name == PALETTE_IMAGE:
                continue
            block = data.get(name)
            if block and block.users == 0:
                data.remove(block)


def clear_collection(collection_name: str):
    """재생성 전에 세션 컬렉션 내용을 비운다 (매 턴 전체 재작성 규약)."""
    coll = bpy.data.collections.get(collection_name)
    if not coll:
        return
    for obj in list(coll.objects):
        mesh = obj.data if obj.type == 'MESH' else None
        bpy.data.objects.remove(obj)
        if mesh and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def execute(code: str, collection_name: str, seed: int = 0, workdir: str = None):
    """에이전트 코드를 실행한다. 성공 시 (True, None), 실패 시 (False, traceback 문자열).

    코드는 workdir(기본: Blender 임시 폴더)에 스크립트 파일로 저장 후 runpy로 실행한다."""
    error = check_code(code)
    if error:
        return False, error

    from .. import lowpoly
    lowpoly.set_session(collection_name)
    clear_collection(collection_name)

    try:
        bpy.ops.ed.undo_push(message="LP3D 생성 전")
    except RuntimeError:
        pass  # 백그라운드 모드에서는 undo 스택이 없음
    snap = snapshot()
    namespace = {
        "bpy": bpy,
        "bmesh": bmesh,
        "math": math,
        "random": random.Random(seed),  # 시드 고정으로 재현성 확보
        "lp": lowpoly,
    }
    script_path = os.path.join(workdir or bpy.app.tempdir, "lp3d_agent_code.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(code)
    try:
        runpy.run_path(script_path, init_globals=namespace)
        return True, None
    except Exception:
        tb = traceback.format_exc()
        rollback(snap)
        return False, tb
