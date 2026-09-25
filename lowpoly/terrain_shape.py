"""지형 윤곽·내부 정점 배치의 순수 기하 계산. bpy 없이 테스트한다.

예전 지형은 경계 상자를 격자로 덮고 윤곽 밖 칸을 지웠다. 그래서 가장자리가 계단처럼 깨지고,
평평한 땅에도 의미 없는 격자 면이 가득했다. 지금은 윤곽 다각형을 매끈하게 다듬어 그 자체를
지면 테두리로 쓰고, 기복이 필요할 때만 안쪽에 성긴 정점을 흩뿌려 삼각화한다.
"""

from __future__ import annotations

import math
import random


def signed_area(poly) -> float:
    s = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        s += x1 * y2 - x2 * y1
    return s * 0.5


def ccw(poly) -> list:
    """반시계 방향으로 맞춘 사본."""
    poly = [(float(x), float(y)) for x, y in poly]
    return poly if signed_area(poly) >= 0 else poly[::-1]


def chaikin(poly, iterations: int = 2) -> list:
    """닫힌 다각형 모서리 깎기 — 6~12점 윤곽을 해안선처럼 둥글게 만든다."""
    out = list(poly)
    for _ in range(max(0, int(iterations))):
        nxt = []
        for i in range(len(out)):
            (x1, y1), (x2, y2) = out[i], out[(i + 1) % len(out)]
            nxt.append((0.75 * x1 + 0.25 * x2, 0.75 * y1 + 0.25 * y2))
            nxt.append((0.25 * x1 + 0.75 * x2, 0.25 * y1 + 0.75 * y2))
        out = nxt
    return out


def resample(poly, max_segment: float) -> list:
    """긴 변만 쪼개 변 길이를 max_segment 이하로 — 짧은 변은 건드리지 않는다."""
    out = []
    step = max(float(max_segment), 1e-6)
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        n = max(1, int(math.ceil(math.hypot(x2 - x1, y2 - y1) / step)))
        for k in range(n):
            t = k / n
            out.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t))
    return out


def point_in_polygon(x: float, y: float, poly) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def edge_distance(x: float, y: float, poly) -> float:
    best = float("inf")
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        dx, dy = x2 - x1, y2 - y1
        length2 = dx * dx + dy * dy
        t = 0.0 if length2 <= 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / length2))
        best = min(best, math.hypot(x - (x1 + dx * t), y - (y1 + dy * t)))
    return best


def interior_points(poly, spacing: float, seed: int = 0) -> list:
    """윤곽 안쪽에 성긴 지터 격자 점 — 테두리에서 spacing의 절반 이상 떨어진 것만."""
    if spacing <= 0:
        return []
    rng = random.Random(seed)
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    out = []
    y = min(ys) + spacing * 0.5
    while y < max(ys):
        x = min(xs) + spacing * 0.5
        while x < max(xs):
            px = x + (rng.random() - 0.5) * spacing * 0.6
            py = y + (rng.random() - 0.5) * spacing * 0.6
            if point_in_polygon(px, py, poly) and edge_distance(px, py, poly) > spacing * 0.5:
                out.append((px, py))
            x += spacing
        y += spacing
    return out


def offset_ring(poly, amount: float, jitter: float = 0.0, seed: int = 0) -> list:
    """반시계 윤곽을 안쪽(amount>0)으로 민 고리. 정점마다 법선 방향으로 지터를 섞어 바위 절벽처럼 거칠게 한다.

    이웃 두 변 법선의 평균으로 밀기 때문에 오목한 곳에서도 꼬이지 않을 만큼만(작게) 쓴다."""
    rng = random.Random(seed)
    count = len(poly)
    out = []
    for i in range(count):
        (ax, ay), (bx, by), (cx, cy) = poly[i - 1], poly[i], poly[(i + 1) % count]
        n1 = _left_normal(ax, ay, bx, by)
        n2 = _left_normal(bx, by, cx, cy)
        nx, ny = n1[0] + n2[0], n1[1] + n2[1]
        length = math.hypot(nx, ny) or 1.0
        nx, ny = nx / length, ny / length
        # 마이터 보정: 모서리에서도 두 변으로부터 같은 거리만큼 들어가게. 뾰족한 곳은 배율을 막는다
        miter = 1.0 / max(nx * n1[0] + ny * n1[1], 0.5)
        d = (amount + (rng.random() - 0.5) * 2.0 * jitter) * miter
        out.append((bx + nx * d, by + ny * d))
    return out


def _left_normal(x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy) or 1.0
    return (-dy / length, dx / length)
