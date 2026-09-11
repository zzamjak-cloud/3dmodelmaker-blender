#!/usr/bin/env python3
# 고정 팔레트 아티팩트 생성기 (개발 도구 — 애드온 패키지에는 포함되지 않는다)
#
# 실행: python scripts/gen_palette.py
# 출력: lowpoly/LP3D_Palette.png, lowpoly/palette_data.py
#
# 모든 색은 Oklab 좌표에서 공식으로 결정된다. 사람이 손으로 고르는 색은 없으므로
# 몇 번을 실행해도 바이트 단위로 동일한 결과가 나온다.
# 설계 근거: docs/superpowers/specs/2026-08-28-fixed-palette-design.md
import math
import os
import struct
import zlib

# ---------- 팔레트 상수 ----------
# 레이아웃: 열(좌→우) = 색상 다양성, 행(아래→위) = 명도. 한 색상은 채도 4단(선명→탁함)이
# 이웃한 4열 한 덩어리(군집)를 이루므로, UV를 옆으로 옮기면 채도/색상이, 위아래로
# 옮기면 밝기가 바뀐다. 텍스처 위쪽이 밝고 아래쪽이 어둡다.
SIZE = 256                 # 텍스처 한 변 (정사각 유지)
CELL_W, CELL_H = 4, 8      # 셀 픽셀 크기 (가로 4 x 세로 8)
COLS, ROWS = SIZE // CELL_W, SIZE // CELL_H   # 64열 x 32행 = 2048 셀

HUE_COUNT = 15             # 유채색 색상 수 (24도 간격)
HUE_START_DEG = 30.0       # 첫 색상(빨강)의 Oklab 색상각 — 좌측부터 빨강→주황→노랑→…→자홍
CHROMA_FRACTIONS = (0.92, 0.64, 0.38, 0.16)   # 군집 내 좌→우: 선명 → 탁함
L_MIN, L_MAX = 0.14, 0.97  # 유채색 명도 범위 (행 32단)
NEUTRAL_L_MIN, NEUTRAL_L_MAX = 0.06, 0.99
# 채도는 절대값이 아니라 해당 (명도, 색상)에서 sRGB 안에 들어가는 최대 채도의 비율.
# 절대값을 쓰면 게멋 밖 조합이 클램핑되어 같은 색이 여러 칸에 중복 생성된다.
# 무채색 4열: (채도, 색상 각도) — 순수 그레이 / 웜 그레이 / 세피아 / 쿨 그레이
NEUTRAL_TINTS = ((0.0, 0.0), (0.012, 70.0), (0.03, 75.0), (0.015, 250.0))
assert len(NEUTRAL_TINTS) + HUE_COUNT * len(CHROMA_FRACTIONS) == COLS

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PNG_PATH = os.path.join(_ROOT, "lowpoly", "LP3D_Palette.png")
_DATA_PATH = os.path.join(_ROOT, "lowpoly", "palette_data.py")


# ---------- 색 공간 변환 ----------

def _lin_to_srgb(c: float) -> float:
    """선형 RGB 성분을 sRGB 감마로 변환한다."""
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def _oklab_to_linear(light: float, a: float, b: float):
    """Oklab 좌표를 선형 RGB로 변환한다 (Björn Ottosson 계수)."""
    l_ = (light + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (light - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (light - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        +4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )


def _srgb_of(light: float, chroma: float, hue: float):
    """Oklch(명도, 채도, 색상 라디안)를 sRGB 0~1 3튜플로 변환한다."""
    a, b = chroma * math.cos(hue), chroma * math.sin(hue)
    return tuple(_lin_to_srgb(c) for c in _oklab_to_linear(light, a, b))


def _in_gamut(rgb, eps: float = 1e-4) -> bool:
    """sRGB 큐브 안에 들어오는지 판정한다."""
    return all(-eps <= c <= 1 + eps for c in rgb)


def _max_chroma(light: float, hue: float) -> float:
    """주어진 명도/색상에서 sRGB 안에 들어가는 최대 채도를 이분 탐색으로 찾는다."""
    low, high = 0.0, 0.45
    for _ in range(22):   # 22회면 1e-7 정밀도 — 8비트 양자화에 충분하다
        mid = (low + high) / 2
        if _in_gamut(_srgb_of(light, mid, hue)):
            low = mid
        else:
            high = mid
    return low


def _quantize(rgb):
    """sRGB 0~1을 0~255 정수로 양자화한다."""
    return tuple(min(255, max(0, round(c * 255))) for c in rgb)


# ---------- 셀 생성 ----------

def _column_colors(chroma_fn, hue):
    """한 열(명도 ROWS단, 아래=어두움)의 RGB 정수 튜플 목록. chroma_fn(light) → 채도."""
    out = []
    for row in range(ROWS):
        light = chroma_fn.l_min + (chroma_fn.l_max - chroma_fn.l_min) * row / (ROWS - 1)
        out.append(_quantize(_srgb_of(light, chroma_fn(light), hue)))
    return out


class _Chroma:
    """열의 채도 규칙: 절대 채도(무채색) 또는 최대 채도 비율(유채색)."""

    def __init__(self, l_min, l_max, hue, absolute=None, fraction=None):
        self.l_min, self.l_max, self.hue = l_min, l_max, hue
        self.absolute, self.fraction = absolute, fraction

    def __call__(self, light):
        limit = _max_chroma(light, self.hue)
        if self.fraction is not None:
            return self.fraction * limit
        return min(self.absolute, limit)


def build_columns():
    """열 순서대로 (열 색상 목록)을 만든다. 무채색 4열 → 색상별 채도 4단 군집."""
    columns = []
    for chroma, hue_deg in NEUTRAL_TINTS:
        hue = math.radians(hue_deg)
        columns.append(_column_colors(_Chroma(NEUTRAL_L_MIN, NEUTRAL_L_MAX, hue, absolute=chroma), hue))
    for index in range(HUE_COUNT):
        hue = math.radians(HUE_START_DEG + 360.0 * index / HUE_COUNT)
        for fraction in CHROMA_FRACTIONS:
            columns.append(_column_colors(_Chroma(L_MIN, L_MAX, hue, fraction=fraction), hue))
    return columns


def build_cells():
    """COLS*ROWS개 셀의 RGB 정수 튜플 리스트. 셀 0 = 좌하단, 행 우선(cell = row*COLS + col)."""
    columns = build_columns()
    return [columns[col][row] for row in range(ROWS) for col in range(COLS)]


# ---------- PNG 출력 ----------

def _chunk(tag: bytes, data: bytes) -> bytes:
    """PNG 청크 하나를 직렬화한다."""
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def render_png(cells) -> bytes:
    """셀 리스트를 SIZE x SIZE 8비트 트루컬러 PNG 바이트로 렌더링한다.

    셀 0이 좌하단이므로 PNG 행(위->아래)을 뒤집어 기록한다."""
    rows = []
    for py in range(SIZE):
        cell_y = ROWS - 1 - (py // CELL_H)
        line = bytearray([0])   # 필터 타입 0 (None) — 압축 결과를 결정적으로 유지
        for px in range(SIZE):
            line += bytes(cells[cell_y * COLS + (px // CELL_W)])
        rows.append(bytes(line))
    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + _chunk(b"IEND", b""))


# ---------- 데이터 모듈 출력 ----------

def render_data_module(cells) -> str:
    """palette_data.py 소스를 만든다."""
    lines = [
        "# 자동 생성 파일 — 직접 수정하지 말 것.",
        "# 재생성: python scripts/gen_palette.py",
        "#",
        "# 고정 팔레트 텍스처(LP3D_Palette.png)의 셀별 색상값.",
        "# 셀 0 = 좌하단, 행 우선(cell = row*COLS + col)으로 증가한다.",
        "# 열 = 색상(무채색 4열 + 색상 15개 x 채도 4단 군집), 행 = 명도(위가 밝음).",
        "",
        "COLS = %d" % COLS,
        "ROWS = %d" % ROWS,
        "CELL_W = %d" % CELL_W,
        "CELL_H = %d" % CELL_H,
        "SIZE = %d" % SIZE,
        "",
        "CELLS = [",
    ]
    for index in range(0, len(cells), 8):
        chunk = cells[index:index + 8]
        lines.append("    " + " ".join("(%d, %d, %d)," % c for c in chunk))
    lines.append("]")
    lines.append("")
    return "\n".join(lines)


def main():
    cells = build_cells()
    with open(_PNG_PATH, "wb") as handle:
        handle.write(render_png(cells))
    with open(_DATA_PATH, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_data_module(cells))
    print("생성 완료: %d셀 / 고유색 %d개" % (len(cells), len(set(cells))))
    print("  " + _PNG_PATH)
    print("  " + _DATA_PATH)


if __name__ == "__main__":
    main()
