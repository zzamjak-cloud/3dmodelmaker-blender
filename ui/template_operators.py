"""베이스 메시 선택·편집용 로드·사용자 등록 UI."""
from pathlib import Path

import bpy
from bpy.props import EnumProperty, StringProperty

from ..core import templates

_ITEMS = []
_KINDS = [('HUMANOID', '인간형', '두 발 A-포즈'),
          ('QUADRUPED', '네발형', '네 발 중립 자세')]


def _items(self, context):
    """동적 enum 문자열의 수명을 유지한다."""
    rows = [('AUTO', '유형에 맞춰 자동', '동물형은 네발형, 나머지는 인간형')]
    rows.extend(templates.enum_items(context))
    if rows != _ITEMS:
        _ITEMS[:] = rows
    return _ITEMS


def _job(context):
    """현재 선택된 캐릭터 작업을 반환한다."""
    props = getattr(context.scene, 'lp3d', None)
    return props.active_job() if props else None


def selected_id(job):
    """자동 선택은 명시된 동물형에서만 네발형으로 해석한다."""
    value = getattr(job, 'character_template', 'AUTO')
    if value == 'AUTO':
        return 'QUADRUPED' if getattr(job, 'character_type', '') == 'ANIMAL' else 'HUMANOID'
    return value


def selected_label(job):
    """삭제되거나 이동한 사용자 템플릿도 UI에서 드러낸다."""
    value = getattr(job, 'character_template', 'AUTO')
    if value == 'AUTO':
        return '자동 (동물형: 네발형)'
    return next((row[1] for row in templates.enum_items() if row[0] == value), '등록 파일 없음')


class LP3D_OT_template_choose(bpy.types.Operator):
    """작업에 저장할 템플릿 식별자를 고른다."""
    bl_idname = 'lp3d.template_choose'
    bl_label = '베이스 메시 선택'
    template_id: EnumProperty(items=_items)

    def execute(self, context):
        job = _job(context)
        if job is None or job.state == 'RUNNING':
            return {'CANCELLED'}
        job.character_template = self.template_id
        return {'FINISHED'}


class LP3D_OT_template_load(bpy.types.Operator):
    """선택한 템플릿의 복사본을 편집용 컬렉션에 불러온다."""
    bl_idname = 'lp3d.template_load'
    bl_label = '템플릿 불러오기'
    bl_description = '편집용 복사본을 불러옵니다. Edit Mode에서 수정한 뒤 선택 메시 등록을 누르세요'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and _job(context) is not None

    def execute(self, context):
        collection = bpy.data.collections.get('LP3D_Template_Edit')
        if collection is None:
            collection = bpy.data.collections.new('LP3D_Template_Edit')
            context.scene.collection.children.link(collection)
        try:
            obj = templates.load_template(selected_id(_job(context)), collection)
        except Exception as exc:
            self.report({'ERROR'}, f'템플릿 로드 실패: {exc}')
            return {'CANCELLED'}
        for selected in context.selected_objects:
            selected.select_set(False)
        obj.hide_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        self.report({'INFO'}, 'Edit Mode에서 수정 후 Object Mode로 돌아와 선택 메시 등록을 누르세요')
        return {'FINISHED'}


class LP3D_OT_template_register(bpy.types.Operator):
    """선택 메시를 독립 템플릿으로 저장하고 현재 작업에서 선택한다."""
    bl_idname = 'lp3d.template_register'
    bl_label = '선택 메시 등록'
    bl_description = '선택 메시를 새 사용자 템플릿으로 저장합니다. 기존 템플릿은 보존됩니다'
    template_name: StringProperty(name='이름', default='사용자 베이스 메시')
    kind: EnumProperty(name='체형', items=_KINDS)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return context.mode == 'OBJECT' and obj is not None and obj.type == 'MESH'

    def invoke(self, context, event):
        self.template_name = context.active_object.name
        self.kind = 'QUADRUPED' if selected_id(_job(context)) == 'QUADRUPED' else 'HUMANOID'
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        try:
            value = templates.register_object(context.active_object, self.template_name, self.kind)
        except Exception as exc:
            self.report({'ERROR'}, f'템플릿 등록 실패: {exc}')
            return {'CANCELLED'}
        job = _job(context)
        if job is not None and job.state != 'RUNNING':
            job.character_template = value
        self.report({'INFO'}, f'사용자 템플릿 등록: {self.template_name}')
        return {'FINISHED'}


class LP3D_OT_template_import(bpy.types.Operator):
    """외부 blend의 메시를 사용자 템플릿 저장소로 등록한다."""
    bl_idname = 'lp3d.template_import'
    bl_label = '.blend 템플릿 등록'
    filepath: StringProperty(subtype='FILE_PATH')
    filter_glob: StringProperty(default='*.blend', options={'HIDDEN'})
    template_name: StringProperty(name='이름', default='')
    kind: EnumProperty(name='체형', items=_KINDS)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        name = self.template_name.strip() or Path(self.filepath).stem
        try:
            value = templates.register_file(bpy.path.abspath(self.filepath), name, self.kind)
        except Exception as exc:
            self.report({'ERROR'}, f'템플릿 파일 등록 실패: {exc}')
            return {'CANCELLED'}
        job = _job(context)
        if job is not None and job.state != 'RUNNING':
            job.character_template = value
        self.report({'INFO'}, f'사용자 템플릿 등록: {name}')
        return {'FINISHED'}


_CLASSES = (LP3D_OT_template_choose, LP3D_OT_template_load,
            LP3D_OT_template_register, LP3D_OT_template_import)


def register():
    """템플릿 조작 연산자를 등록한다."""
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    """템플릿 조작 연산자를 역순으로 해제한다."""
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
