# 6면도 레이아웃 규칙과 paint-over 프롬프트 (순수 파이썬 — bpy 의존 없음)
#
# contact sheet 합성, AI 결과 크롭, 프롬프트의 칸 배치 설명이 모두 이 정의 하나를
# 공유해야 베이크 때 모델 투영 좌표와 생성 이미지 좌표가 어긋나지 않는다.
# 이미지 좌표는 Blender Image.pixels와 같은 좌하단 원점이다.
from dataclasses import dataclass

VIEWS = ("FRONT", "RIGHT", "BACK", "LEFT", "TOP", "BOTTOM")
COLUMNS, ROWS = 3, 2
ASPECT_RATIO = "3:2"
# gpt-image 계열이 지원하는 3:2 캔버스 — 셀당 512px
SHEET_WIDTH, SHEET_HEIGHT = 1536, 1024

_VIEW_LABELS = {
    "FRONT": "FRONT", "RIGHT": "RIGHT SIDE", "BACK": "BACK",
    "LEFT": "LEFT SIDE", "TOP": "TOP", "BOTTOM": "BOTTOM",
}
_VIEW_DESCRIPTIONS = {
    "RIGHT": "RIGHT SIDE는 모델의 오른쪽 측면에서 본 모습입니다.",
    "LEFT": "LEFT SIDE는 모델의 왼쪽 측면에서 본 모습이며 RIGHT SIDE의 단순 좌우 반전이 아니라 실제 왼쪽 면입니다.",
    "TOP": "TOP은 모델 바로 위에서 내려다본 모습이며 정면(FRONT)이 아래쪽, 오른쪽(RIGHT)이 오른쪽에 옵니다.",
    "BOTTOM": "BOTTOM은 모델 바로 아래에서 올려다본 모습이며 정면(FRONT)이 위쪽, 오른쪽(RIGHT)이 오른쪽에 옵니다.",
}


@dataclass(frozen=True)
class SheetLayout:
    """캔버스를 같은 크기의 정사각 셀로 나눠 시점을 배치한다. 첫 행이 캔버스 위쪽."""
    views: tuple = VIEWS
    columns: int = COLUMNS
    rows: int = ROWS

    @property
    def cell_count(self) -> int:
        return self.columns * self.rows

    def cell_bounds(self, width: int, height: int, index: int):
        """좌하단 원점 좌표에서 index번째 셀의 (left, bottom, right, top)."""
        if not 0 <= index < self.cell_count:
            raise ValueError(f"셀 인덱스가 범위를 벗어났습니다: {index}")
        cell_w, cell_h = width // self.columns, height // self.rows
        if cell_w < 1 or cell_h < 1:
            raise ValueError("이미지가 너무 작아 셀로 나눌 수 없습니다")
        column = index % self.columns
        row_from_bottom = self.rows - 1 - index // self.columns
        left, bottom = column * cell_w, row_from_bottom * cell_h
        return left, bottom, left + cell_w, bottom + cell_h

    def canvas_size(self, capture_size: int):
        """정사각 캡처를 격자로 놓은 캔버스 크기 (3x2·3:2는 여백 없이 정확히 맞는다)."""
        if capture_size <= 0:
            raise ValueError("캡처 크기는 0보다 커야 합니다")
        return capture_size * self.columns, capture_size * self.rows

    def cell_origin(self, width: int, height: int, index: int, content: int):
        """index 셀 안에 content 크기 정사각형을 가운데 놓을 때의 좌하단 원점."""
        left, bottom, right, top = self.cell_bounds(width, height, index)
        if content > right - left or content > top - bottom:
            raise ValueError("셀보다 큰 내용은 가운데 배치할 수 없습니다")
        return left + (right - left - content) // 2, bottom + (top - bottom - content) // 2


LAYOUT = SheetLayout()


def shape_contract() -> str:
    """다른 모든 지시보다 우선하는 형상 계약.

    자세 부위를 열거하면 이미지 모델이 그 동작을 새로 만들어 내므로
    실루엣 밖에 아무것도 그리지 말라는 조건만 둔다."""
    return (
        "형상 계약(다른 모든 지시보다 우선):\n"
        "- 이 작업은 새 그림을 그리는 것이 아니라 첨부 이미지 위에 픽셀 단위로 정렬된 채색(paint-over)이다. "
        "실루엣 변경 금지. 물체를 옮기거나 키우거나 줄이거나 회전하지 않는다.\n"
        "- 첨부 이미지는 흰 배경 위에 기본색만 칠해진 3D 로우폴리 모델이다. "
        "그 실루엣 안쪽을 채색하는 작업이며, 실루엣 밖에는 아무것도 그리지 않는다.\n"
        "- 실루엣, 비율, 크기, 화면 안 위치를 그대로 유지한다. 이 형상은 텍스처를 입힐 실제 3D 모델이므로 한 픽셀도 재해석하지 않는다.\n"
        "- 첨부 이미지에 없는 부품은 추가하지 않고, 있는 부품을 빼지도 않는다.\n"
        "- 첨부 이미지의 기본 색 배치(어느 부위가 어떤 색인지)를 유지하고, 그 위에 재질 디테일만 더한다."
    )


def layout_contract(layout: SheetLayout = LAYOUT) -> str:
    """캔버스 분할과 시점 순서 지시."""
    row_labels = []
    for row in range(layout.rows):
        views = layout.views[row * layout.columns:(row + 1) * layout.columns]
        row_labels.append(" | ".join(_VIEW_LABELS[v] for v in views))
    rows_text = " / 아랫줄은 ".join(row_labels)
    descriptions = " ".join(_VIEW_DESCRIPTIONS[v] for v in layout.views if v in _VIEW_DESCRIPTIONS)
    return (
        f"- 하나의 {ASPECT_RATIO} 캔버스({SHEET_WIDTH}x{SHEET_HEIGHT})를 같은 너비의 {layout.columns}열과 "
        f"같은 높이의 {layout.rows}행, 총 {layout.cell_count}칸으로 나눈다. "
        "첨부 이미지가 정확히 같은 배치이므로 그 레이아웃을 그대로 따른다.\n"
        f"- 윗줄은 왼쪽부터 {rows_text} 순서이며, 모든 칸에 동일한 물체, 동일한 축척, 동일한 중심을 배치한다.\n"
        f"- {descriptions}\n"
        "- 각 칸에서 물체의 외곽선 위치, 크기, 중심을 첨부 이미지와 최대한 일치시킨다. "
        "부위 경계도 첨부 실루엣의 같은 위치에 맞춘다.\n"
        "- 모든 시점의 색, 무늬, 마모, 부품 연결은 서로 연속되고 일관되어야 한다. "
        "같은 부위는 어느 시점에서 보아도 같은 색과 명도로 칠한다."
    )


def output_rules() -> str:
    return (
        "- 원근 없는 orthographic view로 표현하고 물체가 잘리지 않게 여백을 유지한다.\n"
        "- 조명 렌더가 아니라 diffuse/albedo에 옮길 수 있는 손으로 그린 색과 명암을 표현한다. "
        "강한 그림자, 하이라이트, 반사는 넣지 않는다.\n"
        "- 배경은 완전히 균일한 흰색(#FFFFFF)으로 둔다.\n"
        "- 텍스트, 라벨, 구분선, 숫자, 로고, 워터마크, 받침대, 그림자는 넣지 않는다."
    )


def _body(request: str) -> str:
    """백엔드와 무관한 paint-over 지시 본문 — codex/OpenRouter 양쪽이 공유한다."""
    return (
        f"대상: {request}\n"
        "목적: 캐주얼 게임용 로우폴리 3D 모델의 손맵(hand-painted) diffuse 텍스처 소스가 되는 6시점도. "
        "첨부 이미지는 이 모델을 FRONT/RIGHT/BACK/LEFT/TOP/BOTTOM 6시점에서 직교 렌더한 것이다.\n\n"
        f"{shape_contract()}\n\n"
        "스타일: 캐주얼 게임 손맵 텍스처. 기본색은 유지하면서 나무결, 금속 마모, 돌 이음새, 천 주름, "
        "때·긁힘 같은 재질 디테일과 부드러운 명암 변화를 더한다. 외곽선은 넣지 않는다.\n\n"
        "출력 계약:\n"
        "- 최종 이미지는 정확히 한 장만 생성한다.\n"
        f"{layout_contract()}\n"
        f"{output_rules()}"
    )


def build_prompt(request: str, filename: str) -> str:
    """codex image_gen용 paint-over 프롬프트 — 첨부한 6면도 가이드 위에 손맵 디테일을 입힌다."""
    return (
        "image_gen 도구를 사용해 첨부한 이미지를 입력(참조)으로 삼아 편집한 이미지 1장을 생성하고, "
        f"반드시 현재 디렉토리에 {filename} 파일로 저장하라. "
        f"크기는 {SHEET_WIDTH}x{SHEET_HEIGHT}({ASPECT_RATIO})로 한다.\n"
        + _body(request) + "\n"
        "저장 완료 후 텍스트로는 SAVED 한 단어만 답하라."
    )


def build_image_prompt(request: str) -> str:
    """OpenRouter Image API용 — 가이드 시트는 input_references로 따로 넘어가므로
    파일 저장·도구 호출 지시가 필요 없다."""
    return (
        f"첨부한 참조 이미지를 그대로 덮어 칠한(paint-over) {ASPECT_RATIO} 이미지 1장을 생성하라.\n"
        + _body(request)
    )
