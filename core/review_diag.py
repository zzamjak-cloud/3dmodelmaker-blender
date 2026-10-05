# 시각 검토 턴에 붙이는 자동 점검 — 렌더만으로는 놓치기 쉬운 짝 없는 파트·떠 있는 파트를 수치로 짚는다
# 판정 로직은 bpy 비의존(셸 상자 목록만 받는다). 셸 추출만 bmesh를 지연 임포트한다.

_PAIR_TOL = 0.04       # 미러 짝 중심 허용 오차(m) — 크기의 10%를 더한다
_CENTER_EPS = 0.03     # |x|가 이보다 작으면 중앙 파트로 보고 짝을 찾지 않는다
_TOUCH_GAP = 0.01      # 상자끼리 이 간격 안이면 닿은 것으로 본다
_GROUND_EPS = 0.02     # 바닥에서 이 높이 안이면 바닥에 닿은 것으로 본다
_MAX_ITEMS = 8
_MIN_SIZE = 0.005     # 이보다 작은 셸은 잔재로 본다


def _size_close(a, b) -> bool:
    return all(abs(x - y) <= max(0.02, 0.25 * max(x, y)) for x, y in zip(a, b))


def unpaired_shells(shells) -> list:
    """x≠0에 있는데 반대편(-x)에 같은 크기 짝이 없는 셸. shells: [{'center', 'size'}]."""
    out = []
    for i, s in enumerate(shells):
        cx, cy, cz = s["center"]
        if abs(cx) < _CENTER_EPS:
            continue
        tol = _PAIR_TOL + 0.1 * max(s["size"])
        mirrored = False
        for j, o in enumerate(shells):
            if i == j:
                continue
            ox, oy, oz = o["center"]
            if (abs(ox + cx) <= tol and abs(oy - cy) <= tol and abs(oz - cz) <= tol
                    and _size_close(s["size"], o["size"])):
                mirrored = True
                break
        if not mirrored:
            out.append(s)
    return out


def _boxes_touch(a, b) -> bool:
    for k in range(3):
        a_lo, a_hi = a["center"][k] - a["size"][k] / 2, a["center"][k] + a["size"][k] / 2
        b_lo, b_hi = b["center"][k] - b["size"][k] / 2, b["center"][k] + b["size"][k] / 2
        if a_hi + _TOUCH_GAP < b_lo or b_hi + _TOUCH_GAP < a_lo:
            return False
    return True


def _volume(s) -> float:
    x, y, z = s["size"]
    return x * y * z


def floating_shells(shells) -> list:
    """본체(가장 큰 셸)나 바닥까지 닿음이 이어지지 않는 셸.

    셸에 'touches'(실제 표면 근접으로 구한 이웃 인덱스)가 있으면 그것을, 없으면 상자 겹침을 쓴다.
    상자 겹침만으로는 본체 상자 안에 든 파트가 모두 닿은 것으로 보여 해치 위에 뜬 트림 같은
    결함을 놓친다 — 실제 판정은 shell_boxes가 채우는 'touches'로 한다."""
    if not shells:
        return []
    main = max(range(len(shells)), key=lambda i: _volume(shells[i]))

    def neighbors(i):
        if "touches" in shells[i]:
            return shells[i]["touches"]
        return [j for j in range(len(shells)) if j != i and _boxes_touch(shells[i], shells[j])]

    anchored = {main} | {i for i, s in enumerate(shells)
                         if s["center"][2] - s["size"][2] / 2 <= _GROUND_EPS}
    stack = list(anchored)
    while stack:
        i = stack.pop()
        for j in neighbors(i):
            if j not in anchored:
                anchored.add(j)
                stack.append(j)
    return [s for i, s in enumerate(shells) if i not in anchored]


def _fmt(s) -> str:
    c = ", ".join(f"{v:.2f}" for v in s["center"])
    z = "×".join(f"{v:.2f}" for v in s["size"])
    return f"중심 ({c}), 크기 {z}"


def describe(shells) -> list:
    """검토 프롬프트에 붙일 문장 목록. 결함이 없으면 빈 목록."""
    # 크기 0에 가까운 잔재(불리언이 남긴 낱점·조각)는 눈에 보이지 않으므로 짚지 않는다
    visible = [s for s in shells if max(s["size"]) >= _MIN_SIZE]
    keep = {id(s) for s in visible}
    shells = [dict(s, touches=[visible.index(o) for o in (shells[j] for j in s["touches"]) if id(o) in keep])
              if "touches" in s else s for s in visible]
    lines = []
    unpaired = unpaired_shells(shells)
    if unpaired:
        lines.append(f"좌우 짝이 없는 파트 {len(unpaired)}개 — 대칭이어야 하면 반대편을 만들어라"
                     "(mirror_x 목록에 넣었는지 확인). 의도된 비대칭이면 무시:")
        lines += [f"  - {_fmt(s)}" for s in unpaired[:_MAX_ITEMS]]
    floating = floating_shells(shells)
    if floating:
        lines.append(f"어디에도 닿지 않고 떠 있는 파트 {len(floating)}개 — 이웃 파트에 파묻거나 지지 파트 끝에 이어라:")
        lines += [f"  - {_fmt(s)}" for s in floating[:_MAX_ITEMS]]
    return lines


def shell_boxes(objects) -> list:
    """메시 오브젝트들의 연결 셸마다 월드 상자(중심·크기)와 실제로 닿은 이웃 셸(touches)을 구한다."""
    import bmesh
    from mathutils.bvhtree import BVHTree
    shells, trees = [], []
    for obj in objects:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.transform(obj.matrix_world)
        bm.verts.ensure_lookup_table()
        seen = set()
        for start in bm.verts:
            if start.index in seen:
                continue
            stack, verts = [start], []
            while stack:
                v = stack.pop()
                if v.index in seen:
                    continue
                seen.add(v.index)
                verts.append(v)
                stack.extend(e.other_vert(v) for e in v.link_edges)
            index = {v.index: k for k, v in enumerate(verts)}
            coords = [v.co.copy() for v in verts]
            faces = {f for v in verts for f in v.link_faces}
            polys = [[index[v.index] for v in f.verts] for f in faces]
            lo = [min(c[k] for c in coords) for k in range(3)]
            hi = [max(c[k] for c in coords) for k in range(3)]
            shells.append({"center": tuple((a + b) / 2 for a, b in zip(lo, hi)),
                           "size": tuple(b - a for a, b in zip(lo, hi)), "touches": []})
            trees.append((BVHTree.FromPolygons(coords, polys) if polys else None, coords))
        bm.free()
    for i in range(len(shells)):
        for j in range(i + 1, len(shells)):
            if not _boxes_touch(shells[i], shells[j]):
                continue
            if _shells_touch(trees[i], trees[j]):
                shells[i]["touches"].append(j)
                shells[j]["touches"].append(i)
    return shells


def _shells_touch(a, b) -> bool:
    """두 셸이 교차하거나 _TOUCH_GAP 안으로 붙어 있으면 True."""
    (tree_a, coords_a), (tree_b, coords_b) = a, b
    if tree_a is None or tree_b is None:
        return False
    if tree_a.overlap(tree_b):
        return True
    for tree, coords in ((tree_b, coords_a), (tree_a, coords_b)):
        for co in coords:
            hit = tree.find_nearest(co, _TOUCH_GAP)
            if hit[0] is not None:
                return True
    return False
