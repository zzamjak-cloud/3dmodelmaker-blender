# 색 스냅: 임의 RGB를 고정 팔레트의 가장 가까운 셀로 매핑한다.
#
# 이 모듈은 bpy에 의존하지 않는 순수 함수만 담는다 (Blender 없이 테스트 가능).
# 거리 계산에 Oklab을 쓰는 이유: 단순 RGB 유클리드 거리는 지각적으로 균등하지
# 않아 어두운 색끼리 엉뚱하게 붙는다.
import math

try:                      # 애드온으로 로드될 때
    from .palette_data import CELLS, GRID
except ImportError:       # 파일 단위로 직접 로드될 때 (테스트)
    from palette_data import CELLS, GRID


def _srgb_to_linear(value: float) -> float:
    """sRGB 감마 성분을 선형 RGB로 변환한다."""
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def srgb_to_oklab(rgb):
    """sRGB 0~1 3튜플을 Oklab 좌표로 변환한다 (Björn Ottosson 계수)."""
    red, green, blue = (_srgb_to_linear(c) for c in rgb[:3])
    long_ = 0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue
    medium = 0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue
    short = 0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue
    long_, medium, short = (_cbrt(long_), _cbrt(medium), _cbrt(short))
    return (
        0.2104542553 * long_ + 0.7936177850 * medium - 0.0040720468 * short,
        1.9779984951 * long_ - 2.4285922050 * medium + 0.4505937099 * short,
        0.0259040371 * long_ + 0.7827717662 * medium - 0.8086757660 * short,
    )


def _cbrt(value: float) -> float:
    """음수도 처리하는 세제곱근."""
    return value ** (1 / 3) if value >= 0 else -((-value) ** (1 / 3))


# 팔레트 1024색의 Oklab 좌표를 임포트 시 한 번만 계산해 캐시한다
_CELL_LAB = tuple(srgb_to_oklab(tuple(c / 255 for c in rgb)) for rgb in CELLS)


def snap_cell(color) -> int:
    """0~1 범위 RGB를 받아 Oklab 최근접 셀 인덱스(0~1023)를 반환한다.

    1024개 선형 탐색이지만 모델당 색이 10개 안팎이라 비용은 무시할 수준이다."""
    target_l, target_a, target_b = srgb_to_oklab(color)
    best_cell, best_distance = 0, None
    for cell, (light, a_axis, b_axis) in enumerate(_CELL_LAB):
        distance = ((light - target_l) ** 2
                    + (a_axis - target_a) ** 2
                    + (b_axis - target_b) ** 2)
        if best_distance is None or distance < best_distance:
            best_cell, best_distance = cell, distance
    return best_cell


def cell_uv(cell: int):
    """셀 중앙의 UV 좌표를 반환한다.

    페이스의 UV를 셀 중앙 한 점으로 모으므로 텍스처 필터링 번짐이 없다."""
    return (cell % GRID + 0.5) / GRID, (cell // GRID + 0.5) / GRID
