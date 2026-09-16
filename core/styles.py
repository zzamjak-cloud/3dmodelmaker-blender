# 아트 스타일 카탈로그 (bpy 무의존 순수 모듈)
#
# 예전에는 "캐주얼 로우폴리"가 system_lowpoly.md와 이미지 시트 프롬프트에 하드코딩돼
# 있었다. 스타일을 바꾸려면 프롬프트 파일을 고쳐야 했고, 큐 항목마다 다른 스타일을
# 쓸 수도 없었다. 스타일을 데이터로 빼면서 프롬프트는 공통 골격(system_base.md) +
# 스타일 조각(prompts/styles/*.md)으로 나뉘었다.
#
# `image_note`는 참조 시트(멀티뷰·씬 컨셉)의 스타일 한 줄이다. 모델링 지침과 시트가
# 따로 놀면 시트를 보고 만든 모델이 시트와 다른 스타일이 된다 — 한 곳에서 같이 정한다.
# `tri_budget`은 배경 모드의 에셋 트라이 상한 배수다 (로우폴리 기준 1.0).
import os

_PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "prompts")
_STYLE_DIR = os.path.join(_PROMPT_DIR, "styles")

STYLES = (
    {
        "id": "LOWPOLY",
        "label": "로우폴리 캐주얼",
        "desc": "각진 면과 플랫 셰이딩, 채도 높은 색 — 캐주얼 모바일 게임 기본",
        "file": "lowpoly.md",
        "tri_scale": 1.0,
        "image_note": "스타일: 로우폴리 게임 에셋, 플랫 셰이딩, 각진 면, 채도 높은 단순한 색 팔레트, 흰 배경.\n",
    },
    {
        "id": "VOXEL",
        "label": "복셀 (로블록스형)",
        "desc": "같은 크기의 정육면체 격자로만 구성 — 곡선·경사 없음",
        "file": "voxel.md",
        "tri_scale": 1.2,
        "image_note": ("스타일: 복셀(voxel) 게임 에셋. 모든 형태가 같은 크기의 정육면체 블록으로만 "
                       "이루어지고 곡선·경사면이 없다. 계단식 윤곽, 블록 단위 단색, 흰 배경.\n"),
    },
    {
        "id": "CUTE",
        "label": "귀여운 둥근",
        "desc": "모서리 없이 부푼 덩어리, 상단이 큰 역삼각 비율, 밝은 파스텔",
        "file": "cute.md",
        "tri_scale": 2.0,
        "image_note": ("스타일: 귀엽고 둥근 캐주얼 게임 에셋. 모서리가 둥글고 덩어리가 부풀어 있으며 "
                       "위쪽 덩어리가 아래보다 크다. 밝은 파스텔 색, 부드러운 명암, 흰 배경.\n"),
    },
    {
        "id": "STYLIZED",
        "label": "스타일리쉬 캐주얼",
        "desc": "기울고 휜 실루엣, 극단적 상하 대비, 표면 디테일 다수",
        "file": "stylized.md",
        "tri_scale": 2.5,
        "image_note": ("스타일: 과장된 스타일라이즈드 캐주얼 게임 에셋(Clash of Clans 계열). "
                       "실루엣이 기울거나 휘어 있고 위쪽 덩어리가 크게 과장됐다. 채도 높은 색과 "
                       "강한 명도 대비, 표면 디테일이 많다. 흰 배경.\n"),
    },
    {
        "id": "REALISTIC",
        "label": "사실적 (미드폴리)",
        "desc": "실제 비율·실제 치수, 낮은 채도 — 과장 없음",
        "file": "realistic.md",
        "tri_scale": 6.0,
        "image_note": ("스타일: 사실적인 게임 에셋. 실제 비율과 실제 치수를 지키고 과장이 없다. "
                       "낮은 채도의 현실적인 재질 색, 부드러운 곡률, 흰 배경.\n"),
    },
)

DEFAULT_STYLE = "LOWPOLY"

_BY_ID = {s["id"]: s for s in STYLES}


def enum_items():
    """EnumProperty items — 큐 항목 드롭다운용."""
    return [(s["id"], s["label"], s["desc"]) for s in STYLES]


def style_def(style_id) -> dict:
    """스타일 정의. 모르는 id면 기본 스타일로 떨어진다 (구버전 .blend 보호)."""
    return _BY_ID.get(str(style_id or "").strip().upper(), _BY_ID[DEFAULT_STYLE])


def image_note(style_id) -> str:
    """참조 시트 프롬프트에 넣을 스타일 한 줄."""
    return style_def(style_id)["image_note"]


def tri_scale(style_id) -> float:
    """트라이 예산 배수 — 스타일마다 형태에 필요한 밀도가 다르다."""
    return float(style_def(style_id)["tri_scale"])


def guide(style_id) -> str:
    """스타일 지침 본문 (prompts/styles/*.md). 파일이 없으면 빈 문자열."""
    path = os.path.join(_STYLE_DIR, style_def(style_id)["file"])
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def uses_voxel_api(style_id) -> bool:
    """복셀 격자 헬퍼를 프롬프트 어휘에 노출할 스타일인가.

    복셀 외 스타일에 voxel 헬퍼를 보여주면 쓸 일이 없는 어휘만 늘어나고,
    반대로 복셀에 노출하지 않으면 스타일 지침이 요구하는 함수가 존재하지 않는다."""
    return style_def(style_id)["id"] == "VOXEL"
