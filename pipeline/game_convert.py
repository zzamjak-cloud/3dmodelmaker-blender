"""게임용 glTF 변환의 순수 계산 — 팔레트 셀 → 단색 머티리얼 이름·색, 정규화 배율. bpy 없이 테스트한다.

엔진 셰이더가 텍스처가 아니라 머티리얼 albedo 하나만 읽는 경우(머티리얼별로 다시 칠하는 Godot 게임 등)를 위해
공유 팔레트 텍스처의 UV 셀을 셀 색 그대로의 단색 머티리얼로 바꾼다.

색 공간: 팔레트 셀 값은 sRGB 0~255 다(LP3D_Palette.png 픽셀 그대로). Principled Base Color 와 glTF
baseColorFactor 는 선형이므로 한 번만 sRGB→선형으로 바꿔 넣는다. 엔진 임포터(Godot 등)가 다시 sRGB 로
되돌리므로 albedo 는 팔레트 PNG 와 같은 색이 된다. 선형 변환을 빼면 엔진에서 색이 바래고, 두 번 하면 어두워진다.
"""


def srgb_to_linear(value: float) -> float:
    """sRGB 0~1 성분을 선형으로 변환한다."""
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def material_name(rgb) -> str:
    """셀 sRGB(0~255) 3튜플 → 'C_rrggbb'. 같은 색 셀은 같은 이름이 되어 한 머티리얼로 합쳐진다."""
    r, g, b = (int(c) for c in rgb[:3])
    return f"C_{r:02x}{g:02x}{b:02x}"


def linear_color(rgb):
    """셀 sRGB(0~255) → Base Color 용 선형 RGBA."""
    return tuple(srgb_to_linear(c / 255.0) for c in rgb[:3]) + (1.0,)


def uv_cell(u: float, v: float, cols: int, rows: int) -> int:
    """UV 좌표가 가리키는 팔레트 셀 인덱스 (셀 0 = 좌하단, 행 우선). 범위 밖 UV는 가장자리 셀로 자른다."""
    col = min(max(int(u * cols), 0), cols - 1)
    row = min(max(int(v * rows), 0), rows - 1)
    return row * cols + col


def fit_scale(size, height=None, footprint=None) -> float:
    """(x, y, z) 크기를 목표 높이·최대 발판(가로·세로 중 큰 쪽)에 맞추는 균일 배율. 둘 다 없으면 1.

    두 조건을 모두 주면 더 작은 배율을 써서 어느 쪽도 넘지 않게 한다."""
    factors = []
    if height:
        factors.append(height / max(size[2], 1e-6))
    if footprint:
        factors.append(footprint / max(size[0], size[1], 1e-6))
    return min(factors) if factors else 1.0
