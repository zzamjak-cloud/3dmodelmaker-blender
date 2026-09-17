# 애드온 환경설정: CLI 경로, 타임아웃, 에셋 라이브러리
import os
import shutil

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       StringProperty)

from .core import imagegen

# macOS Finder로 실행한 Blender는 사용자 PATH를 상속하지 않으므로 흔한 설치 경로를 직접 탐색
_EXTRA_PATHS = (
    "~/.local/bin",
    "~/.npm-global/bin",
    "~/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
)


def find_cli(name: str) -> str:
    """PATH + 흔한 설치 경로에서 CLI 실행 파일 절대경로를 찾는다. 없으면 빈 문자열."""
    found = shutil.which(name)
    if found:
        return found
    for base in _EXTRA_PATHS:
        candidate = os.path.join(os.path.expanduser(base), name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return ""


def _persist_cb(self, context):
    # 변경 즉시 JSON에 저장 — 애드온 업데이트/스키마 변경에도 설정 유지
    from .core import persist
    persist.on_prefs_changed(self)


class LP3DPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    codex_path: StringProperty(
        name="Codex CLI 경로",
        description="codex 실행 파일 절대경로 (비우면 자동 탐지)",
        subtype='FILE_PATH',
        default="",
        update=_persist_cb,
    )
    timeout: IntProperty(
        name="CLI 타임아웃(초)",
        description="에이전트 호출 1회당 최대 대기 시간",
        default=300, min=30, max=1800,
        update=_persist_cb,
    )
    ai_concurrency: IntProperty(
        name="동시 AI 실행 수",
        description=("동시에 실행할 AI CLI 개수 — 큐에 쌓인 여러 프롬프트의 AI 호출이 "
                     "이만큼 병렬로 진행된다. Blender 작업은 이 값과 무관하게 항상 "
                     "하나씩 순차 실행된다. 1로 두면 예전처럼 완전 순차 동작"),
        default=3, min=1, max=8,
        update=_persist_cb,
    )
    use_multiview: BoolProperty(
        name="멀티뷰 참조 생성",
        description="생성 시작 시 정면/측면/상면/쿼터뷰 참조 시트를 먼저 만들어 모델링 기준으로 사용 (생성 백엔드는 아래 '참조 이미지 생성'에서 고른다)",
        default=True,
        update=_persist_cb,
    )
    image_backend: EnumProperty(
        name="이미지 생성 백엔드",
        description=("참조 시트(멀티뷰·씬 컨셉·텍스처 6면도)를 무엇으로 만들지 — "
                     "OpenRouter는 이미지 1장에 HTTP 1회만 쓰므로 Codex 세션보다 "
                     "토큰 소모가 훨씬 적고 모델을 고를 수 있다. Astra는 어느 쪽이든 "
                     "모델링 턴에만 쓰인다"),
        items=[
            ('OPENROUTER', "OpenRouter API", "OpenRouter Image API로 직접 생성 (API 키 필요)"),
            ('CODEX', "Codex CLI", "codex CLI의 image_gen 도구로 생성 (API 키 불필요)"),
        ],
        default='OPENROUTER',
        update=_persist_cb,
    )
    openrouter_api_key: StringProperty(
        name="OpenRouter API 키",
        description=("openrouter.ai/settings/keys 에서 발급한 키. "
                     "비워두면 OPENROUTER_API_KEY 환경변수를 쓰고, 그것도 없으면 "
                     "Codex CLI 경로로 자동 폴백한다"),
        subtype='PASSWORD',
        default="",
        update=_persist_cb,
    )
    image_model: EnumProperty(
        name="이미지 모델",
        description="참조 시트를 생성할 OpenRouter 이미지 모델",
        items=imagegen.enum_items(),
        default=imagegen.DEFAULT_MODEL,
        update=_persist_cb,
    )
    image_quality: EnumProperty(
        name="이미지 품질",
        description=("gpt-image(덕테이프) 계열 전용 품질 티어 — 높을수록 느리고 비싸다. "
                     "나노바나나 계열은 이 값을 무시한다"),
        items=[
            ('low', "낮음", "빠르고 저렴"),
            ('medium', "보통", ""),
            ('high', "높음", "기본 — 시트 판독성과 비용의 균형"),
            ('xhigh', "매우 높음", "2.5 계열 전용"),
            ('max', "최대", "2.5 계열 전용 — 가장 느리고 비싸다"),
        ],
        default='high',
        update=_persist_cb,
    )
    use_shapegen: BoolProperty(
        name="캐릭터 이미지→3D 셰이프 생성",
        description=("캐릭터를 코드로 조립하는 대신, 턴어라운드 시트의 정면·뒷면·측면을 로컬 "
                     "셰이프 서버(로컬 또는 자기 Modal 계정)에 넣어 하이폴리 셰이프를 받고 리토폴로지한다. 서버가 없으면 "
                     "자동으로 코드 모델링 경로로 폴백"),
        default=True,
        update=_persist_cb,
    )
    shapegen_url: StringProperty(
        name="셰이프 서버 주소",
        description=("셰이프 생성 서버. 로컬(http://127.0.0.1:8081) 또는 자기 Modal 계정에 배포한 주소"
                     "(https://…modal.run). 기본값은 로컬 — 클라우드 주소는 각자 자기 것을 넣는다"),
        default="http://127.0.0.1:8081",
        update=_persist_cb,
    )
    shapegen_token: StringProperty(
        name="셰이프 서버 토큰",
        description=("클라우드 서버 인증 토큰. Modal 프록시 인증은 `키:시크릿` 형식(Modal-Key/Modal-Secret 헤더), "
                     "그 외는 Bearer 토큰으로 보낸다. 로컬 서버면 비워둔다. 이 값은 저장소에 절대 넣지 말 것"),
        default="", subtype='PASSWORD',
        update=_persist_cb,
    )
    shapegen_faces: IntProperty(
        name="리토폴로지 목표 면수",
        description="이미지→3D 셰이프를 게임용으로 줄일 때의 목표 폴리곤(쿼드) 수. 트라이는 약 2배",
        default=12000, min=2000, max=100000,
        update=_persist_cb,
    )
    shapegen_method: EnumProperty(
        name="리토폴로지 방식",
        description="이미지→3D 셰이프를 게임용 메시로 줄이는 방법",
        items=[
            ('TEMPLATE', "베이스 메시 템플릿", "몸체에 얼굴·관절 루프를 가진 템플릿을 적합. 극단적인 비율은 수동 보정 필요"),
            ('QUADRIFLOW', "QuadriFlow 쿼드", "복셀 리메시 → QuadriFlow → 하이폴리 슈링크랩 — 균일 쿼드 메시"),
            ('DECIMATE', "데시메이트 (트라이)", "조각 제거 후 데시메이트 — 빠르지만 삼각형 그대로라 수정이 어렵다"),
        ],
        default='QUADRIFLOW',
        update=_persist_cb,
    )
    character_height: FloatProperty(
        name="캐릭터 기본 키(m)",
        description="이미지→3D 셰이프의 크기 기준. 발바닥 z=0에서 머리끝까지",
        default=1.8, min=0.2, max=10.0,
        update=_persist_cb,
    )
    character_compare_turns: IntProperty(
        name="캐릭터 6면도 대조 횟수",
        description=("캐릭터 모델을 실행한 뒤 시트와 같은 6시점으로 렌더해 턴어라운드 시트와 "
                     "대조하고 차이를 고치는 추가 턴 수. 0이면 대조 없이 한 번에 마무리. "
                     "1회당 Astra 호출 1번이 늘어난다"),
        default=1, min=0, max=3,
        update=_persist_cb,
    )
    use_library: BoolProperty(
        name="생성 라이브러리 사용",
        description="성공한 생성 결과(프롬프트·코드)를 쌓아두고, 비슷한 요청이 오면 과거 합격 코드를 예시로 참고해 품질을 높인다",
        default=True,
        update=_persist_cb,
    )
    texture_resolution: EnumProperty(
        name="개별 매핑 텍스처 크기",
        description="개별 매핑 타입의 베이크 텍스처 한 변 픽셀 수 — 클수록 CPU 베이크가 오래 걸린다",
        items=[
            ('512', "512", "빠름 — 소품용"),
            ('1024', "1024", "기본 — 품질과 베이크 시간의 균형"),
            ('2048', "2048", "느림 — 큰 건물용"),
        ],
        default='1024',
        update=_persist_cb,
    )
    texture_per_view: BoolProperty(
        name="텍스처 시점별 고해상 생성",
        description=("개별 매핑의 AI 채색을 시트 한 장(칸당 512px) 대신 시점마다 1:1 이미지로 "
                     "6번 요청한다(칸당 1024px). 4배 선명하지만 이미지 요청이 6회다. "
                     "캐릭터 모드는 이 설정과 무관하게 항상 시점별로 받는다"),
        default=False,
        update=_persist_cb,
    )
    scene_tri_budget: IntProperty(
        name="배경 씬 트라이 상한",
        description=("배경 공간 하나가 쓸 수 있는 전체 삼각형 수. **0이면 상한 없음(기본)** — "
                     "스타일의 트라이 상한은 모델 1개 기준이라 에셋이 여러 종 들어가는 배경에 "
                     "그대로 씌우면 밀도를 만들 수 없다. 특정 기기 한도에 맞춰야 할 때만 값을 넣는다"),
        default=0, min=0, max=2000000,
        update=_persist_cb,
    )
    scene_max_assets: IntProperty(
        name="배경 에셋 종류 상한",
        description=("배경 플랜이 요청할 수 있는 고유 에셋 종류 수. **0이면 씬 규모에 따라 자동** "
                     "(실내 10 / 구역 16 / 대규모 24). 많을수록 키트 생성 시간이 길어진다"),
        default=0, min=0, max=40,
        update=_persist_cb,
    )
    scene_timeout_scale: FloatProperty(
        name="배경 턴 타임아웃 배수",
        description="배경 모드의 플랜·배치 턴은 오브젝트 한 개보다 오래 걸린다 — CLI 타임아웃에 이 배수를 곱한다",
        default=2.0, min=1.0, max=5.0,
        update=_persist_cb,
    )
    asset_library_path: StringProperty(
        name="에셋 라이브러리 경로",
        description="Asset Browser 라이브러리 루트 (카탈로그 파일 위치)",
        subtype='DIR_PATH',
        default="",
        update=_persist_cb,
    )

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "codex_path")
        col.prop(self, "timeout")
        col.prop(self, "ai_concurrency")
        col.prop(self, "use_multiview")
        col.prop(self, "use_library")
        col.prop(self, "texture_resolution")
        col.prop(self, "texture_per_view")
        char_box = self.layout.box()
        char_box.label(text="캐릭터", icon='ARMATURE_DATA')
        char_box.prop(self, "use_shapegen")
        if self.use_shapegen:
            char_box.prop(self, "shapegen_url")
            char_box.prop(self, "shapegen_token")
            char_box.prop(self, "shapegen_faces")
            char_box.prop(self, "shapegen_method")
            char_box.prop(self, "character_height")
        char_box.prop(self, "character_compare_turns")
        img_box = self.layout.box()
        img_box.label(text="참조 이미지 생성", icon='IMAGE_DATA')
        img_box.prop(self, "image_backend")
        if self.image_backend == 'OPENROUTER':
            img_box.prop(self, "openrouter_api_key")
            img_box.prop(self, "image_model")
            if imagegen.model_def(self.image_model)["qualities"]:
                img_box.prop(self, "image_quality")
            if not imagegen.api_key():
                warn = img_box.column()
                warn.alert = True
                warn.label(text="키가 없어 Codex CLI로 폴백합니다", icon='ERROR')
        scene_box = self.layout.box()
        scene_box.label(text="배경 공간", icon='WORLD')
        scene_box.prop(self, "scene_tri_budget")
        scene_box.prop(self, "scene_max_assets")
        scene_box.prop(self, "scene_timeout_scale")
        from .core import library
        try:
            info = library.stats()
            col.label(text=f"라이브러리: {info['count']}개 축적 ({info['rated']}개 평가됨)", icon='ASSET_MANAGER')
        except Exception:
            pass
        col.prop(self, "asset_library_path")


class _Defaults:
    """애드온으로 활성화되지 않은 상태(테스트 등)에서 쓰는 기본값."""
    codex_path = ""
    timeout = 300
    ai_concurrency = 3
    use_multiview = True
    use_library = True
    asset_library_path = ""
    texture_resolution = '1024'
    texture_per_view = False
    image_backend = 'OPENROUTER'
    openrouter_api_key = ""
    image_model = imagegen.DEFAULT_MODEL
    image_quality = 'high'
    character_compare_turns = 1
    use_shapegen = True
    shapegen_url = "http://127.0.0.1:8081"
    shapegen_token = ""
    shapegen_faces = 12000
    shapegen_method = 'QUADRIFLOW'
    character_height = 1.8
    scene_tri_budget = 0
    scene_max_assets = 0
    scene_timeout_scale = 2.0


_DEFAULTS = _Defaults()


def get_prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else _DEFAULTS


def resolve_cli_path(agent: str = 'CODEX') -> str:
    """설정값을 우선하고 없으면 Codex CLI를 자동 탐지한다."""
    prefs = get_prefs()
    return prefs.codex_path or find_cli("codex")


def _restore_deferred():
    # 애드온 활성화가 끝난 뒤 저장된 설정을 복원한다
    addon = bpy.context.preferences.addons.get(__package__)
    if addon:
        from .core import persist
        persist.apply_prefs(addon.preferences)
    return None


def register():
    bpy.utils.register_class(LP3DPreferences)
    bpy.app.timers.register(_restore_deferred, first_interval=0.2)


def unregister():
    bpy.utils.unregister_class(LP3DPreferences)
