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

# ---------- 팔레트 상수 (영구 고정 — 변경 금지) ----------
GRID = 32          # 32x32 = 1024 셀
CELL_PX = 8        # 셀당 픽셀
SIZE = GRID * CELL_PX  # 256x256

NEUTRAL_ROWS = 2   # 하단 2행 = 무채색 램프
HUE_COUNT = 15     # 유채색: 색상 15개 x 2행(64칸)
CHROMA_STEPS = 4
L_STEPS = 16
L_MIN, L_MAX = 0.16, 0.97          # 유채색 명도 범위
NEUTRAL_L_MIN, NEUTRAL_L_MAX = 0.06, 0.99
# 채도는 절대값이 아니라 해당 (명도, 색상)에서 sRGB 안에 들어가는 최대 채도의 비율.
# 절대값을 쓰면 게멋 밖 조합이 클램핑되어 같은 색이 여러 칸에 중복 생성된다.
CHROMA_FRACTIONS = (0.16, 0.38, 0.64, 0.92)
# 무채색 램프 2종: (채도, 색상 각도) — 순수 그레이 / 따뜻한 그레이
NEUTRAL_TINTS = ((0.0, 0.0), (0.018, 70.0))

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

def build_cells():
    """1024개 셀의 RGB 정수 튜플 리스트를 만든다. 셀 0 = 좌하단, 행 우선."""
    cells = []
    # 하단 2행: 무채색 램프
    for row in range(NEUTRAL_ROWS):
        chroma, hue_deg = NEUTRAL_TINTS[row]
        hue = math.radians(hue_deg)
        for col in range(GRID):
            light = NEUTRAL_L_MIN + (NEUTRAL_L_MAX - NEUTRAL_L_MIN) * col / (GRID - 1)
            cells.append(_quantize(_srgb_of(light, min(chroma, _max_chroma(light, hue)), hue)))
    # 나머지 30행: 색상 15개 x (채도 4단 x 명도 16단)
    for index in range(HUE_COUNT):
        hue = 2 * math.pi * index / HUE_COUNT
        for fraction in CHROMA_FRACTIONS:
            for step in range(L_STEPS):
                light = L_MIN + (L_MAX - L_MIN) * step / (L_STEPS - 1)
                chroma = fraction * _max_chroma(light, hue)
                cells.append(_quantize(_srgb_of(light, chroma, hue)))
    return cells


# ---------- PNG 출력 ----------

def _chunk(tag: bytes, data: bytes) -> bytes:
    """PNG 청크 하나를 직렬화한다."""
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def render_png(cells) -> bytes:
    """셀 리스트를 256x256 8비트 트루컬러 PNG 바이트로 렌더링한다.

    셀 0이 좌하단이므로 PNG 행(위->아래)을 뒤집어 기록한다."""
    rows = []
    for py in range(SIZE):
        cell_y = GRID - 1 - (py // CELL_PX)
        line = bytearray([0])   # 필터 타입 0 (None) — 압축 결과를 결정적으로 유지
        for px in range(SIZE):
            line += bytes(cells[cell_y * GRID + (px // CELL_PX)])
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
        "# 셀 0 = 좌하단, 행 우선으로 증가한다.",
        "",
        "GRID = %d" % GRID,
        "CELL_PX = %d" % CELL_PX,
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
