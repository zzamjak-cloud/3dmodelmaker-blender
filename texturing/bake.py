"""AI 3면도를 공유 UV Atlas의 Diffuse/Albedo 텍스처로 투영한다.

Blender 데이터 접근은 :func:`bake_diffuse`에만 모으고, 좌표 투영과 래스터화는
Blender 없이도 회귀 테스트할 수 있는 순수 파이썬 함수로 유지한다.
"""

from __future__ import annotations

from array import array
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, replace
import binascii
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile
from typing import Iterable, Mapping, Sequence
import uuid
import zlib

try:  # Blender 밖의 순수 테스트에서도 이 모듈을 가져올 수 있어야 한다.
    import bpy  # type: ignore
    from mathutils import Vector  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - Blender 테스트에서 별도 검증
    bpy = None
    Vector = None


Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
BBox = tuple[int, int, int, int]


@dataclass(frozen=True)
class ViewSpec:
    """직교 뷰 하나의 월드 축 정의.

    ``right``/``up``은 화면 x/y축이고, ``toward_camera``는 depth 축이다.
    depth는 값이 클수록 카메라에 가깝다. 카메라는 중심에서 ``toward_camera``
    방향에 놓인다.
    """

    name: str
    right: Vec3
    up: Vec3
    toward_camera: Vec3


# Blender 표준 Z-up, -Y 정면 기준. TOP은 numpad 7(정면이 아래), BOTTOM은
# Ctrl+numpad 7(정면이 위) 배치와 같다.
VIEW_SPECS: dict[str, ViewSpec] = {
    "FRONT": ViewSpec("FRONT", (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
    "RIGHT": ViewSpec("RIGHT", (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
    "BACK": ViewSpec("BACK", (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "LEFT": ViewSpec("LEFT", (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), (-1.0, 0.0, 0.0)),
    "TOP": ViewSpec("TOP", (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "BOTTOM": ViewSpec("BOTTOM", (1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0)),
}
VIEW_NAMES = tuple(VIEW_SPECS)
# 생성 이미지가 반드시 있어야 하는 뷰. 나머지는 있으면 쓰고 없으면 대체한다.
REQUIRED_SOURCE_VIEW_NAMES = ("FRONT", "RIGHT", "BACK")
SOURCE_VIEW_NAMES = REQUIRED_SOURCE_VIEW_NAMES
# 소스가 없는 뷰의 대체 소스와 좌우 반전 여부.
_VIEW_SOURCE_FALLBACKS = {"LEFT": ("RIGHT", True)}
MAX_ATLAS_RESOLUTION = 4096
# 생성 실루엣 밖 표본을 최근접 전경 색으로 이어 붙일 최대 거리(피사체 크기 대비).
# 넓히면 실루엣이 좁게 그려진 부위(벌린 팔 등)에 가로 줄무늬 띠가 생긴다.
FOREGROUND_EXTEND_RATIO = 0.03
# 배경보다 밝은 무채색 픽셀을 배경으로 볼 때의 채도 상한과, 이 규칙을 켤 배경 휘도 하한.
# AI가 팔·몸통 틈을 배경(0.93)보다 밝은 순백으로 채우면 색 거리만으로는 전경이 된다.
BRIGHT_BACKGROUND_MAX_SATURATION = 0.06
BRIGHT_BACKGROUND_MIN_LUMINANCE = 0.6
# 테두리에서 시작하는 배경 플러드필의 이웃 차 허용치와 배경색과의 최대 거리.
BACKGROUND_FLOOD_NEIGHBOR_TOLERANCE = 0.02
BACKGROUND_FLOOD_MAX_DISTANCE = 0.15
# 부분 베이크에서 "미채색" 마커가 실제 뷰와 경쟁하는 기준 입사각. 이 각도보다
# 비스듬히 보이는 면은 색이 늘어진 표본이라 다음 시점이 다시 칠하도록 회색이 이긴다.
UNPAINTED_MARKER_ANGLE_DEGREES = 35.0
# 뷰 가중치 지수. 클수록 정면을 향한 뷰만 남아 전이 폭이 좁아진다.
DEFAULT_BLEND_EXPONENT = 4.0
# 뷰 간 색조 보정 gain의 허용 범위, 최소 표본 쌍 수, 표본별 비율 IQR 상한, 표본이
# 실루엣 안쪽으로 들어와야 하는 픽셀 여유. 자세가 어긋난 표본은 서로 다른 내용을
# 비교하므로 비율이 흩어진다. 그런 경우 보정하지 않는 편이 안전하다.
VIEW_GAIN_RANGE = (0.85, 1.18)
VIEW_GAIN_MIN_SAMPLES = 32
VIEW_GAIN_MAX_IQR = 0.15
VIEW_GAIN_INTERIOR_MARGIN = 8
# 실루엣 불일치 판정 상한. 행별 연속 구간 수가 다른 행의 비율과 모델 실루엣 안의
# 배경 구멍 비율. IoU는 팔을 붙여 그린 경우에도 0.87이 나와 판별력이 없다.
SILHOUETTE_SEGMENT_MISMATCH_LIMIT = 0.25
SILHOUETTE_HOLE_LIMIT = 0.03
# 베이크 후 렌더와 생성 그림의 일치 점수(0~1) 권장 하한.
VERIFY_SCORE_LIMIT = 0.6
# 실루엣 정합 IoU 평가 격자 한 변의 최대 칸 수와 행·열 워프 표의 최대 항목 수.
ALIGNMENT_GRID = 128
WARP_TABLE_LIMIT = 512
# 이 IoU 미만이면 정합을 믿지 않고 경계 상자 스트레치로 되돌아간다. 낮은 IoU의
# 변환을 그대로 쓰면 머리가 가슴 색으로 칠해지고 팽창 마스크가 진짜 전경을 자른다.
ALIGNMENT_MIN_IOU = 0.6
# 축별 스케일이 경계 상자 스트레치에서 벗어날 수 있는 범위. AI가 비율을 왜곡해도
# IoU 최적화가 직접 흡수하도록 넉넉히 둔다.
ALIGNMENT_ASPECT_RANGE = (0.6, 1.67)
# 워프로 흡수할 최대 상대 폭 차이. 이보다 크면 정합 실패로 보고 항등을 쓴다.
WARP_MAX_RELATIVE_CHANGE = 0.15
# 행 워프가 모델 구간 중앙을 옮길 수 있는 최대 거리(피사체 긴 변 대비). 이보다 큰
# 이동은 부위 대응이 틀렸다는 뜻이라 항등으로 둔다.
WARP_MAX_CENTER_SHIFT_RATIO = 0.01
# 정합된 모델 실루엣을 팽창시킬 여유(피사체 크기 대비). 이 밖은 무조건 배경이다.
MODEL_MASK_MARGIN_RATIO = 0.02
_IMAGE_MARKER = "uvmapping_ai_albedo"
_MATERIAL_MARKER = "uvmapping_ai_albedo"
# 0/1 마스크와 '0'/'1' 문자열 사이 변환표. 마스크를 큰 정수 비트열로 다룰 때 쓴다.
_MASK_TO_TEXT = bytes.maketrans(b"\x00\x01", b"01")
_TEXT_TO_MASK = bytes.maketrans(b"01", b"\x00\x01")


@dataclass(frozen=True)
class BakeTriangle:
    """월드 좌표 삼각형과 각 코너의 UV 좌표.

    ``vertex_normals``가 있으면 픽셀마다 보간해 뷰 가중치를 매기고, 없으면
    면 법선 하나로 삼각형 전체를 같은 가중치로 칠한다.
    """

    positions: tuple[Vec3, Vec3, Vec3]
    uvs: tuple[Vec2, Vec2, Vec2]
    normal: Vec3
    vertex_normals: tuple[Vec3, Vec3, Vec3] | None = None


@dataclass(frozen=True)
class RasterSource:
    """Blender에서 선형 색 공간으로 읽은 생성 이미지.

    ``filled``은 배경 픽셀을 가장 가까운 전경 색으로 채운 버퍼이고,
    ``foreground_distance``는 각 픽셀에서 전경까지의 픽셀 거리다. 생성 그림의
    실루엣이 모델보다 좁을 때 이 두 배열로 경계 밖 표본을 공간적으로 연속되게
    복구한다.
    """

    width: int
    height: int
    pixels: Sequence[float]
    subject_bbox: BBox
    background: tuple[float, float, float, float]
    background_threshold: float
    fallback_color: tuple[float, float, float, float]
    filled: Sequence[float]
    foreground_distance: Sequence[float]
    # 모델 실루엣과 정합된 변환. None이면 피사체 경계 상자 스트레치로 표본을 읽는다.
    alignment: "SilhouetteAlignment | None" = None
    # 색 거리로 판정한 전경 마스크(1=전경). 정합 단계가 재계산 없이 재사용한다.
    foreground: bytearray | None = None


@dataclass(frozen=True)
class SilhouetteAlignment:
    """투영 정규화 좌표(0-1)를 생성 이미지 픽셀 좌표로 옮기는 정합 변환.

    전역 변환(스케일·오프셋) 뒤에 행·열 워프를 적용한다. 워프 표는 소스 경계
    상자 구간을 균등 분할한 항목마다 ``(a, b)``를 담고 ``x' = a + b * x``로
    쓴다. AI가 팔 굵기나 어깨 폭을 다르게 그렸을 때 행 단위로 실루엣 폭을
    맞추기 위한 것이다. 열 워프 필드는 구버전 상태 호환용으로만 남아 있고
    더 이상 만들지 않는다. 기본은 워프 없이 전역 변환만 쓴다.
    """

    scale_x: float
    scale_y: float
    offset_x: float
    offset_y: float
    row_warp: tuple[tuple[float, float], ...] | None = None
    row_range: tuple[float, float] = (0.0, 0.0)
    column_warp: tuple[tuple[float, float], ...] | None = None
    column_range: tuple[float, float] = (0.0, 0.0)
    iou: float = 0.0

    def map_global(self, u: float, v: float) -> tuple[float, float]:
        return self.offset_x + u * self.scale_x, self.offset_y + v * self.scale_y

    def map_to_source(self, u: float, v: float) -> tuple[float, float]:
        x = self.offset_x + u * self.scale_x
        y = self.offset_y + v * self.scale_y
        row_warp = self.row_warp
        column_warp = self.column_warp
        # 열 워프는 워프 전 전역 좌표로 만들었으므로 행 워프 이전의 x로 찾는다.
        global_x = x
        if row_warp:
            a, b = _interpolate_warp(row_warp, self.row_range, y)
            x = a + b * x
        if column_warp:
            a, b = _interpolate_warp(column_warp, self.column_range, global_x)
            y = a + b * y
        return x, y


def _interpolate_warp(
    table: tuple[tuple[float, float], ...], span: tuple[float, float], coordinate: float
) -> tuple[float, float]:
    """워프 표를 좌표에 맞춰 선형 보간한다. 구간 밖은 가장자리 항목을 쓴다."""

    start, end = span
    count = len(table)
    if count == 1 or end <= start:
        return table[0]
    position = (coordinate - start) / (end - start) * count - 0.5
    if position <= 0.0:
        return table[0]
    if position >= count - 1:
        return table[-1]
    index = int(position)
    fraction = position - index
    first = table[index]
    second = table[index + 1]
    return (
        first[0] + (second[0] - first[0]) * fraction,
        first[1] + (second[1] - first[1]) * fraction,
    )


@dataclass(frozen=True)
class ProjectionFrame:
    """모든 뷰가 공유하는 직교 카메라 프레이밍."""

    center: Vec3
    scale: float
    projected_bboxes: Mapping[str, tuple[float, float, float, float]]


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return min(maximum, max(minimum, value))


def _as_unit(value: float | int) -> float:
    numeric = float(value)
    return _clamp(numeric / 255.0 if numeric > 1.0 else numeric)


def project_point(point: Vec3, view: str, center: Vec3, scale: float) -> tuple[float, float, float]:
    """월드 점을 정사각 직교 화면에 투영한다.

    depth는 카메라 방향 축 값이 클수록 가까운 값이다. 축 정의는
    :data:`VIEW_SPECS`를 따른다.
    """

    if scale <= 0.0:
        raise ValueError("투영 scale은 0보다 커야 합니다.")
    spec = VIEW_SPECS.get(view.upper())
    if spec is None:
        raise ValueError(f"지원하지 않는 투영 뷰입니다: {view}")
    x = point[0] - center[0]
    y = point[1] - center[1]
    z = point[2] - center[2]
    right, up, toward = spec.right, spec.up, spec.toward_camera
    screen_x = x * right[0] + y * right[1] + z * right[2]
    screen_y = x * up[0] + y * up[1] + z * up[2]
    depth = x * toward[0] + y * toward[1] + z * toward[2]
    return 0.5 + screen_x / scale, 0.5 + screen_y / scale, depth


def barycentric_weights(point: Vec2, a: Vec2, b: Vec2, c: Vec2) -> tuple[float, float, float] | None:
    """2D 삼각형 내부 점의 barycentric 가중치를 반환한다."""

    denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
    if abs(denominator) < 1.0e-12:
        return None
    first = ((b[1] - c[1]) * (point[0] - c[0]) + (c[0] - b[0]) * (point[1] - c[1])) / denominator
    second = ((c[1] - a[1]) * (point[0] - c[0]) + (a[0] - c[0]) * (point[1] - c[1])) / denominator
    third = 1.0 - first - second
    epsilon = -1.0e-7
    if first < epsilon or second < epsilon or third < epsilon:
        return None
    return first, second, third


def bilinear_sample(
    pixels: Sequence[float], width: int, height: int, x: float, y: float, channels: int = 4
) -> tuple[float, float, float, float]:
    """좌하단 원점 픽셀 배열을 bilinear 방식으로 읽는다."""

    if width <= 0 or height <= 0 or channels < 3:
        raise ValueError("이미지 크기 또는 채널 수가 올바르지 않습니다.")
    x = _clamp(x, 0.0, float(width - 1))
    y = _clamp(y, 0.0, float(height - 1))
    x0, y0 = int(math.floor(x)), int(math.floor(y))
    x1, y1 = min(width - 1, x0 + 1), min(height - 1, y0 + 1)
    tx, ty = x - x0, y - y0

    def channel(px: int, py: int, index: int) -> float:
        if index >= channels:
            return 1.0
        return float(pixels[(py * width + px) * channels + index])

    result = []
    for channel_index in range(4):
        bottom = channel(x0, y0, channel_index) * (1.0 - tx) + channel(x1, y0, channel_index) * tx
        top = channel(x0, y1, channel_index) * (1.0 - tx) + channel(x1, y1, channel_index) * tx
        result.append(bottom * (1.0 - ty) + top * ty)
    return tuple(result)  # type: ignore[return-value]


def detect_foreground_bbox(
    pixels: Sequence[float | int], width: int, height: int, channels: int = 4
) -> BBox:
    """투명 또는 단색 계열 배경에서 피사체의 경계 상자를 찾는다.

    반환 좌표의 최대값은 파이썬 slice처럼 exclusive이다. 판별이 불가능하면
    전체 이미지를 반환해 생성 이미지의 일부를 잘못 버리지 않는다.
    """

    if width <= 0 or height <= 0 or channels < 3:
        raise ValueError("이미지 크기 또는 채널 수가 올바르지 않습니다.")
    expected = width * height * channels
    if len(pixels) < expected:
        raise ValueError("픽셀 배열 길이가 이미지 크기보다 짧습니다.")

    alpha_available = channels >= 4
    alpha_values = []
    border_samples: list[tuple[float, float, float, float]] = []
    stride = max(1, min(width, height) // 256)

    def read(px: int, py: int) -> tuple[float, float, float, float]:
        offset = (py * width + px) * channels
        alpha = _as_unit(pixels[offset + 3]) if alpha_available else 1.0
        return (
            _as_unit(pixels[offset]),
            _as_unit(pixels[offset + 1]),
            _as_unit(pixels[offset + 2]),
            alpha,
        )

    for x in range(0, width, stride):
        border_samples.append(read(x, 0))
        border_samples.append(read(x, height - 1))
    for y in range(0, height, stride):
        border_samples.append(read(0, y))
        border_samples.append(read(width - 1, y))
    if alpha_available:
        alpha_values = [sample[3] for sample in border_samples]

    transparent_border = bool(alpha_values) and sum(alpha_values) / len(alpha_values) < 0.25
    sorted_channels = [sorted(sample[channel] for sample in border_samples) for channel in range(4)]
    middle = len(border_samples) // 2
    background = tuple(values[middle] for values in sorted_channels)

    # 투명도를 표현한 checker 배경은 두 색이 번갈아 나타난다. 한 가지 중앙값만
    # 배경으로 보면 나머지 checker 색과 구분선이 이어져 거대한 전경이 될 수
    # 있으므로, 테두리에서 반복되는 지배 색을 최대 네 개까지 배경으로 인정한다.
    color_bins: dict[int, list[float | int]] = {}
    for sample in border_samples:
        red = min(15, int(sample[0] * 16.0))
        green = min(15, int(sample[1] * 16.0))
        blue = min(15, int(sample[2] * 16.0))
        key = (red << 8) | (green << 4) | blue
        bucket = color_bins.setdefault(key, [0, 0.0, 0.0, 0.0])
        bucket[0] += 1
        bucket[1] += sample[0]
        bucket[2] += sample[1]
        bucket[3] += sample[2]
    minimum_palette_count = max(2, int(round(len(border_samples) * 0.04)))
    dominant_bins = sorted(color_bins.values(), key=lambda bucket: int(bucket[0]), reverse=True)
    background_palette = [
        (
            float(bucket[1]) / int(bucket[0]),
            float(bucket[2]) / int(bucket[0]),
            float(bucket[3]) / int(bucket[0]),
        )
        for bucket in dominant_bins[:4]
        if int(bucket[0]) >= minimum_palette_count
    ]
    if not background_palette:
        background_palette = [(background[0], background[1], background[2])]

    segment_start = background_palette[0]
    segment_vector = (0.0, 0.0, 0.0)
    segment_length_squared = 0.0
    if len(background_palette) >= 2:
        segment_vector = tuple(
            background_palette[1][channel] - segment_start[channel] for channel in range(3)
        )
        segment_length_squared = sum(value * value for value in segment_vector)

    def distance_to_background(color: Sequence[float]) -> float:
        """지배 배경색과 checker 경계의 보간색까지 포함한 색 거리를 구한다."""

        distance = min(
            max(abs(color[channel] - palette_color[channel]) for channel in range(3))
            for palette_color in background_palette
        )
        if segment_length_squared <= 1.0e-8:
            return distance
        projection = sum(
            (color[channel] - segment_start[channel]) * segment_vector[channel]
            for channel in range(3)
        ) / segment_length_squared
        projection = _clamp(projection)
        segment_distance = max(
            abs(
                color[channel]
                - (segment_start[channel] + segment_vector[channel] * projection)
            )
            for channel in range(3)
        )
        return min(distance, segment_distance)

    deviations = sorted(
        distance_to_background(sample)
        for sample in border_samples
    )
    median_deviation = deviations[len(deviations) // 2]
    threshold = min(0.20, max(0.055, median_deviation * 4.0 + 0.035))

    # bytearray 마스크와 재사용 가능한 uint32 큐만 사용한다. 생성 이미지가
    # 1K 이상이어도 픽셀마다 파이썬 객체를 만들지 않아 메모리 사용이 안정적이다.
    foreground_mask = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            color = read(x, y)
            if transparent_border:
                foreground = color[3] > 0.20
            else:
                background_distance = distance_to_background(color)
                foreground = color[3] > 0.05 and background_distance > threshold
            if foreground:
                foreground_mask[y * width + x] = 1

    # AI가 3면도에 추가한 FRONT 같은 라벨과 구분선은 본체와 떨어진 작은
    # 연결 성분이다. 모든 전경의 min/max를 쓰지 않고 가장 크면서 화면 중앙에
    # 가까운 성분을 본체로 정한 뒤, 근접한 부품 성분만 합친다.
    components = array("I")
    queue = array("I")
    pixel_count = width * height
    for start in range(pixel_count):
        if foreground_mask[start] != 1:
            continue
        del queue[:]
        queue.append(start)
        foreground_mask[start] = 2
        cursor = 0
        area = 0
        left = right = start % width
        bottom = top = start // width
        while cursor < len(queue):
            index = queue[cursor]
            cursor += 1
            x, y = index % width, index // width
            area += 1
            left = min(left, x)
            right = max(right, x)
            bottom = min(bottom, y)
            top = max(top, y)
            if x > 0 and foreground_mask[index - 1] == 1:
                foreground_mask[index - 1] = 2
                queue.append(index - 1)
            if x + 1 < width and foreground_mask[index + 1] == 1:
                foreground_mask[index + 1] = 2
                queue.append(index + 1)
            if y > 0 and foreground_mask[index - width] == 1:
                foreground_mask[index - width] = 2
                queue.append(index - width)
            if y + 1 < height and foreground_mask[index + width] == 1:
                foreground_mask[index + width] = 2
                queue.append(index + width)
        components.extend((area, left, bottom, right + 1, top + 1))

    if not components:
        return 0, 0, width, height

    def is_separator(component_left: int, component_bottom: int, component_right: int, component_top: int) -> bool:
        """길고 매우 얇은 3면도 구분선을 피사체 후보에서 제외한다."""

        component_width = component_right - component_left
        component_height = component_top - component_bottom
        edge_margin_x = max(1, int(round(width * 0.005)))
        edge_margin_y = max(1, int(round(height * 0.005)))
        vertical = (
            component_width <= max(3, int(round(width * 0.03)))
            and component_bottom <= edge_margin_y
            and component_top >= height - edge_margin_y
        )
        horizontal = (
            component_height <= max(3, int(round(height * 0.03)))
            and component_left <= edge_margin_x
            and component_right >= width - edge_margin_x
        )
        return vertical or horizontal

    main_offset = -1
    main_score = -1.0
    fallback_offset = 0
    fallback_area = -1
    for offset in range(0, len(components), 5):
        area, left, bottom, right, top = components[offset : offset + 5]
        if area > fallback_area:
            fallback_area = area
            fallback_offset = offset
        if is_separator(left, bottom, right, top):
            continue
        center_x = (left + right) * 0.5 / width
        center_y = (bottom + top) * 0.5 / height
        center_distance = min(1.0, math.hypot(center_x - 0.5, center_y - 0.5) / math.sqrt(0.5))
        score = area * (1.0 - center_distance * 0.35)
        if score > main_score:
            main_score = score
            main_offset = offset

    if main_offset < 0:
        main_offset = fallback_offset
    main_area, left, bottom, right, top = components[main_offset : main_offset + 5]
    main_width = right - left
    main_height = top - bottom
    close_gap = max(2, int(round(min(main_width, main_height) * 0.06)))
    accessory_gap = max(close_gap, int(round(max(main_width, main_height) * 0.20)))

    for offset in range(0, len(components), 5):
        if offset == main_offset:
            continue
        area, candidate_left, candidate_bottom, candidate_right, candidate_top = components[offset : offset + 5]
        if is_separator(candidate_left, candidate_bottom, candidate_right, candidate_top):
            continue
        gap_x = max(0, left - candidate_right, candidate_left - right)
        gap_y = max(0, bottom - candidate_top, candidate_bottom - top)
        gap = max(gap_x, gap_y)
        area_ratio = area / max(1, main_area)
        # 작은 손·장식은 본체 바로 곁에 있을 때 유지하고, 검·방패처럼 큰
        # 분리 부품은 조금 더 떨어져 있어도 포함한다. 멀리 놓인 글자는 제외된다.
        if (area_ratio >= 0.001 and gap <= close_gap) or (area_ratio >= 0.02 and gap <= accessory_gap):
            left = min(left, candidate_left)
            bottom = min(bottom, candidate_bottom)
            right = max(right, candidate_right)
            top = max(top, candidate_top)

    padding_x = max(1, int(round(width * 0.005)))
    padding_y = max(1, int(round(height * 0.005)))
    return (
        max(0, left - padding_x),
        max(0, bottom - padding_y),
        min(width, right + padding_x),
        min(height, top + padding_y),
    )


def dilate_rgba(rgba: bytearray, occupied: bytearray, width: int, height: int, padding: int) -> int:
    """UV 경계색을 빈 픽셀로 확장해 mipmap seam을 줄인다."""

    pixel_count = width * height
    if len(rgba) != pixel_count * 4 or len(occupied) != pixel_count:
        raise ValueError("Atlas 픽셀 버퍼 크기가 올바르지 않습니다.")
    if padding <= 0:
        return 0
    padding = min(65534, int(padding))
    distances = array("H", [65535]) * pixel_count
    queue: deque[int] = deque()
    for index, value in enumerate(occupied):
        if value:
            distances[index] = 0
            queue.append(index)

    expanded = 0
    while queue:
        index = queue.popleft()
        distance = distances[index]
        if distance >= padding:
            continue
        x, y = index % width, index // width
        neighbors = []
        if x > 0:
            neighbors.append(index - 1)
        if x + 1 < width:
            neighbors.append(index + 1)
        if y > 0:
            neighbors.append(index - width)
        if y + 1 < height:
            neighbors.append(index + width)
        for neighbor in neighbors:
            if distances[neighbor] != 65535:
                continue
            distances[neighbor] = distance + 1
            source_offset = index * 4
            target_offset = neighbor * 4
            rgba[target_offset : target_offset + 4] = rgba[source_offset : source_offset + 4]
            occupied[neighbor] = 2
            expanded += 1
            queue.append(neighbor)
    return expanded


def _png_chunk(name: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + name + payload + struct.pack(">I", binascii.crc32(name + payload) & 0xFFFFFFFF)


def encode_srgb_png(rgba: bytes | bytearray, width: int, height: int) -> bytes:
    """좌하단 원점 RGBA8 버퍼를 표준 sRGB PNG로 인코딩한다."""

    if width <= 0 or height <= 0 or len(rgba) != width * height * 4:
        raise ValueError("PNG로 저장할 픽셀 버퍼 크기가 올바르지 않습니다.")
    rows = bytearray()
    row_bytes = width * 4
    for y in range(height - 1, -1, -1):
        rows.append(0)  # PNG filter None
        start = y * row_bytes
        rows.extend(rgba[start : start + row_bytes])
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + b"".join(
        (
            _png_chunk(b"IHDR", header),
            _png_chunk(b"sRGB", b"\x00"),
            _png_chunk(b"gAMA", struct.pack(">I", 45455)),
            _png_chunk(b"IDAT", zlib.compress(bytes(rows), level=6)),
            _png_chunk(b"IEND", b""),
        )
    )


def _linear_to_srgb_byte(value: float) -> int:
    value = _clamp(value)
    if value <= 0.0031308:
        encoded = value * 12.92
    else:
        encoded = 1.055 * (value ** (1.0 / 2.4)) - 0.055
    return int(round(_clamp(encoded) * 255.0))


def _srgb_to_linear(value: float) -> float:
    """Blender가 반환한 파일 픽셀의 sRGB 채널을 선형값으로 바꾼다."""

    value = _clamp(float(value))
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


# 연산자가 가이드 회색 같은 sRGB 상수를 선형 베이크 색으로 넘길 때 쓰는 공개 이름.
srgb_to_linear = _srgb_to_linear


def _projected_bbox(triangles: Sequence[BakeTriangle], view: str, center: Vec3, scale: float) -> tuple[float, float, float, float]:
    projected = [project_point(point, view, center, scale) for triangle in triangles for point in triangle.positions]
    minimum_u = min(point[0] for point in projected)
    maximum_u = max(point[0] for point in projected)
    minimum_v = min(point[1] for point in projected)
    maximum_v = max(point[1] for point in projected)
    epsilon = 1.0e-8
    if maximum_u - minimum_u < epsilon:
        minimum_u, maximum_u = 0.0, 1.0
    if maximum_v - minimum_v < epsilon:
        minimum_v, maximum_v = 0.0, 1.0
    return minimum_u, minimum_v, maximum_u, maximum_v


def _screen_raster_bounds(points: tuple[Vec2, Vec2, Vec2], width: int, height: int) -> tuple[int, int, int, int]:
    minimum_x = max(0, int(math.floor(min(point[0] for point in points) * width - 0.5)))
    maximum_x = min(width - 1, int(math.ceil(max(point[0] for point in points) * width - 0.5)))
    minimum_y = max(0, int(math.floor(min(point[1] for point in points) * height - 0.5)))
    maximum_y = min(height - 1, int(math.ceil(max(point[1] for point in points) * height - 0.5)))
    return minimum_x, minimum_y, maximum_x, maximum_y


def build_depth_buffer(
    triangles: Sequence[BakeTriangle], view: str, center: Vec3, scale: float, resolution: int
) -> array:
    """화면 픽셀마다 카메라에 가장 가까운 depth(max)를 기록한다."""

    if resolution <= 0:
        raise ValueError("depth buffer 해상도는 0보다 커야 합니다.")
    buffer = array("f", [-math.inf]) * (resolution * resolution)
    for triangle in triangles:
        projected = tuple(project_point(point, view, center, scale) for point in triangle.positions)
        screen = tuple((point[0], point[1]) for point in projected)
        bounds = _screen_raster_bounds(screen, resolution, resolution)
        if bounds[0] > bounds[2] or bounds[1] > bounds[3]:
            continue
        for y in range(bounds[1], bounds[3] + 1):
            py = (y + 0.5) / resolution
            for x in range(bounds[0], bounds[2] + 1):
                px = (x + 0.5) / resolution
                weights = barycentric_weights((px, py), screen[0], screen[1], screen[2])
                if weights is None:
                    continue
                depth = sum(weights[index] * projected[index][2] for index in range(3))
                pixel_index = y * resolution + x
                if depth > buffer[pixel_index]:
                    buffer[pixel_index] = depth
    return buffer


def _visibility_weight(
    projected: tuple[float, float, float], depth_buffer: Sequence[float], resolution: int, tolerance: float
) -> float:
    """해당 뷰에서 점이 보이는 정도를 0~1로 돌려준다.

    가림을 불린으로 자르면 실루엣 경계에서 색이 급변한다. 허용 오차 안이면
    1.0, 그보다 세 배 더 깊이 가려질 때까지 선형으로 0.0에 이르게 한다.
    """

    u, v, depth = projected
    if u < 0.0 or u > 1.0 or v < 0.0 or v > 1.0:
        return 0.0
    x = min(resolution - 1, max(0, int(u * resolution)))
    y = min(resolution - 1, max(0, int(v * resolution)))
    nearest = -math.inf
    for offset_y in (-1, 0, 1):
        sample_y = y + offset_y
        if sample_y < 0 or sample_y >= resolution:
            continue
        row = sample_y * resolution
        for offset_x in (-1, 0, 1):
            sample_x = x + offset_x
            if 0 <= sample_x < resolution:
                value = depth_buffer[row + sample_x]
                if value > nearest:
                    nearest = value
    if not math.isfinite(nearest):
        return 0.0
    gap = nearest - depth
    if gap <= tolerance:
        return 1.0
    return max(0.0, 1.0 - (gap - tolerance) / (tolerance * 3.0))


def _is_foreground(source: RasterSource, color: tuple[float, float, float, float]) -> bool:
    return _foreground_test(source.background, source.background_threshold)(color)


def _foreground_test(background, background_threshold: float):
    """배경 색·투명도 기준으로 전경 여부를 판정하는 함수를 만든다."""

    if background[3] < 0.25:
        def transparent_test(color) -> bool:
            return color[3] > 0.12

        return transparent_test

    limit = background_threshold * 0.55
    bright_rule = _bright_background_test(background)

    def opaque_test(color) -> bool:
        difference = max(abs(color[channel] - background[channel]) for channel in range(3))
        if color[3] <= 0.05 or difference <= limit:
            return False
        return not bright_rule(color)

    return opaque_test


def _luminance(color) -> float:
    return 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]


def _bright_background_test(background):
    """배경보다 밝고 채도가 없는 픽셀을 배경으로 보는 판정 함수를 만든다.

    밝은 배경 위에서만 켠다. 어두운 배경에서는 밝은 무채색이 흰 옷일 수 있다.
    """

    background_luminance = _luminance(background)
    if background_luminance < BRIGHT_BACKGROUND_MIN_LUMINANCE:
        return lambda color: False
    threshold = background_luminance + 0.01

    def is_bright_background(color) -> bool:
        return (
            _luminance(color) > threshold
            and max(color[0], color[1], color[2]) - min(color[0], color[1], color[2])
            < BRIGHT_BACKGROUND_MAX_SATURATION
        )

    return is_bright_background


def background_flood_mask(
    pixels: Sequence[float],
    width: int,
    height: int,
    background: tuple[float, float, float, float],
    *,
    neighbor_tolerance: float = BACKGROUND_FLOOD_NEIGHBOR_TOLERANCE,
    max_distance: float = BACKGROUND_FLOOD_MAX_DISTANCE,
) -> bytearray:
    """테두리에서 이어진 배경 영역(1=배경)을 플러드필로 찾는다.

    배경에 완만한 그라데이션이 있으면 색 거리 임계만으로는 안쪽이 전경으로
    남는다. 이웃끼리 차이가 작고 배경색에서 크게 벗어나지 않은 픽셀을 테두리부터
    이어 붙이면 그라데이션은 통과하고 피사체 경계에서는 멈춘다. 512px 이상은
    2배 축소 격자에서 채운 뒤 되돌려 시간을 줄인다.
    """

    if background[3] < 0.25:
        return bytearray(width * height)
    step = 2 if max(width, height) >= 512 else 1
    grid_width = (width + step - 1) // step
    grid_height = (height + step - 1) // step
    size = grid_width * grid_height
    luminance = array("f", [0.0]) * size
    near_background = bytearray(size)
    for gy in range(grid_height):
        y = min(height - 1, gy * step)
        for gx in range(grid_width):
            x = min(width - 1, gx * step)
            offset = (y * width + x) * 4
            r, g, b = pixels[offset], pixels[offset + 1], pixels[offset + 2]
            index = gy * grid_width + gx
            luminance[index] = 0.2126 * r + 0.7152 * g + 0.0722 * b
            distance = max(abs(r - background[0]), abs(g - background[1]), abs(b - background[2]))
            if distance <= max_distance:
                near_background[index] = 1
    visited = bytearray(size)
    queue = deque()
    for gx in range(grid_width):
        for gy in (0, grid_height - 1):
            index = gy * grid_width + gx
            if near_background[index] and not visited[index]:
                visited[index] = 1
                queue.append(index)
    for gy in range(grid_height):
        for gx in (0, grid_width - 1):
            index = gy * grid_width + gx
            if near_background[index] and not visited[index]:
                visited[index] = 1
                queue.append(index)
    while queue:
        index = queue.popleft()
        gx = index % grid_width
        gy = index // grid_width
        current = luminance[index]
        for neighbor in (
            index - 1 if gx > 0 else -1,
            index + 1 if gx + 1 < grid_width else -1,
            index - grid_width if gy > 0 else -1,
            index + grid_width if gy + 1 < grid_height else -1,
        ):
            if neighbor < 0 or visited[neighbor] or not near_background[neighbor]:
                continue
            if abs(luminance[neighbor] - current) > neighbor_tolerance:
                continue
            visited[neighbor] = 1
            queue.append(neighbor)
    if step == 1:
        return visited
    mask = bytearray(width * height)
    for y in range(height):
        grid_row = (y // step) * grid_width
        row = y * width
        for x in range(width):
            if visited[grid_row + x // step]:
                mask[row + x] = 1
    return mask


def coarse_foreground_mask(
    pixels: Sequence[float],
    width: int,
    height: int,
    background: tuple[float, float, float, float],
    background_threshold: float,
) -> bytearray:
    """색 거리·밝은 무채색 규칙·테두리 플러드필을 합친 전경 마스크(1=전경)."""

    mask = foreground_mask(pixels, width, height, _foreground_test(background, background_threshold))
    if background_threshold < 0.0:
        # 피사체가 프레임을 가득 채운 경우다. 테두리가 곧 피사체라 플러드필을 쓸 수 없다.
        return mask
    flood = background_flood_mask(pixels, width, height, background)
    if any(flood):
        size = width * height
        combined = int.from_bytes(bytes(mask), "big") & ~int.from_bytes(bytes(flood), "big")
        mask = bytearray((combined & ((1 << (size * 8)) - 1)).to_bytes(size, "big"))
    return mask


def _nearest_foreground(
    pixels: Sequence[float], width: int, height: int, is_foreground
) -> tuple[array, array, float]:
    """chamfer 스캔 두 번으로 픽셀마다 최근접 전경 인덱스와 제곱 거리를 구한다."""

    return _nearest_in_mask(foreground_mask(pixels, width, height, is_foreground), width, height)


def foreground_mask(pixels: Sequence[float], width: int, height: int, is_foreground) -> bytearray:
    """판정 함수를 픽셀마다 적용한 전경 마스크(1=전경)를 만든다."""

    size = width * height
    mask = bytearray(size)
    for index in range(size):
        offset = index * 4
        if is_foreground((pixels[offset], pixels[offset + 1], pixels[offset + 2], pixels[offset + 3])):
            mask[index] = 1
    return mask


def _mask_bits(mask: Sequence[int], width: int, height: int, radius: int) -> tuple[int, int]:
    """마스크를 행마다 ``radius`` 0 패딩을 둔 비트열 정수로 바꾼다. (bits, stride)를 돌려준다."""

    stride = width + 2 * radius
    pad = b"0" * radius
    rows = [
        pad + bytes(mask[y * width : (y + 1) * width]).translate(_MASK_TO_TEXT) + pad
        for y in range(height)
    ]
    return int(b"".join(rows), 2), stride


def _bits_to_mask(bits: int, width: int, height: int, radius: int) -> bytearray:
    stride = width + 2 * radius
    total = stride * height
    bits &= (1 << total) - 1
    text = format(bits, "b").rjust(total, "0").encode("ascii")
    result = bytearray(width * height)
    for y in range(height):
        start = y * stride + radius
        result[y * width : (y + 1) * width] = text[start : start + width].translate(_TEXT_TO_MASK)
    return result


def _mask_boundary_indices(mask: Sequence[int], width: int, height: int) -> list[int]:
    """4-이웃 중 배경이 있는 전경 픽셀(실루엣 가장자리)의 인덱스 목록."""

    bits, stride = _mask_bits(mask, width, height, 1)
    if bits == 0:
        return []
    interior = bits & (bits << 1) & (bits >> 1) & (bits << stride) & (bits >> stride)
    boundary = _bits_to_mask(bits & ~interior, width, height, 1)
    indices = []
    position = boundary.find(b"\x01")
    while position >= 0:
        indices.append(position)
        position = boundary.find(b"\x01", position + 1)
    return indices


def _nearest_in_mask_bounded(
    mask: Sequence[int], width: int, height: int, max_distance: float
) -> tuple[dict[int, int], dict[int, float]]:
    """실루엣 가장자리에서 ``max_distance`` 안쪽 배경 픽셀만 최근접 전경을 찾는다.

    표본은 이 거리 밖이면 어차피 다른 시점에 양보하므로 전체 chamfer 변환 대신
    가장자리에서 시작하는 brushfire로 필요한 띠만 계산한다. 2K 소스에서 전 픽셀
    변환은 수 초가 걸리지만 띠는 그 수십분의 일이다.
    """

    limit_squared = max_distance * max_distance
    nearest: dict[int, int] = {}
    squared: dict[int, float] = {}
    queue = deque()
    for index in _mask_boundary_indices(mask, width, height):
        nearest[index] = index
        squared[index] = 0.0
        queue.append(index)
    offsets = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1))
    while queue:
        index = queue.popleft()
        source_index = nearest[index]
        source_x = source_index % width
        source_y = source_index // width
        x = index % width
        y = index // width
        for offset_x, offset_y in offsets:
            neighbor_x = x + offset_x
            neighbor_y = y + offset_y
            if not (0 <= neighbor_x < width and 0 <= neighbor_y < height):
                continue
            neighbor = neighbor_y * width + neighbor_x
            if mask[neighbor]:
                continue
            delta_x = source_x - neighbor_x
            delta_y = source_y - neighbor_y
            candidate = float(delta_x * delta_x + delta_y * delta_y)
            if candidate > limit_squared:
                continue
            current = squared.get(neighbor)
            if current is not None and current <= candidate:
                continue
            squared[neighbor] = candidate
            nearest[neighbor] = source_index
            queue.append(neighbor)
    return nearest, squared


def _nearest_in_mask(mask: Sequence[int], width: int, height: int) -> tuple[array, array, float]:
    size = width * height
    nearest = array("i", [-1]) * size
    foreground_found = False
    for index in range(size):
        if mask[index]:
            nearest[index] = index
            foreground_found = True
    infinity = float(width * width + height * height + 1)
    if not foreground_found:
        return nearest, array("f", [infinity]) * size, infinity

    squared = array("f", [0.0 if nearest[index] >= 0 else infinity for index in range(size)])

    def sweep(rows, columns, offsets) -> None:
        for y in rows:
            row = y * width
            for x in columns:
                index = row + x
                best_squared = squared[index]
                if best_squared == 0.0:
                    continue
                best_nearest = nearest[index]
                for offset_x, offset_y in offsets:
                    neighbor_x = x + offset_x
                    neighbor_y = y + offset_y
                    if not (0 <= neighbor_x < width and 0 <= neighbor_y < height):
                        continue
                    candidate = nearest[neighbor_y * width + neighbor_x]
                    if candidate < 0:
                        continue
                    source_x = candidate % width
                    source_y = candidate // width
                    delta_x = source_x - x
                    delta_y = source_y - y
                    candidate_squared = float(delta_x * delta_x + delta_y * delta_y)
                    if candidate_squared < best_squared:
                        best_squared = candidate_squared
                        best_nearest = candidate
                squared[index] = best_squared
                nearest[index] = best_nearest

    sweep(range(height), range(width), ((-1, 0), (0, -1), (-1, -1), (1, -1)))
    sweep(
        range(height - 1, -1, -1),
        range(width - 1, -1, -1),
        ((1, 0), (0, 1), (1, 1), (-1, 1)),
    )
    return nearest, squared, infinity


def extend_foreground(
    pixels: Sequence[float], width: int, height: int, is_foreground
) -> tuple[array, array]:
    """배경을 가장 가까운 전경 색으로 채우고 전경까지의 거리를 함께 돌려준다.

    표본이 실루엣 밖으로 나갔을 때 중심 쪽으로 몇 단계씩 끌어당기면 이웃 픽셀이
    서로 다른 위치를 읽어 빗살 무늬가 생긴다. 그래서 미리 연속적인 확장 버퍼를
    만들어 둔다.
    """

    return extend_foreground_mask(
        pixels, width, height, foreground_mask(pixels, width, height, is_foreground)
    )


def mask_distance(mask: Sequence[int], width: int, height: int) -> array:
    """마스크(1=전경)에서 픽셀마다 가장 가까운 전경까지의 거리를 구한다."""

    size = width * height
    _nearest, squared, infinity = _nearest_in_mask(mask, width, height)
    far = float(max(width, height))
    return array(
        "f",
        [math.sqrt(squared[index]) if squared[index] < infinity else far for index in range(size)],
    )


def extend_foreground_mask(
    pixels: Sequence[float],
    width: int,
    height: int,
    mask: Sequence[int],
    max_distance: float | None = None,
) -> tuple[array, array]:
    """미리 만든 전경 마스크로 :func:`extend_foreground`와 같은 버퍼를 만든다.

    ``max_distance``를 주면 실루엣에서 그 거리 안쪽만 채우고 바깥은 원본 색과
    "멀다" 거리로 둔다. 표본 함수가 그 거리 밖을 버리므로 결과는 같다.
    """

    size = width * height
    far = float(max(width, height))
    if max_distance is not None:
        filled = array("f", pixels[: size * 4])
        distance = array("f", [far]) * size
        mask_bytes = bytes(mask)
        position = mask_bytes.find(b"\x01")
        while position >= 0:
            distance[position] = 0.0
            position = mask_bytes.find(b"\x01", position + 1)
        nearest_map, squared_map = _nearest_in_mask_bounded(mask, width, height, max_distance)
        for index, source_index in nearest_map.items():
            if source_index == index:
                continue
            source_offset = source_index * 4
            offset = index * 4
            filled[offset] = pixels[source_offset]
            filled[offset + 1] = pixels[source_offset + 1]
            filled[offset + 2] = pixels[source_offset + 2]
            filled[offset + 3] = pixels[source_offset + 3]
            distance[index] = math.sqrt(squared_map[index])
        return filled, distance

    nearest, squared, infinity = _nearest_in_mask(mask, width, height)
    distance = array("f", [0.0]) * size
    if all(index < 0 for index in nearest):
        return array("f", pixels[: size * 4]), distance

    filled = array("f", [0.0]) * (size * 4)
    for index in range(size):
        source_index = nearest[index]
        if source_index < 0:
            source_index = index
        source_offset = source_index * 4
        offset = index * 4
        filled[offset] = pixels[source_offset]
        filled[offset + 1] = pixels[source_offset + 1]
        filled[offset + 2] = pixels[source_offset + 2]
        filled[offset + 3] = pixels[source_offset + 3]
        distance[index] = math.sqrt(squared[index]) if squared[index] < infinity else float(
            max(width, height)
        )
    return filled, distance


def build_raster_source(
    *,
    width: int,
    height: int,
    pixels: Sequence[float],
    subject_bbox: BBox,
    background: tuple[float, float, float, float],
    background_threshold: float,
    fallback_color: tuple[float, float, float, float],
) -> RasterSource:
    """생성 이미지 한 장에서 전경 확장 버퍼까지 갖춘 표본 원본을 만든다."""

    mask = coarse_foreground_mask(pixels, width, height, background, background_threshold)
    filled, distance = extend_foreground_mask(
        pixels, width, height, mask, _extend_radius(subject_bbox)
    )
    return RasterSource(
        width=width,
        height=height,
        pixels=pixels,
        subject_bbox=subject_bbox,
        background=background,
        background_threshold=background_threshold,
        fallback_color=fallback_color,
        filled=filled,
        foreground_distance=distance,
        foreground=mask,
    )


def _extend_radius(subject_bbox: BBox) -> float:
    """표본 함수가 실루엣 밖 표본을 받아들이는 최대 거리(+여유)."""

    left, bottom, right, top = subject_bbox
    return max(4.0, FOREGROUND_EXTEND_RATIO * max(right - left, top - bottom)) + 2.0


def dilate_mask(mask: Sequence[int], width: int, height: int, radius: int) -> bytearray:
    """마스크(1=전경)를 사각 반경 ``radius``만큼 팽창시킨다.

    픽셀 루프 대신 이미지를 큰 정수의 비트열로 바꿔 시프트·OR로 처리한다.
    행 사이에 ``radius``만큼 0을 끼워 가로 시프트가 이웃 행으로 새지 않게 한다.
    """

    if radius <= 0:
        return bytearray(mask)
    bits, stride = _mask_bits(mask, width, height, radius)
    if bits == 0:
        return bytearray(width * height)
    horizontal = bits
    for shift in range(1, radius + 1):
        horizontal |= (bits << shift) | (bits >> shift)
    vertical = horizontal
    for shift in range(1, radius + 1):
        vertical |= (horizontal << (shift * stride)) | (horizontal >> (shift * stride))
    return _bits_to_mask(vertical, width, height, radius)


def _mask_and(first: Sequence[int], second: Sequence[int]) -> bytearray:
    """두 0/1 마스크의 AND. 정수 변환으로 픽셀 루프를 피한다."""

    size = len(first)
    combined = int.from_bytes(bytes(first), "big") & int.from_bytes(bytes(second), "big")
    return bytearray(combined.to_bytes(size, "big"))


def build_model_mask(depth_buffer: Sequence[float], resolution: int) -> bytearray:
    """깊이 버퍼에서 유한 depth가 기록된 픽셀(=모델 실루엣)을 1로 표시한다."""

    size = resolution * resolution
    mask = bytearray(size)
    for index in range(size):
        if depth_buffer[index] > -math.inf:
            mask[index] = 1
    return mask


def _mask_runs(mask: Sequence[int], width: int, height: int, bbox: BBox) -> tuple[list, list]:
    """경계 상자 안에서 행별·열별 연속 구간 목록 ``[(시작, 끝+1), ...]``을 구한다. 빈 줄은 None.

    바깥 끝점 하나로 줄이면 발밑 그림자나 떨어진 소품이 실루엣 폭에 섞인다.
    구간을 따로 두면 모델 구간과 겹치는 것만 골라 쓸 수 있다.
    """

    left, bottom, right, top = bbox
    left, right = max(0, left), min(width, right)
    bottom, top = max(0, bottom), min(height, top)
    rows: list = [None] * height
    columns: list = [None] * width
    column_open = [None] * width
    for y in range(bottom, top):
        row = y * width
        runs = []
        start = None
        for x in range(left, right):
            if mask[row + x]:
                if start is None:
                    start = x
                if column_open[x] is None:
                    column_open[x] = y
            else:
                if start is not None:
                    runs.append((start, x))
                    start = None
                opened = column_open[x]
                if opened is not None:
                    columns[x] = (columns[x] or []) + [(opened, y)]
                    column_open[x] = None
        if start is not None:
            runs.append((start, right))
        if runs:
            rows[y] = runs
    for x in range(left, right):
        opened = column_open[x]
        if opened is not None:
            columns[x] = (columns[x] or []) + [(opened, top)]
    return rows, columns


def _runs_correspond(model_runs, source_runs) -> bool:
    """모델과 소스의 연속 구간이 같은 수이고 순서대로 서로 겹칠 때만 1:1 대응으로 본다.

    팔·다리가 있는 줄에서 구간 수가 다르면 "어느 구간이 어느 부위인지"를 알 수
    없고, 바깥 극값만 맞추면 몸통 중앙이 옆으로 밀린다.
    """

    if not model_runs or not source_runs or len(model_runs) != len(source_runs):
        return False
    for model_run, source_run in zip(model_runs, source_runs):
        if min(model_run[1], source_run[1]) - max(model_run[0], source_run[0]) <= 0.0:
            return False
    return True


def _build_warp_table(
    count: int,
    span: tuple[float, float],
    model_runs_at,
    source_runs_at,
    subject_size: float,
) -> tuple[tuple[float, float], ...] | None:
    """행마다 모델 구간을 소스 구간으로 옮기는 ``(a, b)`` 표를 만든다.

    구간이 1:1 대응하는 줄만 워프하고, 폭 비율이 15%를 넘거나 구간 중앙이
    피사체 크기의 1% 넘게 움직이는 줄은 항등으로 둔다. 항등 줄은 평활에 섞지
    않는다. 항등 ``(0, 1)``과 오프셋이 큰 아핀을 평균하면 이웃 줄마다 부호가
    다른 이동이 남아 세로 특징이 지그재그가 되기 때문이다.
    """

    start, end = span
    if count <= 0 or end <= start:
        return None
    shift_limit = max(1.0, subject_size * WARP_MAX_CENTER_SHIFT_RATIO)
    raw: list[tuple[float, float] | None] = []
    for index in range(count):
        coordinate = start + (index + 0.5) * (end - start) / count
        model_runs = model_runs_at(coordinate)
        source_runs = None if not model_runs else source_runs_at(coordinate)
        if not _runs_correspond(model_runs, source_runs):
            raw.append(None)
            continue
        model_start, model_end = model_runs[0][0], model_runs[-1][1]
        source_start, source_end = source_runs[0][0], source_runs[-1][1]
        model_width = model_end - model_start
        source_width = source_end - source_start
        if model_width <= 1.0e-6 or source_width <= 1.0e-6:
            raw.append(None)
            continue
        ratio = source_width / model_width
        if abs(ratio - 1.0) > WARP_MAX_RELATIVE_CHANGE:
            raw.append(None)
            continue
        intercept = source_start - ratio * model_start
        center = (model_start + model_end) * 0.5
        if abs(intercept + (ratio - 1.0) * center) > shift_limit:
            raw.append(None)
            continue
        raw.append((intercept, ratio))
    if all(entry is None for entry in raw):
        return None
    radius = max(3, int(round(count * 0.02)))
    smoothed: list[tuple[float, float]] = []
    for index in range(count):
        if raw[index] is None:
            smoothed.append((0.0, 1.0))
            continue
        # 같은 워프 블록 안의 이웃만 평균한다. 항등 줄을 만나면 그쪽은 멈춘다.
        window = [raw[index]]
        for step in range(1, radius + 1):
            neighbor = index - step
            if neighbor < 0 or raw[neighbor] is None:
                break
            window.append(raw[neighbor])
        for step in range(1, radius + 1):
            neighbor = index + step
            if neighbor >= count or raw[neighbor] is None:
                break
            window.append(raw[neighbor])
        smoothed.append(
            (
                sum(item[0] for item in window) / len(window),
                sum(item[1] for item in window) / len(window),
            )
        )
    # 워프 블록 가장자리는 항등 줄과 한 줄 차이로 만나므로, 항등 줄까지의 거리에
    # 비례해 워프를 줄여 경계에서 줄마다 튀는 이동을 없앤다.
    tapered: list[tuple[float, float]] = []
    for index in range(count):
        if raw[index] is None:
            tapered.append((0.0, 1.0))
            continue
        distance = radius + 1
        for step in range(1, radius + 1):
            lower = index - step
            upper = index + step
            if (lower < 0 or raw[lower] is None) or (upper >= count or raw[upper] is None):
                distance = step
                break
        factor = min(1.0, distance / (radius + 1))
        intercept, ratio = smoothed[index]
        tapered.append((intercept * factor, 1.0 + (ratio - 1.0) * factor))
    return tuple(tapered)


def align_silhouette(
    model_mask: Sequence[int],
    mask_resolution: int,
    model_bbox_in_mask: tuple[float, float, float, float],
    source_mask: Sequence[int],
    width: int,
    height: int,
    source_bbox: BBox,
    *,
    warp: bool = False,
) -> SilhouetteAlignment:
    """모델 실루엣과 생성 실루엣의 IoU를 최대화하는 정합 변환을 찾는다.

    ``model_bbox_in_mask``는 투영 화면(0-1) 단위의 모델 경계 상자다. 정규화
    좌표 ``u``/``v``는 이 상자 안에서 0-1이므로, 반환 변환은 ``u``/``v``를
    소스 픽셀 좌표로 옮긴다. 경계 상자만 맞추면 AI가 그린 라벨이나 머리카락
    한 가닥에도 흔들리므로 실루엣 면적 겹침으로 스케일·위치를 고른다.
    """

    min_u, min_v, max_u, max_v = model_bbox_in_mask
    model_width = max(1.0e-8, max_u - min_u)
    model_height = max(1.0e-8, max_v - min_v)
    left, bottom, right, top = source_bbox
    source_width = max(1.0, float(right - left - 1))
    source_height = max(1.0, float(top - bottom - 1))
    # 기준 변환은 경계 상자 스트레치(축별 독립)다. 여기서 균일 스케일과 축별
    # 비율을 함께 탐색하므로, 비율이 맞는 경우와 왜곡된 경우를 모두 덮는다.
    base_scale_x = source_width
    base_scale_y = source_height
    size = max(source_width, source_height)
    # 모델 실루엣 무게중심(정규화 u,v). 라벨·그림자로 오염된 경계 상자 대신
    # 실루엣 무게중심끼리 맞추는 것을 초기 위치로 삼는다.
    model_count = 0
    model_sum_x = 0
    model_sum_y = 0
    model_step = max(1, mask_resolution // 256)
    for my in range(0, mask_resolution, model_step):
        row = my * mask_resolution
        for mx in range(0, mask_resolution, model_step):
            if model_mask[row + mx]:
                model_count += 1
                model_sum_x += mx
                model_sum_y += my
    if model_count:
        model_cu = ((model_sum_x / model_count + 0.5) / mask_resolution - min_u) / model_width
        model_cv = ((model_sum_y / model_count + 0.5) / mask_resolution - min_v) / model_height
    else:
        model_cu = model_cv = 0.5

    # IoU 평가 격자: 소스 경계 상자를 25% 넓힌 관심 영역을 최대 128²로 나눈다.
    margin_x = source_width * 0.25
    margin_y = source_height * 0.25
    roi_left = max(0.0, left - margin_x)
    roi_right = min(float(width), right + margin_x)
    roi_bottom = max(0.0, bottom - margin_y)
    roi_top = min(float(height), top + margin_y)
    # 칸 한 변이 2px보다 작아질 만큼 잘게 나눌 필요는 없다.
    grid = min(ALIGNMENT_GRID, max(8, int(max(roi_right - roi_left, roi_top - roi_bottom) / 2.0)))
    cells: list[tuple[float, float, int]] = []
    source_count = 0
    source_sum_x = 0.0
    source_sum_y = 0.0
    for gy in range(grid):
        sy = roi_bottom + (gy + 0.5) * (roi_top - roi_bottom) / grid
        py = min(height - 1, max(0, int(sy)))
        for gx in range(grid):
            sx = roi_left + (gx + 0.5) * (roi_right - roi_left) / grid
            px = min(width - 1, max(0, int(sx)))
            inside = 1 if source_mask[py * width + px] else 0
            source_count += inside
            if inside:
                source_sum_x += sx
                source_sum_y += sy
            cells.append((sx, sy, inside))
    if source_count:
        source_cx = source_sum_x / source_count
        source_cy = source_sum_y / source_count
    else:
        source_cx = left + source_width * 0.5
        source_cy = bottom + source_height * 0.5

    def transform(scale_factor: float, dx: float, dy: float, ax: float, ay: float) -> tuple[float, float, float, float]:
        scale_x = base_scale_x * scale_factor * ax
        scale_y = base_scale_y * scale_factor * ay
        return (
            scale_x,
            scale_y,
            source_cx + dx * size - model_cu * scale_x,
            source_cy + dy * size - model_cv * scale_y,
        )

    def evaluate(parameters: tuple[float, float, float, float, float]) -> tuple[float, float]:
        """(탐색 목적값, IoU). 목적값은 IoU × 모델 커버리지다.

        IoU만 높이면 둥근 모델을 각진 소스에 맞출 때 모델을 소스 밖으로 키워
        "소스의 안 덮인 면적"을 줄이는 쪽으로 간다. 표본은 모델 픽셀이 소스 전경
        위에 놓여야 의미가 있으므로 커버리지를 곱해 그 방향을 막는다.
        """

        scale_x, scale_y, offset_x, offset_y = transform(*parameters)
        intersection = 0
        model_hits = 0
        for sx, sy, inside in cells:
            u = (sx - offset_x) / scale_x
            v = (sy - offset_y) / scale_y
            fx = (min_u + u * model_width) * mask_resolution
            fy = (min_v + v * model_height) * mask_resolution
            # int()는 음수를 0 쪽으로 자르므로 음수 좌표를 0행·0열로 착각하지 않게 먼저 거른다.
            if fx < 0.0 or fy < 0.0:
                continue
            mx = int(fx)
            my = int(fy)
            if mx < mask_resolution and my < mask_resolution and model_mask[my * mask_resolution + mx]:
                model_hits += 1
                intersection += inside
        union = source_count + model_hits - intersection
        iou_value = intersection / union if union > 0 else 0.0
        coverage = intersection / model_hits if model_hits > 0 else 0.0
        return iou_value * coverage, iou_value

    def iou(parameters: tuple[float, float, float, float, float]) -> float:
        return evaluate(parameters)[0]

    # 시작 후보: 경계 상자 스트레치와 모델 비율 유지(균일 맞춤) 중 IoU가 높은 쪽.
    fit = min(source_width / model_width, source_height / model_height)
    starts = [
        (1.0, 0.0, 0.0, 1.0, 1.0),
        (1.0, 0.0, 0.0, fit * model_width / base_scale_x, fit * model_height / base_scale_y),
    ]
    best = starts[0]
    best_score = 0.0
    if source_count and model_count:
        aspect_low, aspect_high = ALIGNMENT_ASPECT_RANGE
        scored = []
        for start in starts:
            if aspect_low <= start[3] <= aspect_high and aspect_low <= start[4] <= aspect_high:
                scored.append((iou(start), start))
        best_score, best = max(scored, key=lambda item: item[0])
        scale_step, offset_step, aspect_step = 0.1, 0.05, 0.12
        for level in range(4):
            # 오프셋과 같은 축의 비율은 함께 움직여야 한다. 발밑 그림자가 무게중심을
            # 끌어내린 경우 세로 이동만으로도, 세로 비율만으로도 IoU가 나빠져
            # 축별 탐색은 국소 최적에 갇힌다. 첫 단계는 넓게, 이후는 좁게 훑는다.
            reach = (-2, -1, 0, 1, 2) if level == 0 else (-1, 0, 1)
            pairs = ((2, 4), (1, 3))
            for offset_axis, aspect_axis in pairs:
                for shift_offset in reach:
                    for shift_aspect in reach:
                        if shift_offset == 0 and shift_aspect == 0:
                            continue
                        candidate = list(best)
                        candidate[offset_axis] += shift_offset * offset_step
                        candidate[aspect_axis] += shift_aspect * aspect_step
                        if not aspect_low <= candidate[aspect_axis] <= aspect_high:
                            continue
                        score = iou(tuple(candidate))  # type: ignore[arg-type]
                        if score > best_score + 1.0e-6:
                            best_score, best = score, tuple(candidate)  # type: ignore[assignment]
            for shift in (-2, -1, 1, 2):
                candidate = list(best)
                candidate[0] += shift * scale_step
                if not 0.5 <= candidate[0] <= 2.0:
                    continue
                score = iou(tuple(candidate))  # type: ignore[arg-type]
                if score > best_score + 1.0e-6:
                    best_score, best = score, tuple(candidate)  # type: ignore[assignment]
            scale_step *= 0.5
            offset_step *= 0.5
            aspect_step *= 0.5
    scale_x, scale_y, offset_x, offset_y = transform(*best)
    # 보고·수용 판정에는 목적값이 아니라 실제 IoU를 쓴다.
    best_score = evaluate(best)[1] if source_count and model_count else 0.0
    alignment = SilhouetteAlignment(scale_x, scale_y, offset_x, offset_y, iou=best_score)
    if not warp or not source_count or not model_count:
        return alignment

    model_bbox_pixels = (
        int(min_u * mask_resolution),
        int(min_v * mask_resolution),
        min(mask_resolution, int(math.ceil(max_u * mask_resolution)) + 1),
        min(mask_resolution, int(math.ceil(max_v * mask_resolution)) + 1),
    )
    model_rows, _model_columns = _mask_runs(model_mask, mask_resolution, mask_resolution, model_bbox_pixels)
    source_rows, _source_columns = _mask_runs(source_mask, width, height, source_bbox)

    def to_source_x(mx: float) -> float:
        return offset_x + ((mx / mask_resolution - min_u) / model_width) * scale_x

    def model_row_runs(source_y: float):
        v = (source_y - offset_y) / scale_y
        fy = (min_v + v * model_height) * mask_resolution
        if fy < 0.0:
            return None
        my = int(fy)
        if my >= mask_resolution:
            return None
        runs = model_rows[my]
        if not runs:
            return None
        return [(to_source_x(run[0]), to_source_x(run[1])) for run in runs]

    def source_row_runs(source_y: float):
        py = int(source_y)
        if not 0 <= py < height:
            return None
        runs = source_rows[py]
        if not runs:
            return None
        return [(float(run[0]), float(run[1])) for run in runs]

    # 열 워프는 만들지 않는다. 열이 지나는 부위(가랑이~머리, 발~머리, 손~어깨)에
    # 따라 극값이 수십 px씩 달라져 몸통 중앙을 위아래로 흔들었다. 행 워프만
    # 구간이 1:1 대응하는 줄에 한해 둔다.
    row_range = (float(bottom), float(top))
    subject_size = float(max(right - left, top - bottom))
    row_warp = _build_warp_table(
        min(WARP_TABLE_LIMIT, top - bottom), row_range, model_row_runs, source_row_runs, subject_size
    )
    return SilhouetteAlignment(
        scale_x,
        scale_y,
        offset_x,
        offset_y,
        row_warp=row_warp,
        row_range=row_range,
        iou=best_score,
    )


def _model_mask_in_source(
    model_mask: Sequence[int],
    mask_resolution: int,
    model_bbox_in_mask: tuple[float, float, float, float],
    alignment: SilhouetteAlignment,
    width: int,
    height: int,
) -> bytearray:
    """모델 실루엣을 정합 변환(워프 포함)으로 소스 픽셀 공간에 찍는다.

    워프는 역변환이 닫힌 식이 아니므로 마스크 픽셀을 앞방향으로 뿌린다. 마스크
    한 픽셀이 소스 여러 픽셀에 해당하면 그 발자국만큼 블록으로 채워 구멍을 막는다.
    """

    min_u, min_v, max_u, max_v = model_bbox_in_mask
    model_width = max(1.0e-8, max_u - min_u)
    model_height = max(1.0e-8, max_v - min_v)
    result = bytearray(width * height)
    step = max(1, mask_resolution // 256)
    # 워프가 늘린 행·열에서는 발자국도 그만큼 커야 마스크에 구멍이 나지 않는다.
    row_stretch = max([1.0] + [entry[1] for entry in (alignment.row_warp or ())])
    column_stretch = max([1.0] + [entry[1] for entry in (alignment.column_warp or ())])
    footprint_x = alignment.scale_x / (model_width * mask_resolution) * step * row_stretch
    footprint_y = alignment.scale_y / (model_height * mask_resolution) * step * column_stretch
    half_x = max(0, int(math.ceil(footprint_x * 0.5)))
    half_y = max(0, int(math.ceil(footprint_y * 0.5)))
    for my in range(0, mask_resolution, step):
        row = my * mask_resolution
        v = ((my + 0.5) / mask_resolution - min_v) / model_height
        for mx in range(0, mask_resolution, step):
            if not model_mask[row + mx]:
                continue
            u = ((mx + 0.5) / mask_resolution - min_u) / model_width
            sx, sy = alignment.map_to_source(u, v)
            cx = int(round(sx))
            cy = int(round(sy))
            for y in range(max(0, cy - half_y), min(height, cy + half_y + 1)):
                base = y * width
                for x in range(max(0, cx - half_x), min(width, cx + half_x + 1)):
                    result[base + x] = 1
    return result


def _mask_mean_color(
    pixels: Sequence[float], width: int, height: int, mask: Sequence[int], bbox: BBox
) -> tuple[float, float, float, float] | None:
    """마스크 안 픽셀의 평균색. 경계 상자 안을 성기게 표본해 전 픽셀 루프를 피한다."""

    left, bottom, right, top = bbox
    left, bottom = max(0, left), max(0, bottom)
    right, top = min(width, right), min(height, top)
    step = max(1, int(math.sqrt(max(1, (right - left) * (top - bottom) // 20000))))
    totals = [0.0, 0.0, 0.0, 0.0]
    count = 0
    for y in range(bottom, top, step):
        row = y * width
        for x in range(left, right, step):
            index = row + x
            if mask[index]:
                offset = index * 4
                totals[0] += pixels[offset]
                totals[1] += pixels[offset + 1]
                totals[2] += pixels[offset + 2]
                totals[3] += pixels[offset + 3]
                count += 1
    if count == 0:
        return None
    return totals[0] / count, totals[1] / count, totals[2] / count, totals[3] / count


def prepare_sources(
    sources: Mapping[str, RasterSource],
    depth_buffers: Mapping[str, Sequence[float]],
    depth_resolution: int,
    projected_bboxes: Mapping[str, tuple[float, float, float, float]],
    *,
    silhouette_warp: bool = False,
    report: dict | None = None,
) -> dict[str, RasterSource]:
    """각 생성 이미지를 모델 실루엣에 정합하고 실루엣 밖을 배경으로 확정한다.

    테두리 색 추정만으로는 AI가 그린 그림자·받침대·라벨이 전경으로 남는다.
    깊이 버퍼에서 얻은 모델 실루엣을 정합 변환으로 소스 위에 올리고 약간
    팽창시킨 뒤, 그 밖의 픽셀은 색과 무관하게 배경으로 본다. LEFT처럼 다른
    소스를 빌려 쓰는 뷰는 원본 소스(RIGHT) 기준으로 한 번만 정합한다.
    정합 IoU가 낮으면 변환도 마스크 정제도 쓰지 않고 경계 상자 경로로 남긴다.
    """

    prepared: dict[str, RasterSource] = {}
    for name, source in sources.items():
        depth_buffer = depth_buffers.get(name)
        bbox = projected_bboxes.get(name)
        if depth_buffer is None or bbox is None:
            prepared[name] = source
            continue
        width, height = source.width, source.height
        coarse = source.foreground
        if coarse is None:
            coarse = coarse_foreground_mask(
                source.pixels, width, height, source.background, source.background_threshold
            )
        model_mask = build_model_mask(depth_buffer, depth_resolution)
        alignment = align_silhouette(
            model_mask,
            depth_resolution,
            bbox,
            coarse,
            width,
            height,
            source.subject_bbox,
            warp=silhouette_warp,
        )
        accepted = alignment.iou >= ALIGNMENT_MIN_IOU
        left, bottom, right, top = source.subject_bbox
        margin = max(3, int(math.ceil(MODEL_MASK_MARGIN_RATIO * max(right - left, top - bottom))))
        model_in_source = _model_mask_in_source(model_mask, depth_resolution, bbox, alignment, width, height)
        if report is not None:
            # 정합이 거부된 소스도 지표는 남긴다. 거부 자체가 강한 불일치 신호다.
            match = evaluate_silhouette_match(
                model_in_source, width, height, coarse, iou=alignment.iou, margin=margin
            )
            report[name] = {
                "scale": [round(alignment.scale_x, 2), round(alignment.scale_y, 2)],
                "offset": [round(alignment.offset_x, 2), round(alignment.offset_y, 2)],
                "iou": round(alignment.iou, 4),
                "warped": bool(alignment.row_warp or alignment.column_warp),
                "accepted": accepted,
                **match,
                "passed": accepted and silhouette_match_passed(match),
            }
        if not accepted:
            prepared[name] = source
            continue
        refined = _mask_and(coarse, dilate_mask(model_in_source, width, height, margin))
        if refined == coarse:
            # 실루엣 밖에 아무것도 없으면 확장 버퍼를 다시 만들 이유가 없다.
            prepared[name] = replace(source, alignment=alignment)
            continue
        fallback = _mask_mean_color(source.pixels, width, height, refined, source.subject_bbox)
        if fallback is None:
            prepared[name] = replace(source, alignment=alignment)
            continue
        filled, foreground_distance = extend_foreground_mask(
            source.pixels, width, height, refined, _extend_radius(source.subject_bbox)
        )
        prepared[name] = replace(
            source,
            fallback_color=fallback,
            filled=filled,
            foreground_distance=foreground_distance,
            alignment=alignment,
            foreground=refined,
        )
    return prepared


def erode_mask(mask: Sequence[int], width: int, height: int, radius: int) -> bytearray:
    """마스크(1=전경)를 사각 반경 ``radius``만큼 침식한다. 배경을 팽창시킨 뒤 뒤집는다."""

    if radius <= 0:
        return bytearray(mask)
    inverted = bytearray(1 if value == 0 else 0 for value in mask)
    grown = dilate_mask(inverted, width, height, radius)
    return bytearray(1 if value == 0 else 0 for value in grown)


def evaluate_silhouette_match(
    model_in_source: Sequence[int],
    width: int,
    height: int,
    foreground: Sequence[int],
    *,
    iou: float,
    margin: int = 3,
) -> dict:
    """정합된 모델 실루엣과 생성 전경 마스크의 내부 구조 일치도를 잰다.

    ``coverage``는 모델 픽셀 중 생성 전경 위에 놓인 비율,
    ``segment_mismatch_ratio``는 모델이 있는 행 가운데 연속 구간 수가 생성 전경과
    다른 행의 비율(팔을 벌린 모델 위에 팔을 붙여 그린 그림이면 3구간 대 1구간),
    ``bright_hole_ratio``는 모델 실루엣 안쪽 깊숙이(가장자리 ``margin`` 침식 뒤)
    생성 그림이 배경인 픽셀 비율이다. 외곽 IoU는 이런 자세 차이를 못 잡는다.
    """

    size = width * height
    model_pixels = 0
    covered = 0
    for index in range(size):
        if model_in_source[index]:
            model_pixels += 1
            if foreground[index]:
                covered += 1
    if model_pixels == 0:
        return {
            "coverage": 0.0,
            "segment_mismatch_ratio": 1.0,
            "bright_hole_ratio": 1.0,
            "iou": round(iou, 4),
        }
    interior = erode_mask(model_in_source, width, height, max(1, margin))
    interior_pixels = 0
    holes = 0
    for index in range(size):
        if interior[index]:
            interior_pixels += 1
            if not foreground[index]:
                holes += 1
    bbox = (0, 0, width, height)
    model_rows, _columns = _mask_runs(model_in_source, width, height, bbox)
    source_rows, _columns = _mask_runs(foreground, width, height, bbox)
    min_run = max(2, int(round(width * 0.01)))
    counted = 0
    mismatched = 0
    for y in range(height):
        model_runs = [run for run in (model_rows[y] or ()) if run[1] - run[0] >= min_run]
        if not model_runs:
            continue
        counted += 1
        left = model_runs[0][0] - margin
        right = model_runs[-1][1] + margin
        source_runs = [
            run
            for run in (source_rows[y] or ())
            if run[1] - run[0] >= min_run and run[1] > left and run[0] < right
        ]
        if len(source_runs) != len(model_runs):
            mismatched += 1
    return {
        "coverage": round(covered / model_pixels, 4),
        "segment_mismatch_ratio": round(mismatched / counted, 4) if counted else 1.0,
        "bright_hole_ratio": round(holes / interior_pixels, 4) if interior_pixels else 0.0,
        "iou": round(iou, 4),
    }


def silhouette_match_passed(report: Mapping[str, object]) -> bool:
    """실루엣 지표가 모두 상한 안이면 True."""

    return (
        float(report.get("segment_mismatch_ratio", 1.0)) <= SILHOUETTE_SEGMENT_MISMATCH_LIMIT
        and float(report.get("bright_hole_ratio", 1.0)) <= SILHOUETTE_HOLE_LIMIT
    )


def compare_rendered_view(
    rendered_pixels: Sequence[float],
    width: int,
    height: int,
    source: RasterSource,
    model_mask: Sequence[int],
    mask_resolution: int,
    model_bbox_in_mask: tuple[float, float, float, float],
    *,
    max_samples: int = 20000,
) -> dict:
    """베이크한 모델 렌더와 생성 그림을 같은 시점에서 비교한 참고 지표.

    렌더는 깊이 버퍼와 같은 직교 프레임이므로 모델 마스크 픽셀이 곧 렌더 좌표다.
    같은 픽셀을 정합 변환으로 생성 그림에 옮겨 선형 RGB 평균 절대차를 재고,
    ``score = clamp(1 - 4·오차) × 실루엣 IoU``로 0~1 점수를 만든다.
    """

    min_u, min_v, max_u, max_v = model_bbox_in_mask
    model_width = max(1.0e-8, max_u - min_u)
    model_height = max(1.0e-8, max_v - min_v)
    total = sum(1 for value in model_mask if value)
    step = max(1, int(math.sqrt(max(1, total // max_samples))))
    foreground = source.foreground
    alignment = source.alignment
    left, bottom, right, top = source.subject_bbox
    error_sum = 0.0
    matched = 0
    visited = 0
    for my in range(0, mask_resolution, step):
        row = my * mask_resolution
        v = (my + 0.5) / mask_resolution
        for mx in range(0, mask_resolution, step):
            if not model_mask[row + mx]:
                continue
            visited += 1
            u = (mx + 0.5) / mask_resolution
            render_x = min(width - 1, max(0, int(u * width)))
            render_y = min(height - 1, max(0, int(v * height)))
            normalized_u = _clamp((u - min_u) / model_width)
            normalized_v = _clamp((v - min_v) / model_height)
            if alignment is None:
                sx = left + normalized_u * max(0, right - left - 1)
                sy = bottom + normalized_v * max(0, top - bottom - 1)
            else:
                sx, sy = alignment.map_to_source(normalized_u, normalized_v)
            px = min(source.width - 1, max(0, int(round(sx))))
            py = min(source.height - 1, max(0, int(round(sy))))
            source_index = py * source.width + px
            if foreground is not None and not foreground[source_index]:
                continue
            render_offset = (render_y * width + render_x) * 4
            source_offset = source_index * 4
            error_sum += (
                abs(rendered_pixels[render_offset] - source.pixels[source_offset])
                + abs(rendered_pixels[render_offset + 1] - source.pixels[source_offset + 1])
                + abs(rendered_pixels[render_offset + 2] - source.pixels[source_offset + 2])
            ) / 3.0
            matched += 1
    mean_error = error_sum / matched if matched else 1.0
    coverage = matched / visited if visited else 0.0
    silhouette_iou = alignment.iou if alignment is not None else coverage
    score = _clamp(1.0 - mean_error * 4.0) * _clamp(silhouette_iou)
    return {
        "mean_color_error": round(mean_error, 4),
        "silhouette_iou": round(silhouette_iou, 4),
        "coverage": round(coverage, 4),
        "sample_count": matched,
        "score": round(score, 4),
    }


def compose_verification_sheet(
    rows: Sequence[Sequence[tuple[Sequence[float], int, int] | None]],
    cell_size: int,
    background: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> tuple[bytearray, int, int]:
    """(픽셀, 폭, 높이) 셀을 격자로 배치한 sRGB RGBA 바이트를 만든다. 첫 행이 위쪽.

    셀 픽셀은 파일에서 읽은 인코딩 값(0~1)이어야 한다. 표시용 시트라 색 변환 없이
    최근접 표본으로 크기만 맞춘다(수백만 픽셀을 순수 파이썬으로 돌리므로 보간은 생략).
    """

    if not rows or not rows[0]:
        raise ValueError("검증 시트에 넣을 셀이 없습니다.")
    columns = max(len(row) for row in rows)
    sheet_width = columns * cell_size
    sheet_height = len(rows) * cell_size
    base = bytes((int(round(_clamp(background[0]) * 255)), int(round(_clamp(background[1]) * 255)), int(round(_clamp(background[2]) * 255)), 255))
    rgba = bytearray(base * (sheet_width * sheet_height))
    for row_index, row in enumerate(rows):
        origin_y = (len(rows) - 1 - row_index) * cell_size
        for column_index, cell in enumerate(row):
            if cell is None:
                continue
            pixels, width, height = cell
            origin_x = column_index * cell_size
            scale_x = width / cell_size
            scale_y = height / cell_size
            column_offsets = [
                min(width - 1, int((x + 0.5) * scale_x)) * 4 for x in range(cell_size)
            ]
            for y in range(cell_size):
                source_row = min(height - 1, int((y + 0.5) * scale_y)) * width * 4
                target_row = (origin_y + y) * sheet_width
                for x in range(cell_size):
                    source_offset = source_row + column_offsets[x]
                    offset = (target_row + origin_x + x) * 4
                    rgba[offset] = int(_clamp(pixels[source_offset]) * 255.0 + 0.5)
                    rgba[offset + 1] = int(_clamp(pixels[source_offset + 1]) * 255.0 + 0.5)
                    rgba[offset + 2] = int(_clamp(pixels[source_offset + 2]) * 255.0 + 0.5)
                    rgba[offset + 3] = 255
    return rgba, sheet_width, sheet_height


def _aligned_source_sample(
    source: RasterSource,
    projected: tuple[float, float, float],
    projected_bbox: tuple[float, float, float, float],
    *,
    mirror_x: bool = False,
    vertical_fallback: int = 0,
) -> tuple[tuple[float, float, float, float], float] | None:
    """표본 색과 신뢰도(0.05~1.0)를 돌려준다.

    신뢰도는 생성 실루엣 안이면 1.0이고, 실루엣 밖으로 멀어질수록 낮아진다.
    경계에서 다른 뷰로 부드럽게 넘어가도록 호출부가 가중치에 곱한다.
    """

    target_x, target_y = _source_coordinates(
        source, projected, projected_bbox, mirror_x=mirror_x, vertical_fallback=vertical_fallback
    )
    pixel_x = min(source.width - 1, max(0, int(round(target_x))))
    pixel_y = min(source.height - 1, max(0, int(round(target_y))))
    distance = source.foreground_distance[pixel_y * source.width + pixel_x]
    # 생성 실루엣이 모델보다 좁으면 표본이 배경으로 나간다. 가까우면 최근접
    # 전경 색으로 이어 붙이되 신뢰도를 거리만큼 낮추고, 한계에 닿으면 다른
    # 시점에 완전히 양보한다. 하한을 두면 배경 쪽 표본이 다른 시점을 이긴다.
    left, bottom, right, top = source.subject_bbox
    limit = max(4.0, FOREGROUND_EXTEND_RATIO * max(right - left, top - bottom))
    if distance <= 0.0:
        confidence = 1.0
    elif distance >= limit:
        return None
    else:
        confidence = 1.0 - distance / limit
    return (
        bilinear_sample(source.filled, source.width, source.height, target_x, target_y),
        confidence,
    )


def _source_coordinates(
    source: RasterSource,
    projected: tuple[float, float, float],
    projected_bbox: tuple[float, float, float, float],
    *,
    mirror_x: bool = False,
    vertical_fallback: int = 0,
) -> tuple[float, float]:
    """투영 좌표를 생성 이미지 픽셀 좌표로 옮긴다(정합 변환 또는 경계 상자 스트레치)."""

    u, v = projected[0], projected[1]
    min_u, min_v, max_u, max_v = projected_bbox
    normalized_u = _clamp((u - min_u) / max(1.0e-8, max_u - min_u))
    normalized_v = _clamp((v - min_v) / max(1.0e-8, max_v - min_v))
    if mirror_x:
        normalized_u = 1.0 - normalized_u
    if vertical_fallback > 0:
        normalized_v = min(normalized_v, 0.94)
    elif vertical_fallback < 0:
        normalized_v = max(normalized_v, 0.06)
    alignment = source.alignment
    if alignment is None:
        left, bottom, right, top = source.subject_bbox
        return (
            left + normalized_u * max(0, right - left - 1),
            bottom + normalized_v * max(0, top - bottom - 1),
        )
    return alignment.map_to_source(normalized_u, normalized_v)


def _source_for_view(sources: Mapping[str, RasterSource], view: str) -> tuple[str, bool] | None:
    """뷰가 읽을 소스 이름과 좌우 반전 여부를 돌려준다. 소스가 없으면 None."""

    if view in sources:
        return view, False
    fallback = _VIEW_SOURCE_FALLBACKS.get(view)
    if fallback is not None and fallback[0] in sources:
        return fallback
    return None


def _normal_view_weights(
    normal: Vec3, exponent: float = DEFAULT_BLEND_EXPONENT, views: Sequence[str] = VIEW_NAMES
) -> dict[str, float]:
    """법선과 각 뷰 카메라 방향의 코사인을 ``exponent`` 제곱해 가중치로 쓴다."""

    x, y, z = normal
    weights = {}
    for name in views:
        toward = VIEW_SPECS[name].toward_camera
        cosine = x * toward[0] + y * toward[1] + z * toward[2]
        weights[name] = cosine ** exponent if cosine > 0.0 else 0.0
    return weights


def _pixel_view_weights(
    normal: Vec3,
    exponent: float,
    active_views: Sequence[str],
    has_top: bool,
    has_bottom: bool,
) -> tuple[dict[str, float], int]:
    """픽셀 법선의 뷰 가중치와 상·하면 대체 방향(-1/0/1)을 정한다.

    위·아래 소스가 없으면 법선의 수평 성분으로 측면 뷰를 고르고, 위·아래가
    지배적인 면은 측면 이미지의 최상·최하단 색을 끌어오도록 표시한다.
    """

    x, y, z = normal
    vertical_fallback = 0
    if (z > 0.0 and not has_top) or (z < 0.0 and not has_bottom):
        if abs(z) >= max(abs(x), abs(y)):
            vertical_fallback = 1 if z > 0.0 else -1
        horizontal = math.hypot(x, y)
        if horizontal < 1.0e-6:
            return {name: 1.0 for name in active_views if name not in ("TOP", "BOTTOM")}, vertical_fallback
        normal = (x / horizontal, y / horizontal, 0.0)
    return _normal_view_weights(normal, exponent, active_views), vertical_fallback


def _interpolated_normal(
    triangle: BakeTriangle, weights: tuple[float, float, float]
) -> Vec3:
    normals = triangle.vertex_normals
    if normals is None:
        return triangle.normal
    x = weights[0] * normals[0][0] + weights[1] * normals[1][0] + weights[2] * normals[2][0]
    y = weights[0] * normals[0][1] + weights[1] * normals[1][1] + weights[2] * normals[2][1]
    z = weights[0] * normals[0][2] + weights[1] * normals[1][2] + weights[2] * normals[2][2]
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1.0e-9:
        return triangle.normal
    return x / length, y / length, z / length


def _uniform_normal(triangle: BakeTriangle) -> bool:
    normals = triangle.vertex_normals
    return normals is None or (normals[0] == normals[1] == normals[2])


def estimate_view_gains(
    triangles: Sequence[BakeTriangle],
    sources: Mapping[str, RasterSource],
    center: Vec3,
    scale: float,
    depth_buffers: Mapping[str, Sequence[float]],
    depth_resolution: int,
    depth_tolerance: float,
    projected_bboxes: Mapping[str, tuple[float, float, float, float]],
) -> dict[str, tuple[float, float, float]]:
    """뷰 간 밝기 차이를 FRONT 기준 휘도 gain으로 추정한다.

    AI가 칸마다 명도를 다르게 그리면 접합부에 띠가 생긴다. FRONT와 함께 보는
    표면(삼각형 중심과 변 중점)에서 두 뷰의 표본 휘도 비율을 모아 중앙값을
    gain으로 쓴다. 표본이 적거나 비율이 흩어진 뷰는 보정하지 않는다.
    """

    return _estimate_view_gains(
        triangles, sources, center, scale, depth_buffers, depth_resolution, depth_tolerance, projected_bboxes
    )[0]


def _interior_mask(source: RasterSource) -> tuple[bytearray | None, int]:
    """전경 마스크를 안쪽으로 침식한 마스크와 침식 폭. 작은 소스는 침식하지 않는다."""

    mask = source.foreground
    if mask is None:
        return None, 0
    left, bottom, right, top = source.subject_bbox
    margin = min(VIEW_GAIN_INTERIOR_MARGIN, max(0, min(right - left, top - bottom) // 8))
    if margin <= 0:
        return mask, 0
    inverted = bytearray(1 if value == 0 else 0 for value in mask)
    grown = dilate_mask(inverted, source.width, source.height, margin)
    return bytearray(1 if value == 0 else 0 for value in grown), margin


def _estimate_view_gains(
    triangles: Sequence[BakeTriangle],
    sources: Mapping[str, RasterSource],
    center: Vec3,
    scale: float,
    depth_buffers: Mapping[str, Sequence[float]],
    depth_resolution: int,
    depth_tolerance: float,
    projected_bboxes: Mapping[str, tuple[float, float, float, float]],
) -> tuple[dict[str, tuple[float, float, float]], dict[tuple[str, str], int]]:
    """gain과 함께 뷰 쌍별 표본 수를 돌려준다. 표본 부족과 겹침 없음을 구분하기 위함."""

    gains: dict[str, tuple[float, float, float]] = {name: (1.0, 1.0, 1.0) for name in VIEW_NAMES}
    pair_counts: dict[tuple[str, str], int] = {}
    # 자기 소스가 있는 뷰만 독립 추정한다. LEFT처럼 소스를 빌리는 뷰는 같은 이미지에
    # 두 가지 보정이 걸리지 않도록 원본 뷰의 gain을 그대로 물려받는다.
    active_views = [name for name in VIEW_NAMES if name in sources and name in depth_buffers]
    if "FRONT" not in active_views:
        return gains, pair_counts
    # 체인 전파는 하지 않는다. RIGHT→BACK→LEFT로 이어 붙이면 정합 오차가 누적되어
    # 실제 밝기 차가 2% 이내인데도 상한·하한까지 포화되는 gain이 나온다.
    others = [name for name in active_views if name != "FRONT"]
    interiors = {name: _interior_mask(sources[name]) for name in active_views}
    sample_weights = ((1 / 3, 1 / 3, 1 / 3), (0.5, 0.5, 0.0), (0.0, 0.5, 0.5), (0.5, 0.0, 0.5))
    ratios: dict[str, list[float]] = {name: [] for name in others}

    def interior_sample(view: str, projected) -> float | None:
        """정합 실루엣 안쪽 깊숙한 곳에서 완전히 보이는 표본의 휘도. 아니면 None."""

        if _visibility_weight(projected, depth_buffers[view], depth_resolution, depth_tolerance) < 0.99:
            return None
        source = sources[view]
        bbox = projected_bboxes[view]
        sample = _aligned_source_sample(source, projected, bbox)
        if sample is None or sample[1] < 0.999:
            return None
        interior, _margin = interiors[view]
        if interior is not None:
            x, y = _source_coordinates(source, projected, bbox)
            px = min(source.width - 1, max(0, int(round(x))))
            py = min(source.height - 1, max(0, int(round(y))))
            if not interior[py * source.width + px]:
                return None
        return _luminance(sample[0])

    front_toward = VIEW_SPECS["FRONT"].toward_camera
    for triangle in triangles:
        for weights in sample_weights:
            normal = _interpolated_normal(triangle, weights)
            # 지수 적용 전 코사인 기준으로 고정한다. 블렌딩 지수를 적용한 값으로
            # 임계를 두면 지수가 클 때 45도 면조차 두 뷰를 동시에 만족하지 못한다.
            if normal[0] * front_toward[0] + normal[1] * front_toward[1] + normal[2] * front_toward[2] < 0.3:
                continue
            position = tuple(
                weights[0] * triangle.positions[0][axis]
                + weights[1] * triangle.positions[1][axis]
                + weights[2] * triangle.positions[2][axis]
                for axis in range(3)
            )
            front_luminance = None
            for view in others:
                toward = VIEW_SPECS[view].toward_camera
                if normal[0] * toward[0] + normal[1] * toward[1] + normal[2] * toward[2] < 0.3:
                    continue
                if front_luminance is None:
                    front_luminance = interior_sample("FRONT", project_point(position, "FRONT", center, scale))
                    if front_luminance is None:
                        break
                other_luminance = interior_sample(view, project_point(position, view, center, scale))
                if other_luminance is None:
                    continue
                pair_counts[("FRONT", view)] = pair_counts.get(("FRONT", view), 0) + 1
                if front_luminance > 0.02 and other_luminance > 0.02:
                    ratios[view].append(front_luminance / other_luminance)

    low, high = VIEW_GAIN_RANGE
    for view, values in ratios.items():
        if len(values) < VIEW_GAIN_MIN_SAMPLES:
            continue
        values.sort()
        count = len(values)
        first_quartile = values[count // 4]
        third_quartile = values[(count * 3) // 4]
        if third_quartile - first_quartile > VIEW_GAIN_MAX_IQR:
            continue
        gain = min(high, max(low, values[count // 2]))
        gains[view] = (gain, gain, gain)
    for name in VIEW_NAMES:
        if name in sources:
            continue
        borrowed = _source_for_view(sources, name)
        if borrowed is not None:
            gains[name] = gains[borrowed[0]]
    return gains, pair_counts


def rasterize_atlas(
    triangles: Sequence[BakeTriangle],
    sources: Mapping[str, RasterSource],
    resolution: int,
    padding: int,
    center: Vec3,
    scale: float,
    *,
    blend_exponent: float = DEFAULT_BLEND_EXPONENT,
    harmonize_colors: bool = True,
    silhouette_warp: bool = False,
    unpainted_color: tuple[float, float, float] | None = None,
    require_all_sources: bool = True,
    allow_view_substitution: bool = True,
) -> tuple[bytearray, dict]:
    """삼각형을 UV 공간에 래스터화하고 생성 뷰 색을 투영한다.

    ``unpainted_color``가 있으면 어떤 소스에서도 색을 얻지 못한 픽셀을 평균색
    대신 이 선형 색으로 칠한다. 순차 생성 모드는 이 회색 영역을 "아직 칠하지
    않은 곳"으로 다음 시점 가이드에 드러내야 한다. ``require_all_sources``를
    끄면 FRONT 한 장처럼 일부 소스만으로도 부분 베이크한다.
    ``allow_view_substitution``을 끄면 LEFT의 RIGHT 미러 대체와 TOP/BOTTOM의
    측면 색 늘리기 대체를 모두 막아, 소스가 있는 뷰가 직접 보는 면만 칠한다.
    순차 모드의 다음 시점 가이드에 미채색 영역이 실제로 남아야 하기 때문이다.
    """

    if not triangles:
        raise ValueError("Atlas에 투영할 삼각형이 없습니다.")
    if resolution < 16 or resolution > MAX_ATLAS_RESOLUTION:
        raise ValueError(
            f"텍스처 해상도는 16~{MAX_ATLAS_RESOLUTION}px만 지원합니다. "
            "8192px가 필요하면 우선 4096px로 생성한 뒤 업스케일해 주세요."
        )
    if require_all_sources:
        missing = [name for name in REQUIRED_SOURCE_VIEW_NAMES if name not in sources]
        if missing:
            raise ValueError(f"생성 뷰 이미지가 없습니다: {', '.join(missing)}")
    elif not sources:
        raise ValueError("투영할 생성 뷰 이미지가 한 장도 없습니다.")
    unknown = [name for name in sources if name not in VIEW_SPECS]
    if unknown:
        raise ValueError(f"지원하지 않는 생성 뷰입니다: {', '.join(unknown)}")
    if blend_exponent <= 0.0:
        raise ValueError("뷰 블렌딩 지수는 0보다 커야 합니다.")
    if not allow_view_substitution and unpainted_color is None:
        raise ValueError("뷰 대체를 끄려면 미채색 영역을 칠할 unpainted_color가 필요합니다.")

    # 소스가 있거나 대체 소스가 있는 뷰만 투영·가림 계산 대상이다.
    if allow_view_substitution:
        view_sources = {
            name: resolved
            for name in VIEW_NAMES
            if (resolved := _source_for_view(sources, name)) is not None
        }
        has_top, has_bottom = "TOP" in sources, "BOTTOM" in sources
    else:
        view_sources = {name: (name, False) for name in VIEW_NAMES if name in sources}
        # 대체를 막을 때는 위·아래 소스가 없어도 측면 뷰로 눕히지 않는다.
        has_top = has_bottom = True
    active_views = tuple(view_sources)
    projected_bboxes = {name: _projected_bbox(triangles, name, center, scale) for name in active_views}
    source_limit = max(max(source.width, source.height) for source in sources.values())
    depth_resolution = min(1024, max(256, min(resolution, source_limit)))
    depth_buffers = {
        name: build_depth_buffer(triangles, name, center, scale, depth_resolution)
        for name in active_views
    }
    depth_tolerance = max(scale / depth_resolution * 3.0, scale * 1.0e-4)
    alignment_report: dict = {}
    sources = prepare_sources(
        sources,
        depth_buffers,
        depth_resolution,
        projected_bboxes,
        silhouette_warp=silhouette_warp,
        report=alignment_report,
    )
    if harmonize_colors:
        gains = estimate_view_gains(
            triangles,
            sources,
            center,
            scale,
            depth_buffers,
            depth_resolution,
            depth_tolerance,
            projected_bboxes,
        )
    else:
        gains = {name: (1.0, 1.0, 1.0) for name in VIEW_NAMES}
    # 픽셀 루프에서 딕셔너리 조회를 줄이기 위해 뷰별 정보를 튜플로 푼다.
    view_table = tuple(
        (
            name,
            VIEW_SPECS[name].right,
            VIEW_SPECS[name].up,
            VIEW_SPECS[name].toward_camera,
            sources[view_sources[name][0]],
            view_sources[name][1],
            depth_buffers[name],
            projected_bboxes[name],
            gains[name],
        )
        for name in active_views
    )
    rgba = bytearray(resolution * resolution * 4)
    occupied = bytearray(resolution * resolution)
    filled_pixels = 0
    occluded_samples = 0
    occluded_fallback_pixels = 0
    fallback_pixels = 0
    unpainted_pixels = 0
    unpainted_rgba = None
    marker_weight = 0.0
    if unpainted_color is not None:
        unpainted_rgba = (float(unpainted_color[0]), float(unpainted_color[1]), float(unpainted_color[2]), 1.0)
        if not allow_view_substitution:
            # 대체 없는 부분 베이크는 회색 마커를 가상 뷰처럼 함께 섞는다. 그래야
            # 비스듬한 시점의 미세한 가중치가 픽셀을 독점해 늘어진 색을 칠하지 않고,
            # 가려진 면도 뒤쪽 시점 색을 끌어오지 않는다.
            marker_weight = math.cos(math.radians(UNPAINTED_MARKER_ANGLE_DEGREES)) ** blend_exponent
    inverse_scale = 1.0 / scale

    for triangle in triangles:
        uv_screen = tuple((uv[0], uv[1]) for uv in triangle.uvs)
        bounds = _screen_raster_bounds(uv_screen, resolution, resolution)
        if bounds[0] > bounds[2] or bounds[1] > bounds[3]:
            continue
        positions = triangle.positions
        uniform_normal = _uniform_normal(triangle)
        if uniform_normal:
            view_weights, vertical_fallback = _pixel_view_weights(
                triangle.normal, blend_exponent, active_views, has_top, has_bottom
            )
        for y in range(bounds[1], bounds[3] + 1):
            uv_y = (y + 0.5) / resolution
            for x in range(bounds[0], bounds[2] + 1):
                pixel_index = y * resolution + x
                if occupied[pixel_index] == 1:
                    continue
                uv_x = (x + 0.5) / resolution
                weights = barycentric_weights((uv_x, uv_y), uv_screen[0], uv_screen[1], uv_screen[2])
                if weights is None:
                    continue
                w0, w1, w2 = weights
                px = w0 * positions[0][0] + w1 * positions[1][0] + w2 * positions[2][0] - center[0]
                py = w0 * positions[0][1] + w1 * positions[1][1] + w2 * positions[2][1] - center[1]
                pz = w0 * positions[0][2] + w1 * positions[1][2] + w2 * positions[2][2] - center[2]
                if not uniform_normal:
                    view_weights, vertical_fallback = _pixel_view_weights(
                        _interpolated_normal(triangle, weights),
                        blend_exponent,
                        active_views,
                        has_top,
                        has_bottom,
                    )
                colors: list[tuple[tuple[float, float, float, float], float]] = []
                occluded_views: list[tuple] = []
                for name, right, up, toward, source, mirror_x, depth_buffer, bbox, gain in view_table:
                    view_weight = view_weights.get(name, 0.0)
                    if view_weight <= 1.0e-8:
                        continue
                    projected = (
                        0.5 + (px * right[0] + py * right[1] + pz * right[2]) * inverse_scale,
                        0.5 + (px * up[0] + py * up[1] + pz * up[2]) * inverse_scale,
                        px * toward[0] + py * toward[1] + pz * toward[2],
                    )
                    # 상·하단 대체 표본도 가림을 같은 방식으로 따진다. 여기서만 가림을
                    # 건너뛰면 법선이 수직 기준을 넘는 순간 가중치가 튀어 경계가 다시 생긴다.
                    visibility = _visibility_weight(
                        projected, depth_buffer, depth_resolution, depth_tolerance
                    )
                    if visibility <= 0.0:
                        occluded_samples += 1
                        occluded_views.append(
                            (source, projected, bbox, mirror_x, view_weight, gain, vertical_fallback)
                        )
                        continue
                    sample = _aligned_source_sample(
                        source,
                        projected,
                        bbox,
                        mirror_x=mirror_x,
                        vertical_fallback=vertical_fallback,
                    )
                    if sample is not None:
                        color, confidence = sample
                        colors.append(
                            (
                                (color[0] * gain[0], color[1] * gain[1], color[2] * gain[2], color[3]),
                                view_weight * visibility * confidence,
                            )
                        )
                total_weight = sum(item[1] for item in colors)
                if marker_weight > 0.0:
                    if marker_weight >= total_weight:
                        unpainted_pixels += 1
                    colors.append((unpainted_rgba, marker_weight))
                    total_weight += marker_weight
                if total_weight < 1.0e-4 and occluded_views:
                    # 다리 안쪽처럼 모든 시점에서 가려진 면은 전체 평균색을 칠하면
                    # 텍스처가 빠진 것처럼 보인다. 가림을 무시하고 같은 시점의 같은
                    # 좌표를 다시 읽어 주변과 이어지는 색을 쓴다. 가중치가 사실상 0인
                    # 표본 하나가 픽셀을 독점하지 않도록 개수가 아니라 가중치 합으로 판단한다.
                    for source, projected, bbox, mirror_x, view_weight, gain, fallback_direction in occluded_views:
                        sample = _aligned_source_sample(
                            source, projected, bbox, mirror_x=mirror_x, vertical_fallback=fallback_direction
                        )
                        if sample is not None:
                            color, confidence = sample
                            colors.append(
                                (
                                    (color[0] * gain[0], color[1] * gain[1], color[2] * gain[2], color[3]),
                                    view_weight * confidence,
                                )
                            )
                    total_weight = sum(item[1] for item in colors)
                    if total_weight >= 1.0e-4:
                        occluded_fallback_pixels += 1
                if total_weight < 1.0e-4:
                    fallback_pixels += 1
                    if unpainted_rgba is not None:
                        unpainted_pixels += 1
                        colors = [(unpainted_rgba, 1.0)]
                    else:
                        # 소스 단위로 가중치를 모아 각 생성 이미지의 전경 평균색을 섞는다.
                        # 주변 픽셀과 같은 gain을 곱해야 폴백 픽셀만 원래 색조로 튀지 않는다.
                        source_weights: dict[str, float] = {}
                        for name, (source_name, _mirror) in view_sources.items():
                            source_weights[source_name] = source_weights.get(source_name, 0.0) + view_weights.get(name, 0.0)
                        colors = []
                        for source_name, weight in source_weights.items():
                            base = sources[source_name].fallback_color
                            gain = gains[source_name]
                            colors.append(
                                (
                                    (base[0] * gain[0], base[1] * gain[1], base[2] * gain[2], base[3]),
                                    max(0.001, weight),
                                )
                            )
                    total_weight = sum(item[1] for item in colors)
                color = tuple(
                    sum(item[0][channel] * item[1] for item in colors) / total_weight
                    for channel in range(4)
                )
                offset = pixel_index * 4
                rgba[offset] = _linear_to_srgb_byte(color[0])
                rgba[offset + 1] = _linear_to_srgb_byte(color[1])
                rgba[offset + 2] = _linear_to_srgb_byte(color[2])
                rgba[offset + 3] = 255
                occupied[pixel_index] = 1
                filled_pixels += 1

    if filled_pixels == 0:
        raise ValueError("UV가 0~1 Atlas 영역에 없어 텍스처를 만들 수 없습니다.")
    dilated_pixels = dilate_rgba(rgba, occupied, resolution, resolution, padding)
    return rgba, {
        "filled_pixels": filled_pixels,
        "dilated_pixels": dilated_pixels,
        "occluded_samples": occluded_samples,
        "occluded_fallback_pixels": occluded_fallback_pixels,
        "fallback_pixels": fallback_pixels,
        "unpainted_pixels": unpainted_pixels,
        "depth_resolution": depth_resolution,
        "view_gains": {name: [round(value, 4) for value in gains[name]] for name in active_views},
        "view_alignment": alignment_report,
    }


def _source_statistics(pixels: Sequence[float], width: int, height: int, bbox: BBox) -> tuple[
    tuple[float, float, float, float], float, tuple[float, float, float, float]
]:
    border: list[tuple[float, float, float, float]] = []
    stride = max(1, min(width, height) // 256)

    def read(x: int, y: int) -> tuple[float, float, float, float]:
        offset = (y * width + x) * 4
        return tuple(float(pixels[offset + channel]) for channel in range(4))  # type: ignore[return-value]

    for x in range(0, width, stride):
        border.extend((read(x, 0), read(x, height - 1)))
    for y in range(0, height, stride):
        border.extend((read(0, y), read(width - 1, y)))
    background_values = []
    for channel in range(4):
        values = sorted(color[channel] for color in border)
        background_values.append(values[len(values) // 2])
    background = tuple(background_values)  # type: ignore[assignment]
    deviations = sorted(
        max(abs(color[channel] - background[channel]) for channel in range(3))
        for color in border
    )
    threshold = min(0.30, max(0.055, deviations[len(deviations) // 2] * 4.0 + 0.035))

    left, bottom, right, top = bbox
    sample_step = max(1, int(math.sqrt(max(1, (right - left) * (top - bottom) // 20000))))
    totals = [0.0, 0.0, 0.0, 0.0]
    count = 0
    for y in range(bottom, top, sample_step):
        for x in range(left, right, sample_step):
            color = read(x, y)
            if background[3] < 0.25:
                foreground = color[3] > 0.12
            else:
                foreground = max(abs(color[channel] - background[channel]) for channel in range(3)) > threshold * 0.55
            if foreground:
                for channel in range(4):
                    totals[channel] += color[channel]
                count += 1
    fallback = tuple(value / count for value in totals) if count else (0.5, 0.5, 0.5, 1.0)
    return background, threshold, fallback  # type: ignore[return-value]


def _resolve_view_paths(
    view_paths: Mapping[str, str | os.PathLike[str]] | Sequence[str | os.PathLike[str]],
    *,
    require_all: bool = True,
) -> dict[str, Path]:
    """뷰 이름 → 파일 경로를 정규화한다.

    시퀀스는 3장(FRONT/RIGHT/BACK) 또는 6장(FRONT/RIGHT/BACK/LEFT/TOP/BOTTOM)
    순서로 해석하고, 매핑은 대소문자를 무시한 뷰 이름 키로 받는다.
    ``require_all``을 끄면 필수 3장이 없어도 있는 뷰만 돌려준다.
    """

    if isinstance(view_paths, Mapping):
        resolved = {str(name).upper(): Path(path).expanduser().resolve() for name, path in view_paths.items()}
    else:
        if len(view_paths) == len(REQUIRED_SOURCE_VIEW_NAMES):
            names = REQUIRED_SOURCE_VIEW_NAMES
        elif len(view_paths) == len(VIEW_NAMES):
            names = VIEW_NAMES
        else:
            raise ValueError(
                "생성 이미지는 FRONT/RIGHT/BACK 3장 또는 FRONT/RIGHT/BACK/LEFT/TOP/BOTTOM 6장이어야 합니다."
            )
        resolved = {name: Path(path).expanduser().resolve() for name, path in zip(names, view_paths)}
    unknown = [name for name in resolved if name not in VIEW_SPECS]
    if unknown:
        raise ValueError(f"지원하지 않는 생성 뷰입니다: {', '.join(unknown)}")
    if require_all:
        missing = [name for name in REQUIRED_SOURCE_VIEW_NAMES if name not in resolved]
        if missing:
            raise ValueError(f"생성 뷰 경로가 없습니다: {', '.join(missing)}")
    elif not resolved:
        raise ValueError("생성 뷰 경로가 한 장도 없습니다.")
    for name, path in resolved.items():
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"{name} 생성 이미지를 읽을 수 없습니다: {path}")
    return {name: resolved[name] for name in VIEW_NAMES if name in resolved}


def _resolve_uv_name(obj, uv_layer_names, object_index: int) -> str:
    if isinstance(uv_layer_names, Mapping):
        name = uv_layer_names.get(obj.name) or uv_layer_names.get(obj)
    elif uv_layer_names is not None:
        try:
            name = uv_layer_names[object_index]
        except IndexError as exc:
            raise ValueError(f"{obj.name}: 객체별 UV 레이어 이름이 부족합니다.") from exc
    else:
        active = obj.data.uv_layers.active
        name = active.name if active is not None else ""
    if not name:
        raise ValueError(f"{obj.name}: 사용할 UV 레이어가 없습니다.")
    return str(name)


def _generated_topology(original, evaluated) -> bool:
    """Modifier가 polygon/loop 구성을 바꿨는지 판단한다."""

    if (
        len(original.vertices) != len(evaluated.vertices)
        or len(original.loops) != len(evaluated.loops)
        or len(original.polygons) != len(evaluated.polygons)
    ):
        return True
    if any(left.vertex_index != right.vertex_index for left, right in zip(original.loops, evaluated.loops)):
        return True
    return any(
        left.loop_start != right.loop_start or left.loop_total != right.loop_total
        for left, right in zip(original.polygons, evaluated.polygons)
    )


def _collect_blender_triangles(context, objects: Sequence, uv_layer_names) -> tuple[list[BakeTriangle], Vec3, float]:
    depsgraph = context.evaluated_depsgraph_get()
    selected = set(objects)
    selected_mesh_owners = {}
    for obj in objects:
        previous = selected_mesh_owners.get(obj.data)
        if previous is not None and previous.matrix_world != obj.matrix_world:
            raise ValueError(
                f"{obj.data.name}: 서로 다른 Transform의 선택 객체가 같은 Mesh와 UV를 공유합니다. "
                "Single User로 분리한 뒤 다시 실행해 주세요."
            )
        selected_mesh_owners[obj.data] = obj
    for mesh in {obj.data for obj in objects}:
        outsiders = [
            obj.name
            for obj in bpy.data.objects
            if obj.type == "MESH" and obj.data is mesh and obj not in selected
        ]
        if outsiders:
            raise ValueError(
                f"{mesh.name}: 선택 밖의 linked Mesh 인스턴스({', '.join(outsiders[:3])})가 있습니다. "
                "Single User로 분리한 뒤 다시 실행해 주세요."
            )

    bounds_points = []
    triangles: list[BakeTriangle] = []
    for object_index, obj in enumerate(objects):
        if obj.mode != "OBJECT":
            raise ValueError(f"{obj.name}: Object Mode에서 텍스처를 적용해 주세요.")
        evaluated_object = obj.evaluated_get(depsgraph)
        bounds_points.extend(
            tuple(evaluated_object.matrix_world @ Vector(corner))
            for corner in evaluated_object.bound_box
        )
        evaluated_mesh = evaluated_object.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        try:
            # Subsurf, Mirror, Bevel처럼 topology를 바꾸는 Modifier도 평가 Mesh의
            # UV를 그대로 투영하면 되므로 원본과의 topology 일치를 요구하지 않는다.
            # 생성과 베이크 사이의 Modifier 변경은 projection 계약의
            # evaluated_geometry_sha256이 잡아낸다.
            generated = _generated_topology(obj.data, evaluated_mesh)
            uv_name = _resolve_uv_name(obj, uv_layer_names, object_index)
            uv_layer = evaluated_mesh.uv_layers.get(uv_name)
            if uv_layer is None:
                raise ValueError(
                    f"{obj.name}: 평가 Mesh에 '{uv_name}' UV 레이어가 없습니다. "
                    "UV를 지우는 Modifier를 비활성화하거나 적용한 뒤 다시 실행해 주세요."
                    if generated
                    else f"{obj.name}: 평가 Mesh에 '{uv_name}' UV 레이어가 없습니다."
                )
            evaluated_mesh.calc_loop_triangles()
            matrix = evaluated_object.matrix_world
            normal_matrix = matrix.to_3x3().inverted_safe().transposed()
            # 코너 법선은 Smooth/Auto Smooth 결과를 담고 있어, 픽셀 단위로
            # 보간하면 로우폴리에서도 뷰 전이가 면 경계에서 끊기지 않는다.
            corner_normals = getattr(evaluated_mesh, "corner_normals", None)
            for loop_triangle in evaluated_mesh.loop_triangles:
                polygon = evaluated_mesh.polygons[loop_triangle.polygon_index]
                normal_vector = (normal_matrix @ polygon.normal).normalized()
                positions = tuple(
                    tuple(matrix @ evaluated_mesh.vertices[vertex_index].co)
                    for vertex_index in loop_triangle.vertices
                )
                uvs = tuple(tuple(uv_layer.data[loop_index].uv) for loop_index in loop_triangle.loops)
                vertex_normals = None
                if corner_normals is not None:
                    vertex_normals = tuple(
                        tuple((normal_matrix @ corner_normals[loop_index].vector).normalized())
                        for loop_index in loop_triangle.loops
                    )
                triangles.append(
                    BakeTriangle(
                        positions=positions,  # type: ignore[arg-type]
                        uvs=uvs,  # type: ignore[arg-type]
                        normal=tuple(normal_vector),
                        vertex_normals=vertex_normals,  # type: ignore[arg-type]
                    )
                )
        finally:
            evaluated_object.to_mesh_clear()

    if not bounds_points or not triangles:
        raise ValueError("투영할 Mesh 삼각형이 없습니다.")
    minimum = tuple(min(point[axis] for point in bounds_points) for axis in range(3))
    maximum = tuple(max(point[axis] for point in bounds_points) for axis in range(3))
    center = tuple((minimum[axis] + maximum[axis]) * 0.5 for axis in range(3))
    extent = tuple(maximum[axis] - minimum[axis] for axis in range(3))
    scale = max(extent[0], extent[1], extent[2], 0.01) * 1.2
    return triangles, center, scale  # type: ignore[return-value]


def _load_raster_sources(paths: Mapping[str, Path]) -> tuple[dict[str, RasterSource], list]:
    sources: dict[str, RasterSource] = {}
    loaded_images = []
    try:
        for name, path in paths.items():
            image = bpy.data.images.load(str(path), check_existing=False)
            loaded_images.append(image)
            width, height = map(int, image.size)
            if width <= 0 or height <= 0:
                raise ValueError(f"{name} 생성 이미지 크기가 올바르지 않습니다.")
            pixels = array("f", [0.0]) * (width * height * 4)
            image.pixels.foreach_get(pixels)
            # Blender 5.2의 Image.pixels는 sRGB PNG/JPEG를 읽을 때도 파일의
            # 인코딩된 RGB 값(예: 128 -> 0.501961)을 반환한다. 투영 뷰를 이
            # 상태로 블렌딩한 뒤 다시 sRGB 인코딩하면 중간톤이 이중 감마로
            # 밝아지므로, 소스 RGB만 명시적으로 선형화한다.
            for offset in range(0, len(pixels), 4):
                pixels[offset] = _srgb_to_linear(pixels[offset])
                pixels[offset + 1] = _srgb_to_linear(pixels[offset + 1])
                pixels[offset + 2] = _srgb_to_linear(pixels[offset + 2])
            bbox = detect_foreground_bbox(pixels, width, height)
            background, threshold, fallback = _source_statistics(pixels, width, height, bbox)
            if bbox == (0, 0, width, height):
                # 피사체가 프레임을 가득 채우면 border와의 차이를 요구하지 않는다.
                threshold = -1.0
            sources[name] = build_raster_source(
                width=width,
                height=height,
                pixels=pixels,
                subject_bbox=bbox,
                background=background,
                background_threshold=threshold,
                fallback_color=fallback,
            )
        return sources, loaded_images
    except Exception:
        for image in loaded_images:
            bpy.data.images.remove(image)
        raise


def load_image_pixels(path: str | os.PathLike[str], *, linearize: bool = True) -> tuple[array, int, int]:
    """이미지 파일을 좌하단 원점 RGBA float 배열로 읽는다. 데이터블록은 남기지 않는다.

    ``linearize``면 sRGB 인코딩 값을 선형으로 바꾼다(비교·베이크용). 표시용 시트를
    만들 때는 끄고 인코딩 값을 그대로 쓴다.
    """

    if bpy is None:
        raise RuntimeError("이미지 읽기는 Blender 안에서만 실행할 수 있습니다.")
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = map(int, image.size)
        if width <= 0 or height <= 0:
            raise ValueError(f"{Path(path).name}: 이미지 크기가 올바르지 않습니다.")
        pixels = array("f", [0.0]) * (width * height * 4)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    if linearize:
        for offset in range(0, len(pixels), 4):
            pixels[offset] = _srgb_to_linear(pixels[offset])
            pixels[offset + 1] = _srgb_to_linear(pixels[offset + 1])
            pixels[offset + 2] = _srgb_to_linear(pixels[offset + 2])
    return pixels, width, height


@dataclass(frozen=True)
class ViewAnalysis:
    """생성 뷰 이미지를 모델에 정합한 결과. 사전 검증과 베이크 후 검증이 공유한다."""

    center: Vec3
    scale: float
    depth_resolution: int
    depth_buffers: Mapping[str, Sequence[float]]
    projected_bboxes: Mapping[str, tuple[float, float, float, float]]
    sources: Mapping[str, RasterSource]
    report: Mapping[str, dict]


def analyze_view_sources(
    context,
    objects: Sequence,
    view_paths: Mapping[str, str | os.PathLike[str]] | Sequence[str | os.PathLike[str]],
    uv_layer_names=None,
    depth_resolution: int = 512,
    *,
    silhouette_warp: bool = False,
) -> ViewAnalysis:
    """베이크 없이 생성 뷰를 모델 실루엣에 정합하고 뷰별 불일치 지표를 만든다."""

    if bpy is None:
        raise RuntimeError("뷰 분석은 Blender 안에서만 실행할 수 있습니다.")
    paths = _resolve_view_paths(view_paths, require_all=False)
    triangles, center, scale = _collect_blender_triangles(context, tuple(objects), uv_layer_names)
    sources, loaded_images = _load_raster_sources(paths)
    try:
        resolution = min(1024, max(256, int(depth_resolution)))
        names = tuple(name for name in VIEW_NAMES if name in sources)
        depth_buffers = {name: build_depth_buffer(triangles, name, center, scale, resolution) for name in names}
        projected_bboxes = {name: _projected_bbox(triangles, name, center, scale) for name in names}
        report: dict = {}
        prepared = prepare_sources(
            sources, depth_buffers, resolution, projected_bboxes, silhouette_warp=silhouette_warp, report=report
        )
    finally:
        for image in loaded_images:
            if image.name in bpy.data.images:
                bpy.data.images.remove(image)
    return ViewAnalysis(
        center=center,
        scale=scale,
        depth_resolution=resolution,
        depth_buffers=depth_buffers,
        projected_bboxes=projected_bboxes,
        sources=prepared,
        report=report,
    )


def evaluate_view_sources(
    context,
    objects: Sequence,
    view_paths: Mapping[str, str | os.PathLike[str]] | Sequence[str | os.PathLike[str]],
    uv_layer_names=None,
    depth_resolution: int = 512,
) -> dict[str, dict]:
    """뷰별 실루엣 불일치 리포트만 돌려주는 사전 검증 진입점."""

    return dict(analyze_view_sources(context, objects, view_paths, uv_layer_names, depth_resolution).report)


def verify_rendered_views(
    analysis: ViewAnalysis, rendered_paths: Mapping[str, str | os.PathLike[str]]
) -> dict[str, dict]:
    """베이크한 모델을 각 시점에서 렌더한 파일을 생성 그림과 비교한 뷰별 지표."""

    results: dict[str, dict] = {}
    for view, path in rendered_paths.items():
        name = str(view).upper()
        source = analysis.sources.get(name)
        depth_buffer = analysis.depth_buffers.get(name)
        bbox = analysis.projected_bboxes.get(name)
        if source is None or depth_buffer is None or bbox is None:
            continue
        pixels, width, height = load_image_pixels(path)
        model_mask = build_model_mask(depth_buffer, analysis.depth_resolution)
        metrics = compare_rendered_view(
            pixels, width, height, source, model_mask, analysis.depth_resolution, bbox
        )
        metrics["passed"] = metrics["score"] >= VERIFY_SCORE_LIMIT
        results[name] = metrics
    return results


def _find_reusable_image(output_path: Path):
    normalized = os.path.normcase(str(output_path.resolve()))
    for image in bpy.data.images:
        if not image.get(_IMAGE_MARKER):
            continue
        marked_path = image.get("uvmapping_output_path", "")
        if marked_path and os.path.normcase(str(Path(marked_path).resolve())) == normalized:
            return image
    return None


def _find_reusable_material(output_path: Path):
    normalized = os.path.normcase(str(output_path.resolve()))
    for material in bpy.data.materials:
        if not material.get(_MATERIAL_MARKER):
            continue
        marked_path = material.get("uvmapping_output_path", "")
        if marked_path and os.path.normcase(str(Path(marked_path).resolve())) == normalized:
            return material
    return None


def _configure_material(material, image, output_path: Path) -> None:
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    output = next((node for node in nodes if node.bl_idname == "ShaderNodeOutputMaterial"), None)
    if output is None:
        output = nodes.new("ShaderNodeOutputMaterial")
    principled = next((node for node in nodes if node.bl_idname == "ShaderNodeBsdfPrincipled"), None)
    if principled is None:
        principled = nodes.new("ShaderNodeBsdfPrincipled")
    texture = next((node for node in nodes if node.get(_IMAGE_MARKER)), None)
    if texture is None:
        texture = nodes.new("ShaderNodeTexImage")
        texture[_IMAGE_MARKER] = True
        texture.label = "UVMapping AI Albedo"
    texture.image = image
    texture.interpolation = "Linear"
    # Workbench의 TEXTURE 색 모드는 활성 Image Texture 노드를 그린다. 순차 생성
    # 모드가 부분 베이크 결과를 다음 시점 가이드로 렌더할 때 이 노드가 보여야 한다.
    nodes.active = texture
    for link in tuple(principled.inputs["Base Color"].links):
        links.remove(link)
    links.new(texture.outputs["Color"], principled.inputs["Base Color"])
    if "Roughness" in principled.inputs:
        principled.inputs["Roughness"].default_value = 0.82
    for link in tuple(output.inputs["Surface"].links):
        links.remove(link)
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    material[_MATERIAL_MARKER] = True
    material["uvmapping_output_path"] = str(output_path)


def _apply_material_transaction(objects: Sequence, material) -> tuple[dict, list, dict]:
    mesh_snapshots = {}
    appended_meshes = []
    active_indices = {obj: obj.active_material_index for obj in objects}
    try:
        material_indices = {}
        for obj in objects:
            mesh = obj.data
            if mesh not in mesh_snapshots:
                mesh_snapshots[mesh] = tuple(polygon.material_index for polygon in mesh.polygons)
                material_index = mesh.materials.find(material.name)
                if material_index < 0:
                    mesh.materials.append(material)
                    material_index = len(mesh.materials) - 1
                    appended_meshes.append(mesh)
                material_indices[mesh] = material_index
                for polygon in mesh.polygons:
                    polygon.material_index = material_index
            obj.active_material_index = material_indices[mesh]
        return mesh_snapshots, appended_meshes, active_indices
    except Exception:
        _rollback_material_application(mesh_snapshots, appended_meshes, material, active_indices)
        raise


def _rollback_material_application(
    mesh_snapshots: Mapping, appended_meshes: Sequence, material, active_indices: Mapping | None = None
) -> None:
    for mesh, indices in mesh_snapshots.items():
        for polygon, material_index in zip(mesh.polygons, indices):
            polygon.material_index = material_index
    for mesh in reversed(appended_meshes):
        if mesh.materials and mesh.materials[-1] == material:
            mesh.materials.pop(index=len(mesh.materials) - 1)
    for obj, active_index in (active_indices or {}).items():
        if obj.name in bpy.data.objects:
            obj.active_material_index = min(active_index, max(0, len(obj.material_slots) - 1))


def _outside_atlas(triangle: BakeTriangle) -> bool:
    """면적이 있는 삼각형이 0-1 Atlas 바깥에 통째로 놓였는지 확인한다."""

    (u0, v0), (u1, v1), (u2, v2) = triangle.uvs
    if abs((u1 - u0) * (v2 - v0) - (u2 - u0) * (v1 - v0)) <= 1.0e-12:
        # 퇴화 삼각형은 어차피 래스터화되지 않으므로 경고 대상이 아니다.
        return False
    return (
        max(u0, u1, u2) <= 0.0
        or min(u0, u1, u2) >= 1.0
        or max(v0, v1, v2) <= 0.0
        or min(v0, v1, v2) >= 1.0
    )


def _validated_bake_targets_and_paths(
    objects: Sequence,
    view_paths,
    output_path,
    resolution: int,
    padding: int,
    *,
    require_all_sources: bool,
) -> tuple[tuple, dict[str, Path], Path, int, int]:
    """두 베이크 진입점이 공유하는 인자 검증. 정규화된 값을 돌려준다."""

    if bpy is None:
        raise RuntimeError("Atlas 베이크는 Blender 안에서만 실행할 수 있습니다.")
    targets = tuple(objects)
    if not targets or any(obj is None or obj.type != "MESH" for obj in targets):
        raise ValueError("텍스처를 적용할 Mesh 객체가 필요합니다.")
    resolution = int(resolution)
    padding = max(0, int(padding))
    if resolution > MAX_ATLAS_RESOLUTION:
        raise ValueError(
            "현재 CPU Atlas 베이크는 최대 4096px까지 지원합니다. "
            "8192px 설정은 4096px로 낮춘 뒤 다시 실행해 주세요."
        )
    paths = _resolve_view_paths(view_paths, require_all=require_all_sources)
    destination = Path(output_path).expanduser().resolve()
    if destination.suffix.lower() != ".png":
        raise ValueError("Diffuse/Albedo 출력 경로는 .png여야 합니다.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return targets, paths, destination, resolution, padding


def _bake_atlas_png(
    context,
    targets: tuple,
    paths: Mapping[str, Path],
    resolution: int,
    padding: int,
    uv_layer_names,
    raster_kwargs: Mapping[str, object],
) -> tuple[bytes, dict]:
    """평가 Mesh를 모아 래스터화하고 PNG 바이트와 통계를 돌려준다. Blender 데이터는 바꾸지 않는다."""

    triangles, center, scale = _collect_blender_triangles(context, targets, uv_layer_names)
    outside_atlas = sum(1 for triangle in triangles if _outside_atlas(triangle))
    sources, loaded_images = _load_raster_sources(paths)
    try:
        rgba, metrics = rasterize_atlas(
            triangles, sources, resolution, padding, center, scale, **raster_kwargs
        )
    finally:
        for loaded_image in loaded_images:
            if loaded_image.name in bpy.data.images:
                bpy.data.images.remove(loaded_image)
    metrics = {
        "resolution": resolution,
        "padding": padding,
        "view_names": tuple(sources),
        "outside_atlas_triangles": outside_atlas,
        "triangle_count": len(triangles),
        **metrics,
    }
    return encode_srgb_png(rgba, resolution, resolution), metrics


def _write_atomically(destination: Path, data: bytes) -> None:
    stage_path = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp.png")
    try:
        stage_path.write_bytes(data)
        os.replace(stage_path, destination)
    finally:
        stage_path.unlink(missing_ok=True)


def rasterize_to_png(
    context,
    objects: Sequence,
    view_paths: Mapping[str, str | os.PathLike[str]] | Sequence[str | os.PathLike[str]],
    output_path: str | os.PathLike[str],
    resolution: int,
    padding: int,
    uv_layer_names=None,
    *,
    blend_exponent: float = DEFAULT_BLEND_EXPONENT,
    harmonize_colors: bool = True,
    silhouette_warp: bool = False,
    unpainted_color: tuple[float, float, float] | None = None,
    require_all_sources: bool = True,
    allow_view_substitution: bool = True,
) -> dict:
    """Atlas를 PNG 파일로만 굽는다. 이미지·머티리얼 데이터블록은 만들지도 바꾸지도 않는다.

    순차 생성 모드의 부분 베이크처럼 결과를 사용자 머티리얼에 붙이면 안 되는
    중간 산출물용 진입점이다.
    """

    targets, paths, destination, resolution, padding = _validated_bake_targets_and_paths(
        objects, view_paths, output_path, resolution, padding, require_all_sources=require_all_sources
    )
    png, metrics = _bake_atlas_png(
        context,
        targets,
        paths,
        resolution,
        padding,
        uv_layer_names,
        {
            "blend_exponent": blend_exponent,
            "harmonize_colors": harmonize_colors,
            "silhouette_warp": silhouette_warp,
            "unpainted_color": unpainted_color,
            "require_all_sources": require_all_sources,
            "allow_view_substitution": allow_view_substitution,
        },
    )
    _write_atomically(destination, png)
    return {"status": "ATLAS_WRITTEN", "output_path": str(destination), **metrics}


@contextmanager
def apply_temporary_material(objects: Sequence, image_path: str | os.PathLike[str]):
    """이미지 하나를 입힌 임시 머티리얼을 대상 객체에 잠시 적용했다가 원래대로 되돌린다.

    순차 생성 모드가 부분 베이크 결과를 "채색된 모델" 가이드로 렌더할 때 쓴다.
    사용자 머티리얼 슬롯과 면 할당은 블록을 벗어날 때 항상 복원된다.
    """

    if bpy is None:
        raise RuntimeError("임시 머티리얼 적용은 Blender 안에서만 실행할 수 있습니다.")
    targets = tuple(objects)
    path = Path(image_path).expanduser().resolve()
    image = bpy.data.images.load(str(path), check_existing=False)
    material = bpy.data.materials.new(f"UVMapping 임시 가이드 · {path.stem}")
    mesh_snapshots: dict = {}
    appended_meshes: list = []
    active_indices: dict = {}
    try:
        try:
            image.colorspace_settings.name = "sRGB"
        except TypeError:
            pass
        _configure_material(material, image, path)
        mesh_snapshots, appended_meshes, active_indices = _apply_material_transaction(targets, material)
        yield material
    finally:
        _rollback_material_application(mesh_snapshots, appended_meshes, material, active_indices)
        if material.name in bpy.data.materials:
            bpy.data.materials.remove(material)
        if image.name in bpy.data.images:
            bpy.data.images.remove(image)


def bake_diffuse(
    context,
    objects: Sequence,
    view_paths: Mapping[str, str | os.PathLike[str]] | Sequence[str | os.PathLike[str]],
    output_path: str | os.PathLike[str],
    resolution: int,
    padding: int,
    uv_layer_names=None,
    *,
    blend_exponent: float = DEFAULT_BLEND_EXPONENT,
    harmonize_colors: bool = True,
    silhouette_warp: bool = False,
    unpainted_color: tuple[float, float, float] | None = None,
    require_all_sources: bool = True,
    allow_view_substitution: bool = True,
) -> dict:
    """생성 뷰(FRONT/RIGHT/BACK 필수, LEFT/TOP/BOTTOM 선택)를 공유 UV Atlas에 굽고 재질에 연결한다.

    Atlas 파일이 완전히 만들어지기 전에는 Blender 재질/이미지 상태를 바꾸지
    않는다. 파일 교체와 재질 적용 중 실패하면 가능한 범위에서 이전 상태를
    복원한다. 키워드 인자는 :func:`rasterize_atlas`와 같은 뜻이다.
    """

    targets, paths, destination, resolution, padding = _validated_bake_targets_and_paths(
        objects, view_paths, output_path, resolution, padding, require_all_sources=require_all_sources
    )
    png, metrics = _bake_atlas_png(
        context,
        targets,
        paths,
        resolution,
        padding,
        uv_layer_names,
        {
            "blend_exponent": blend_exponent,
            "harmonize_colors": harmonize_colors,
            "silhouette_warp": silhouette_warp,
            "unpainted_color": unpainted_color,
            "require_all_sources": require_all_sources,
            "allow_view_substitution": allow_view_substitution,
        },
    )
    backup_path = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.bak")
    image = None
    material = None
    created_image = False
    created_material = False
    mesh_snapshots = {}
    appended_meshes = []
    active_indices = {}
    file_committed = False
    try:
        if destination.exists():
            shutil.copy2(destination, backup_path)
        _write_atomically(destination, png)
        file_committed = True

        image = _find_reusable_image(destination)
        if image is None:
            image = bpy.data.images.load(str(destination), check_existing=False)
            created_image = True
        else:
            image.filepath = str(destination)
            image.reload()
        image.name = f"UVMapping Albedo · {destination.stem}"
        image[_IMAGE_MARKER] = True
        image["uvmapping_output_path"] = str(destination)
        try:
            image.colorspace_settings.name = "sRGB"
        except TypeError:
            pass

        material = _find_reusable_material(destination)
        if material is None:
            material = bpy.data.materials.new(f"UVMapping Albedo · {destination.stem}")
            created_material = True
        _configure_material(material, image, destination)
        mesh_snapshots, appended_meshes, active_indices = _apply_material_transaction(targets, material)

        if backup_path.exists():
            backup_path.unlink()
        return {
            "status": "ALBEDO_APPLIED",
            "output_path": str(destination),
            "image_name": image.name,
            "material_name": material.name,
            "object_names": tuple(obj.name for obj in targets),
            **metrics,
        }
    except Exception:
        _rollback_material_application(mesh_snapshots, appended_meshes, material, active_indices)
        if created_material and material is not None:
            bpy.data.materials.remove(material)
        if created_image and image is not None:
            bpy.data.images.remove(image)
        if file_committed:
            if backup_path.exists():
                os.replace(backup_path, destination)
                if image is not None and not created_image:
                    try:
                        image.reload()
                    except RuntimeError:
                        pass
            else:
                destination.unlink(missing_ok=True)
        raise
    finally:
        backup_path.unlink(missing_ok=True)


__all__ = (
    "ALIGNMENT_MIN_IOU",
    "WARP_MAX_CENTER_SHIFT_RATIO",
    "WARP_MAX_RELATIVE_CHANGE",
    "SILHOUETTE_HOLE_LIMIT",
    "SILHOUETTE_SEGMENT_MISMATCH_LIMIT",
    "VERIFY_SCORE_LIMIT",
    "ViewAnalysis",
    "analyze_view_sources",
    "background_flood_mask",
    "coarse_foreground_mask",
    "compare_rendered_view",
    "compose_verification_sheet",
    "erode_mask",
    "evaluate_silhouette_match",
    "evaluate_view_sources",
    "load_image_pixels",
    "silhouette_match_passed",
    "verify_rendered_views",
    "BakeTriangle",
    "DEFAULT_BLEND_EXPONENT",
    "FOREGROUND_EXTEND_RATIO",
    "MAX_ATLAS_RESOLUTION",
    "REQUIRED_SOURCE_VIEW_NAMES",
    "RasterSource",
    "SOURCE_VIEW_NAMES",
    "SilhouetteAlignment",
    "VIEW_NAMES",
    "VIEW_SPECS",
    "ViewSpec",
    "align_silhouette",
    "bake_diffuse",
    "barycentric_weights",
    "bilinear_sample",
    "build_depth_buffer",
    "build_model_mask",
    "detect_foreground_bbox",
    "dilate_mask",
    "dilate_rgba",
    "build_raster_source",
    "encode_srgb_png",
    "estimate_view_gains",
    "extend_foreground",
    "extend_foreground_mask",
    "foreground_mask",
    "mask_distance",
    "prepare_sources",
    "project_point",
    "rasterize_atlas",
    "rasterize_to_png",
    "apply_temporary_material",
    "srgb_to_linear",
)
