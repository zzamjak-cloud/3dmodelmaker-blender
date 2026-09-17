"""외부 에셋 없이 연결된 쿼드 인체·네발동물 편집용 원형을 만든다.

얼굴의 국소 동심 루프와 관절의 지지 루프를 제공한다. 손가락, 구강,
눈꺼풀 해부 구조와 리그는 포함하지 않으며 사용자가 체형에 맞게 편집한다.
"""


class _Builder:
    """면을 돌출하고 국소 루프를 추가하는 순수 Python 메시 생성기다."""

    def __init__(self):
        self.vertices, self.faces, self.groups = [], [], {}

    def ring(self, coordinates, group):
        ids = list(range(len(self.vertices), len(self.vertices) + len(coordinates)))
        self.vertices.extend(coordinates)
        self.groups.setdefault(group, []).extend(ids)
        return ids

    def bridge(self, first, second):
        result = []
        for i in range(len(first)):
            j = (i + 1) % len(first)
            face = [first[i], first[j], second[j], second[i]]
            self.faces.append(face)
            result.append(face)
        return result

    def limb(self, face, end, group):
        self.faces.remove(face)
        start = [sum(self.vertices[i][axis] for i in face) / 4 for axis in range(3)]
        offsets = [tuple(self.vertices[i][axis] - start[axis] for axis in range(3)) for i in face]
        previous = face
        for t in (.14, .40, .47, .50, .53, .60, .88, 1.0):
            center = [start[axis] + (end[axis] - start[axis]) * t for axis in range(3)]
            radius = 1.0 - t * .5
            current = self.ring([tuple(center[a] + offset[a] * radius for a in range(3))
                                 for offset in offsets], group)
            if .40 <= t <= .60:
                self.groups.setdefault('joint_' + group, []).extend(current)
            self.bridge(previous, current)
            previous = current
        self.faces.append(list(previous))

    def inset(self, face, group, depth=0.0):
        self.faces.remove(face)
        center = [sum(self.vertices[i][a] for i in face) / 4 for a in range(3)]
        previous = face
        for scale in (.78, .55, .32):
            current = self.ring([tuple(center[a] + (self.vertices[i][a] - center[a]) * scale
                                      + (depth * (1 - scale) if a == 1 else 0)
                                      for a in range(3)) for i in face], group)
            self.bridge(previous, current)
            previous = current
        self.faces.append(previous)


def build_template(kind='HUMANOID'):
    """정점 좌표, 사각 면, 부위 그룹을 돌려준다."""
    if kind not in ('HUMANOID', 'QUADRUPED'):
        raise ValueError('지원하지 않는 템플릿: ' + str(kind))
    builder = _Builder()
    shape = [(-1, -1), (0, -1), (1, -1), (1, 1), (0, 1), (-1, 1)]
    rings = []
    side_faces = []
    for z, x, y in ((.85, .23, .13), (1.02, .21, .12), (1.25, .25, .15), (1.43, .27, .14)):
        ring = builder.ring([(a * x, b * y, z) for a, b in shape], 'torso')
        if rings:
            side_faces.append(builder.bridge(rings[-1], ring))
        rings.append(ring)
    bottom = rings[0]
    legs = [[bottom[i] for i in (5, 4, 1, 0)], [bottom[i] for i in (4, 3, 2, 1)]]
    builder.faces.extend(legs)
    for side, face in zip((-1, 1), legs):
        builder.limb(face, (side * .17, -.02, .04), 'leg_' + ('L' if side > 0 else 'R'))
    for side, index in ((1, 2), (-1, 5)):
        builder.limb(side_faces[-1][index], (side * .88, 0, 1.26),
                     'arm_' + ('L' if side > 0 else 'R'))
    previous = rings[-1]
    facial = []
    for z, x, y in ((1.48, .09, .09), (1.54, .09, .09), (1.59, .14, .12),
                    (1.66, .16, .145), (1.73, .16, .145), (1.80, .145, .13), (1.85, .10, .09)):
        current = builder.ring([(a * x, b * y, z) for a, b in shape], 'head')
        panels = builder.bridge(previous, current)
        if 1.66 <= z <= 1.80:
            facial.extend(panels[:2])
        previous = current
    builder.faces.extend([[previous[i] for i in (0, 1, 4, 5)],
                          [previous[i] for i in (1, 2, 3, 4)]])
    for face in facial:
        builder.inset(face, 'face', -.025)
    if kind == 'QUADRUPED':
        # 몸통을 수평으로 놓고 네 다리는 공통 지면까지 내린다.
        builder.vertices = [(x * .8, -(z - 1.15) * 1.7, y + .86)
                            for x, y, z in builder.vertices]
        for group in ('arm_L', 'arm_R', 'leg_L', 'leg_R'):
            ids = builder.groups[group]
            for j, index in enumerate(ids):
                t = (.14, .40, .47, .50, .53, .60, .88, 1.0)[j // 4]
                x, y, z = builder.vertices[index]
                side = 1 if group.endswith('L') else -1
                anchor_y = -.36 if group.startswith('arm') else .50
                corner = ((-1, -1), (1, -1), (1, 1), (-1, 1))[j % 4]
                radius = .085 * (1 - .5 * t)
                builder.vertices[index] = (side * (.19 + .035 * t) + corner[0] * radius,
                                           anchor_y + corner[1] * radius,
                                           z * (1 - t) + .035 * t)
    return builder.vertices, builder.faces, builder.groups
