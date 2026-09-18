"""내장 및 사용자 베이스 메시의 목록, 로드, 독립 저장을 관리한다."""
import json
import os
from pathlib import Path
import uuid

import bpy
import bmesh

from ..lowpoly.base_templates import build_template

_ENUM_CACHE = []
# 파일 기반 내장 템플릿 — Blender Studio "Human Base Meshes" 번들(CC0)의 전신 베이스 메시.
# 눈·입 주변 동심 루프, 관절 루프, 얼굴 고밀도/팔다리 저밀도 분포를 갖춘 실제 애니메이션용
# 토폴로지다. lowpoly/templates/*.obj (OBJ는 쿼드를 보존한다 — glTF는 삼각형만 저장한다).
_FILE_BUILTINS = {
    'HUMANOID_MALE_STYLIZED': ('humanoid_male_stylized.obj', '인간형 · 남성 스타일라이즈드 (12.5k 쿼드)',
                               'Blender Studio CC0 베이스 메시 — 캐주얼 비율, 관절·얼굴 루프 완비'),
    'HUMANOID_FEMALE_STYLIZED': ('humanoid_female_stylized.obj', '인간형 · 여성 스타일라이즈드 (12.5k 쿼드)',
                                 'Blender Studio CC0 베이스 메시 — 캐주얼 비율, 관절·얼굴 루프 완비'),
    'HUMANOID_MALE_REALISTIC': ('humanoid_male_realistic.obj', '인간형 · 남성 사실적 (10.6k 쿼드)',
                                'Blender Studio CC0 베이스 메시 — 사실 비율'),
    'HUMANOID_FEMALE_REALISTIC': ('humanoid_female_realistic.obj', '인간형 · 여성 사실적 (10.6k 쿼드)',
                                  'Blender Studio CC0 베이스 메시 — 사실 비율'),
}
# 절차 생성 원형 — 손·발·입이 없는 편집용 뼈대. 네발형은 CC0 소스가 없어 이것만 있다
_BUILTINS = [(key, label, desc) for key, (_f, label, desc) in _FILE_BUILTINS.items()] + [
    ('HUMANOID', '인간형 · 절차 원형 (264 쿼드)', '얼굴 국소 루프와 관절 루프만 있는 편집용 원형'),
    ('QUADRUPED', '네발형 · 절차 원형 (264 쿼드)', '수평 몸통과 네 다리가 있는 편집용 원형'),
]
# 체형별 기본 템플릿 — character_template이 AUTO일 때
DEFAULT_FOR_KIND = {'HUMANOID': 'HUMANOID_MALE_STYLIZED', 'CREATURE': 'HUMANOID_MALE_STYLIZED',
                    'ANIMAL': 'QUADRUPED', 'AUTO': 'HUMANOID_MALE_STYLIZED'}
_TEMPLATE_FILE_DIR = Path(__file__).resolve().parent.parent / 'lowpoly' / 'templates'


def resolve_template(template_id, character_type='HUMANOID'):
    """'AUTO'나 빈 값을 체형별 기본 템플릿으로 바꾼다."""
    key = str(template_id or 'AUTO').strip()
    if key in ('', 'AUTO'):
        return DEFAULT_FOR_KIND.get(str(character_type or 'AUTO').upper(), DEFAULT_FOR_KIND['AUTO'])
    return key


def template_root():
    """애드온 업데이트와 분리된 사용자 저장 위치를 만든다."""
    custom = os.environ.get('LP3D_TEMPLATE_DIR')
    root = Path(custom or bpy.utils.user_resource('CONFIG', path='lowpoly3d/templates', create=True))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _entries():
    path = template_root() / 'index.json'
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))


def enum_items(context=None):
    """Blender EnumProperty가 참조할 문자열 수명을 유지한다."""
    try:
        entries = [(key, item['name'], item['kind']) for key, item in _entries().items()]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        _ENUM_CACHE[:] = [(key, name, description + ' (사용자 목록 읽기 오류: index.json 확인 필요)')
                          for key, name, description in _BUILTINS]
        return _ENUM_CACHE
    _ENUM_CACHE[:] = _BUILTINS + entries
    return _ENUM_CACHE


def _save_entry(identifier, name, kind):
    entries = _entries()
    entries[identifier] = {'name': name.strip() or '사용자 템플릿', 'kind': kind}
    from .user_files import write_json_atomic
    write_json_atomic(template_root() / 'index.json', entries)   # 사용자 CONFIG 경로 — 설치 폴더에 쓰지 않는다


def register_object(obj, name, kind='HUMANOID'):
    """선택 메시의 독립 사본을 blend 라이브러리에 저장한다."""
    if obj is None or obj.type != 'MESH' or not len(obj.data.polygons):
        raise ValueError('면이 있는 메시 오브젝트를 선택하세요')
    if kind not in ('HUMANOID', 'QUADRUPED'):
        raise ValueError('지원하지 않는 체형입니다')
    if obj.modifiers:
        raise ValueError('등록 전에 오브젝트의 modifier를 적용하거나 제거하세요')
    identifier = 'USER_' + uuid.uuid4().hex
    destination = template_root() / (identifier + '.blend')
    duplicate = obj.copy()
    duplicate.data = obj.data.copy()
    mesh = duplicate.data
    try:
        # 부모 의존성을 제거하면서 화면에 보이는 월드 변환을 보존한다.
        world = obj.matrix_world.copy()
        duplicate.parent = None
        duplicate.matrix_world = world
        duplicate.animation_data_clear()
        duplicate.constraints.clear()
        duplicate.modifiers.clear()
        bpy.data.libraries.write(str(destination), {duplicate}, fake_user=True)
        _save_entry(identifier, name, kind)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        bpy.data.objects.remove(duplicate, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    return identifier


def register_file(path, name, kind='HUMANOID'):
    """외부 blend의 단일 메시를 복사 등록하고 원본은 유지한다."""
    source = Path(path)
    if source.suffix.lower() != '.blend' or not source.is_file():
        raise ValueError('존재하는 .blend 파일을 선택하세요')
    before = set(bpy.data.objects)
    before_meshes = set(bpy.data.meshes)
    try:
        with bpy.data.libraries.load(str(source), link=False) as (data_from, data_to):
            data_to.objects = list(data_from.objects)
        meshes = [obj for obj in data_to.objects if obj is not None and obj.type == 'MESH']
        if len(meshes) != 1:
            raise ValueError('외부 파일에는 등록할 메시가 정확히 하나 있어야 합니다')
        return register_object(meshes[0], name, kind)
    finally:
        for obj in set(bpy.data.objects) - before:
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in set(bpy.data.meshes) - before_meshes:
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)


def load_template(template_id, collection):
    """새 독립 오브젝트를 컬렉션에 링크한다."""
    if template_id in _FILE_BUILTINS:
        # OBJ 임포트 — 쿼드가 그대로 남는다. 파일은 애드온과 함께 배포된다
        path = _TEMPLATE_FILE_DIR / _FILE_BUILTINS[template_id][0]
        if not path.is_file():
            raise ValueError('내장 템플릿 파일이 없습니다: ' + str(path))
        before = set(bpy.data.objects)
        with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
            bpy.ops.wm.obj_import(filepath=str(path), forward_axis='NEGATIVE_Z', up_axis='Y')
        new = [o for o in bpy.data.objects if o not in before and o.type == 'MESH']
        if len(new) != 1:
            for o in new:
                bpy.data.objects.remove(o, do_unlink=True)
            raise ValueError('내장 템플릿 OBJ에 메시가 정확히 하나여야 합니다: ' + str(path))
        obj = new[0]
        for c in list(obj.users_collection):
            c.objects.unlink(obj)
        # OBJ 임포터는 축 변환을 오브젝트 회전에 두기도 한다 — 메시 좌표만 다루기 위해
        # 오브젝트 변환은 항등으로 리셋한다(아래에서 메시 자체를 Z-up으로 맞춘다)
        from mathutils import Matrix
        obj.matrix_world = Matrix.Identity(4)
        # OBJ 왕복에서 축이 Y-up으로 남을 수 있다 — 정점 좌표로 직접 판정한다.
        # obj.dimensions는 임포트 직후 depsgraph 평가 전이라 값이 낡아 잘못 회전시켰다(실측).
        from mathutils import Matrix
        co = [v.co.copy() for v in obj.data.vertices]
        ext = [max(p[a] for p in co) - min(p[a] for p in co) for a in range(3)]
        if ext[1] > ext[2] and ext[1] > ext[0]:
            obj.data.transform(Matrix.Rotation(1.5707963, 4, 'X'))  # Y-up → Z-up
            obj.data.update()
        # 발바닥 z=0, 중심 X=Y=0
        co = [v.co.copy() for v in obj.data.vertices]
        shift = Matrix.Translation((-(min(p.x for p in co) + max(p.x for p in co)) / 2,
                                    -(min(p.y for p in co) + max(p.y for p in co)) / 2,
                                    -min(p.z for p in co)))
        obj.data.transform(shift)
        obj.data.update()
        # 정면은 -Y여야 한다(엔진 규약·셰이프와 동일). 머리 높이 정점 중 앞으로 가장 튀어나온
        # 점(코)이 +Y면 뒤를 보고 있는 것이다 — Z축 180도 회전
        from mathutils import Matrix
        zs = [v.co.z for v in obj.data.vertices]
        top = max(zs)
        head = [v.co for v in obj.data.vertices if v.co.z > top - (top - min(zs)) * 0.15]
        if head and max(head, key=lambda p: abs(p.y)).y > 0:
            obj.data.transform(Matrix.Rotation(3.14159265, 4, 'Z'))
            obj.data.update()
        obj.name = 'LP3D_' + template_id
        obj.data.name = obj.name
        obj.data.materials.clear()
    elif template_id in ('HUMANOID', 'QUADRUPED'):
        vertices, faces, groups = build_template(template_id)
        mesh = bpy.data.meshes.new('LP3D_' + template_id)
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(mesh.name, mesh)
        for name, indices in groups.items():
            obj.vertex_groups.new(name=name).add(indices, 1.0, 'REPLACE')
    else:
        if template_id not in _entries():
            raise ValueError('등록되지 않은 템플릿입니다: ' + str(template_id))
        with bpy.data.libraries.load(str(template_root() / (template_id + '.blend')), link=False) as (source, target):
            target.objects = list(source.objects)
        meshes = [obj for obj in target.objects if obj is not None and obj.type == 'MESH']
        if len(meshes) != 1:
            for obj in target.objects:
                if obj is not None:
                    bpy.data.objects.remove(obj, do_unlink=True)
            raise ValueError('저장된 템플릿 메시가 올바르지 않습니다')
        obj = meshes[0]
    collection.objects.link(obj)
    obj['lp3d_template_id'] = template_id
    return obj
