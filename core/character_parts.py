"""캐릭터 부품을 순차 생성하고 공통 시트 좌표로 배치한다."""
import os

# 순서가 곧 생성 순서다 — 몸체가 먼저 나와야 나머지 부품의 축척·배치 기준이 생긴다
# 악세사리는 의상에 포함한다 — 벨트·고리처럼 몸을 감싸는 소품만 따로 생성하면 TRELLIS 가 고리 안을 채워 슬랩이 된다(실측)
PARTS = ('BODY', 'OUTFIT', 'WEAPON')
LABELS = {'BODY': '몸체', 'OUTFIT': '의상·악세사리', 'WEAPON': '무기·방어구'}


def retopo_method_note(info):
    """실제 처리 방법과 대체 처리 사유를 사용자 로그용으로 반환한다."""
    note = str(info['method'])
    if info.get('fallback_reason'):
        note += f' (대체 처리: {info["fallback_reason"]})'
    return note


def part_prompt(request, part):
    """원본 턴어라운드의 카메라와 축척을 고정한 부품 요청을 만든다."""
    detail = {
        'BODY': '의상·갑옷·악세사리·무기를 제거하고 가려진 몸체 전체를 복원한다. 머리카락·털은 몸체에 포함한다. 인간형은 불투명하고 장식 없는 밀착형 기본복을 입혀 몸체를 표현한다.',
        # 속이 빈 옷만 그리면 TRELLIS.2 가 형태를 못 잡는다(반쪽 분리·눌림) — 옷 입은 전신으로 만들고 몸체를 불리언으로 뺀다
        'OUTFIT': '무기·방어구(검·방패·투구·갑옷·견갑)만 제거하고, 몸체·머리카락과 옷·신발·장갑·모자, 배낭·가방·벨트·주머니·스카프·장신구·안경·리본 같은 소품은 원본 그대로 몸에 입힌 전신 상태로 둔다.',
        'WEAPON': '손에 들거나 등에 장착한 무기와 갑옷·방패·투구·견갑 같은 방어구만 원래 위치에 남기고 몸체·의상·악세사리는 제거한다.',
    }[part]
    return (f'{request}\n부품 분리: {LABELS[part]}. {detail}\n'
            '첨부 원본 턴어라운드와 동일한 3x2 칸, 카메라, 자세, 축척, 여백, 발바닥 위치를 유지한다. '
            '윗줄은 정면(FRONT), 뒷면(BACK), 좌측면(LEFT); 아랫줄은 우측면(RIGHT), 상면(TOP), 3/4뷰이다. '
            '각 칸 상단 12%에만 라벨을 표기하고 그 아래에는 글자를 넣지 않는다. '
            '작은 부품을 확대하거나 중앙으로 이동하지 않는다. 모든 배경은 순백색, 그림자 금지. '
            '해당 부품이 원본에 없으면 라벨을 제외한 모든 칸을 완전히 흰색으로 둔다. 없는 장비를 만들어내지 않는다.')


GRID_MARGIN = 8          # 셀 경계 격자선 폭(px) — 실루엣으로 세면 bbox 가 셀 전체가 된다
LABEL_MAX_FRAC = 0.12    # 라벨 블록 최대 높이 비율 (셀 상단 또는 하단)
LABEL_ZONE = 0.18        # 라벨은 셀의 위/아래 이 비율 안에서만 나타난다
WHITE = .90              # RGB 최소값이 이보다 밝으면 배경


def _row_spans(pixels, width, height):
    """행별 불투명 픽셀의 (최소x, 최대x). 격자선 여백은 무시한다. bpy 픽셀은 좌하단 원점 — 행 0 이 아래다."""
    m = GRID_MARGIN if width > 4 * GRID_MARGIN and height > 4 * GRID_MARGIN else 0
    spans = [None] * height
    for y in range(m, height - m):
        base = y * width
        lo = hi = None
        for x in range(m, width - m):
            i = (base + x) * 4
            if pixels[i + 3] > .1 and min(pixels[i], pixels[i + 1], pixels[i + 2]) < WHITE:
                if lo is None:
                    lo = x
                hi = x
        if lo is not None:
            spans[y] = (lo, hi)
    return spans


def _blocks(flags):
    """True 연속 구간 [(start, end)]."""
    out, start = [], None
    for i, v in enumerate(flags):
        if v and start is None:
            start = i
        if not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(flags)))
    return out


def _strip_labels(spans, height):
    """상/하단 가장자리의 작은 분리 덩어리(뷰 라벨 글자)를 지운다. 벨트 주머니처럼 가운데 있는 작은 조각은 남긴다.

    생성 모델에 따라 라벨이 셀 위(지시대로) 또는 아래(gpt-image-2 실측)에 붙는다 — 위치를 가정하지 않는다."""
    blocks = _blocks([s is not None for s in spans])
    if not blocks:
        return spans
    body = max(blocks, key=lambda b: b[1] - b[0])
    zone = height * LABEL_ZONE
    for b in blocks:
        small = (b[1] - b[0]) < height * LABEL_MAX_FRAC
        at_edge = b[0] < zone or b[1] > height - zone
        # 본체가 따로 있는 경우의 가장자리 작은 블록, 또는 (빈 시트에서) 라벨만 남은 단독 블록
        if small and at_edge and (b is not body or len(blocks) == 1):
            for y in range(b[0], b[1]):
                spans[y] = None
    return spans


def silhouette_bbox(pixels, width, height):
    """흰 배경 이미지의 불투명 실루엣 범위를 정규화 좌표로 반환한다 (격자선·라벨 제외)."""
    spans = _strip_labels(_row_spans(pixels, width, height), height)
    rows = [y for y, s in enumerate(spans) if s is not None]
    if not rows:
        return None
    x0 = min(spans[y][0] for y in rows)
    x1 = max(spans[y][1] for y in rows)
    area = sum(spans[y][1] - spans[y][0] + 1 for y in rows)
    if area < max(4, width * height * .0003):
        return None
    return x0 / width, min(rows) / height, (x1 + 1) / width, (max(rows) + 1) / height


def silhouette_mask(pixels, width, height, grid: int = 48):
    """실루엣을 grid×grid 불리언 격자로 요약한다 (격자선·라벨 제외). 환각 판정용."""
    spans = _strip_labels(_row_spans(pixels, width, height), height)
    mask = [[False] * grid for _ in range(grid)]
    for y, s in enumerate(spans):
        if s is None:
            continue
        gy = min(grid - 1, y * grid // height)
        for gx in range(min(grid - 1, s[0] * grid // width), min(grid - 1, s[1] * grid // width) + 1):
            # 행 구간을 통째로 칠한다 — 몸통 속이 비어 보이는 경우(팔 사이)도 실루엣 범위로 본다
            mask[gy][gx] = True
    return mask


def outside_ratio(part_mask, body_mask, dilate: int = 2) -> float:
    """부품 실루엣 중 원본 캐릭터 실루엣(dilate 칸 확장) 밖에 있는 비율.

    같은 카메라·축척의 부품 시트라면 실제 부품은 몸체 실루엣 안에 거의 다 들어온다. 생성 모델이 원본에 없는
    부품(검·방패 등)을 지어내면 대부분이 실루엣 밖에 놓인다 — 그 비율로 환각을 걸러낸다."""
    g = len(body_mask)
    dil = [[False] * g for _ in range(g)]
    for y in range(g):
        for x in range(g):
            if body_mask[y][x]:
                for dy in range(-dilate, dilate + 1):
                    for dx in range(-dilate, dilate + 1):
                        yy, xx = y + dy, x + dx
                        if 0 <= yy < g and 0 <= xx < g:
                            dil[yy][xx] = True
    total = outside = 0
    for y in range(g):
        for x in range(g):
            if part_mask[y][x]:
                total += 1
                if not dil[y][x]:
                    outside += 1
    return outside / total if total else 0.0


HALLUCINATION_RATIO = 0.5   # 이 비율 이상이 원본 실루엣 밖이면 "없는 부품을 그린 것"으로 본다

# 시트를 만들기 전에 원화를 비전 모델에 보여 부품 유무를 먼저 묻는다. 이미지 생성 모델은 "없으면 빈 시트" 지시를
# 자주 어기고 몸 실루엣 안에 검·방패를 크게 그려 실루엣 게이트를 통과한다(실측 v6). 색 비교 게이트는 나무 방패색이
# 조끼색과 겹쳐 판별력이 없었다(환각 0.45 vs 임계 0.5). 질문 한 번은 시트 1장·셰이프 1건보다 훨씬 싸다.
PRESENCE_TARGET = {
    'OUTFIT': '옷·신발·장갑·모자, 또는 배낭·가방·벨트·주머니·스카프·장신구·안경·리본 같은 착용 소품',
    'WEAPON': '손에 들거나 등에 장착한 무기(검·창·활·지팡이·총 등), 또는 방패·투구·갑옷·견갑 같은 방어구',
}


def presence_prompt(request: str, part: str) -> str:
    return (f'첨부 이미지는 다음 캐릭터의 턴어라운드 원화다: {request}\n'
            f'질문: 이 캐릭터가 {PRESENCE_TARGET[part]}를 착용하거나 소지하고 있는가?\n'
            '그림에 실제로 보이는 것만 근거로 판단한다. 설명 없이 반드시 YES 또는 NO 한 단어로만 답한다.')


def presence_command(exe: str, work_dir: str, image: str) -> list:
    """codex exec 읽기 전용 — 이미지 1장 첨부, 프롬프트는 stdin, 최종 답은 last_message.txt."""
    return [exe, 'exec', '-s', 'read-only', '--skip-git-repo-check', '--cd', work_dir,
            '-o', os.path.join(work_dir, 'last_message.txt'), '-i', image, '-']


def parse_presence(text):
    """모델 답에서 YES/NO 를 읽는다. 둘 다 없거나 둘 다 있으면 None(판독 실패 → 시트로 진행)."""
    words = set(''.join(ch if ch.isalpha() else ' ' for ch in (text or '')).upper().split())
    yes, no = 'YES' in words, 'NO' in words
    if yes == no:
        return None
    return yes


def presence_cli():
    """유무 판독에 쓸 CLI 경로. 없으면 '' — 판독을 건너뛰고 시트 생성으로 간다(검증 스크립트가 패치하기도 한다)."""
    from .. import preferences
    return preferences.resolve_cli_path('CODEX') or ''
def part_texture_prompt(request, part):
    """출력 격자는 가이드가 정하고 부품 범위와 색 참조만 요청에 추가한다."""
    prompt = (f'{request}\n채색 대상은 {LABELS[part]} 부품만이다. 첨부 부품 원화의 색과 재질을 유지한다. '
              '가이드에 없는 몸체·얼굴·다른 장비를 새로 그리지 않는다.')
    if part == 'BODY':
        prompt += ' 몸체의 장비에 가려진 면도 몸체 재질로 채색한다.'
    return prompt


BUDGET_MIN_RATIO = 0.12   # 아주 작은 부품도 이 비율 이하로는 예산을 줄이지 않는다 (버클·주머니 형태 유지)
BUDGET_EXPONENT = 0.75    # 면적비의 거듭제곱 — 작은 부품에 면적비보다 조금 더 후하게 준다 (디테일 밀도 유지)


def sizes_match(a: dict, b: dict, tolerance: float = 0.04) -> bool:
    """뷰별 칸 크기가 같은 축척으로 볼 수 있는가. 격자선을 찾아 자르면 시트마다 몇 px 씩 다를 수 있다 —
    좌표는 칸 안에서 정규화하므로 이 정도 차이는 배치에 영향이 없다."""
    for view, (w, h) in a.items():
        if view not in b:
            return False
        bw, bh = b[view]
        if abs(w - bw) > tolerance * max(bw, 1) or abs(h - bh) > tolerance * max(bh, 1):
            return False
    return True


SIZE_SKIP_TOLERANCE = 0.15   # 격자 차이가 이 이상이면 다른 레이아웃으로 보고 부품을 생략한다(bbox 는 정규화 좌표라 그 아래는 그대로 진행)


def part_face_budget(base_faces: int, body_box, part_box) -> int:
    """부품별 리토폴로지 목표 면수. 몸체 대비 정면 실루엣 면적비로 배분한다.

    모든 부품에 몸체 예산(예: 12,000)을 똑같이 주면 벨트 하나가 몸체와 같은 면수를 받는다. 면적비를 쓰면
    의상(≈0.7) → 약 9,200, 벨트·주머니(≈0.25) → 약 4,200, 몸체는 기준값 그대로다."""
    base = max(int(base_faces), 1)
    if not body_box or not part_box:
        return base
    body_area = max((body_box[2] - body_box[0]) * (body_box[3] - body_box[1]), 1e-6)
    part_area = max((part_box[2] - part_box[0]) * (part_box[3] - part_box[1]), 0.0)
    ratio = min(1.0, max(BUDGET_MIN_RATIO, (part_area / body_area) ** BUDGET_EXPONENT))
    return max(int(round(base * ratio)), 500)


def placement(body_box, part_box, height):
    """몸체 시트의 픽셀 축척에서 부품 키와 중심·바닥 위치를 구한다."""
    if not body_box or not part_box or body_box[3] <= body_box[1]:
        raise ValueError('부품 정렬에 필요한 몸체 또는 부품 실루엣이 없습니다')
    scale = height / (body_box[3] - body_box[1])
    return {'height': (part_box[3] - part_box[1]) * scale,
            'x': ((part_box[0] + part_box[2] - body_box[0] - body_box[2]) / 2) * scale,
            'z': (part_box[1] - body_box[1]) * scale}


class PartSequence:
    """실패·취소 이후 다음 부품을 요청하지 않는 순차 커서."""
    def __init__(self):
        self.index = 0
        self.stopped = False

    @property
    def current(self):
        return None if self.stopped or self.index >= len(PARTS) else PARTS[self.index]

    def advance(self):
        if self.stopped:
            return False
        self.index += 1
        return self.current is not None

    def stop(self):
        self.stopped = True


class CharacterPartsMixin:
    """세션 스케줄러를 사용하는 시트→셰이프→리토폴로지 부품 파이프라인."""
    def _start_character_parts(self):
        self._parts_sequence = PartSequence()
        self._part_records = {}
        self._orig_masks = None   # 원본 턴어라운드의 뷰별 실루엣 격자 — 환각 부품 판정 기준 (첫 부품 처리 시 계산)
        self._presence_checked = set()
        self._next_part_sheet()

    def _original_masks(self):
        """원본 턴어라운드(정면·좌측면)의 실루엣 격자. 부품 시트와 같은 카메라·축척이므로 직접 비교할 수 있다."""
        if self._orig_masks is None:
            import bpy
            from . import shapegen
            views = shapegen.split_turnaround(self.multiview, os.path.join(self.workdir, 'parts', 'original_views'))
            masks = {}
            for view in ('front', 'left'):
                image = bpy.data.images.load(views[view], check_existing=False)
                try:
                    pixels = list(image.pixels)
                    masks[view] = silhouette_mask(pixels, *image.size)
                finally:
                    bpy.data.images.remove(image)
            self._orig_masks = masks
        return self._orig_masks

    def _parts_alive(self, part):
        return (not self._stale() and not getattr(self, '_cancel_requested', False)
                and self._parts_sequence.current == part)

    def _parts_failed(self, detail):
        self._parts_sequence.stop()
        self._finish(f'부품 분리 실패: {detail}', ok=False)

    def _next_part_sheet(self):
        from . import multiview, styles
        part = self._parts_sequence.current
        if part is None:
            self.last_code = ''
            self.compare_turns_left = 0
            self._finalize()
            return
        directory = os.path.join(self.workdir, 'parts', part.lower())
        os.makedirs(directory, exist_ok=True)
        if part != 'BODY' and part not in self._presence_checked and self._check_part_presence(part, directory):
            return
        self._set_status(f'{LABELS[part]} 분리 시트 생성중...', phase='GEN')
        self._submit_ai(lambda: multiview.generate(
            part_prompt(self.request, part), directory, self.prefs.timeout,
            lambda path, error=None: self._on_part_sheet(part, path, error),
            ref_image=self.multiview, job_key=self.uid,
            style_note=styles.image_note(self.style), sheet='TURNAROUND',
            prompt_override=part_prompt(self.request, part) + '\n' + styles.image_note(self.style)))

    def _check_part_presence(self, part, directory):
        """원화를 비전 모델에 보여 부품 유무를 묻는다. CLI 가 없으면 False(바로 시트 생성)."""
        from . import runner
        exe = presence_cli()
        self._presence_checked.add(part)
        if not exe or not self.multiview or not os.path.exists(self.multiview):
            return False
        self._set_status(f'{LABELS[part]} 유무 판독중 (원화)...', phase='GEN')
        self._submit_ai(lambda: runner.run_cli_async(
            presence_command(exe, directory, self.multiview), directory, min(int(self.prefs.timeout), 180),
            lambda out, error=None: self._on_part_presence(part, directory, out, error),
            stdin_text=presence_prompt(self.request, part), job_key=self.uid))
        return True

    def _on_part_presence(self, part, directory, out, error):
        from . import scheduler
        if not self._parts_alive(part):
            return
        scheduler.release_ai(self.uid)
        text = out or ''
        answer_file = os.path.join(directory, 'last_message.txt')
        if os.path.exists(answer_file):
            try:
                with open(answer_file, encoding='utf-8') as f:
                    text = f.read() or text
            except OSError:
                pass
        verdict = parse_presence(text)
        if verdict is False:
            self._set_status(f'{LABELS[part]} 없음', f'부품 없음: {LABELS[part]} — 원화 판독 결과 없음(NO), 시트·셰이프 생략')
            self._parts_sequence.advance()
            self._next_part_sheet()
            return
        if verdict is None:
            self._set_status(f'{LABELS[part]} 유무 판독 실패', f'{LABELS[part]} 유무 판독 실패({error or "답을 읽을 수 없음"}) — 시트 생성으로 진행')
        self._next_part_sheet()

    def _on_part_sheet(self, part, path, error):
        from . import scheduler
        if not self._parts_alive(part):
            return
        scheduler.release_ai(self.uid)
        if not path:
            self._parts_failed(f'{LABELS[part]} 시트: {error}')
            return
        self._submit_blender(lambda: self._prepare_part_shape(part, path))

    def _prepare_part_shape(self, part, path):
        import bpy
        from . import shapegen, multiview
        directory = os.path.dirname(path)
        views = shapegen.split_turnaround(path, os.path.join(directory, 'views'))
        boxes, sizes, masks = {}, {}, {}
        for view in ('front', 'back', 'left', 'right'):
            image = bpy.data.images.load(views[view], check_existing=False)
            try:
                width, height = image.size
                sizes[view] = (width, height)
                pixels = list(image.pixels)
                boxes[view] = silhouette_bbox(pixels, width, height)
                if view in ('front', 'left'):
                    masks[view] = silhouette_mask(pixels, width, height)
            finally:
                bpy.data.images.remove(image)
        if not boxes['front']:
            if part == 'BODY' or any(boxes.values()):
                self._parts_failed(f'{LABELS[part]} 정면 실루엣이 비어 있어 정렬할 수 없습니다')
                return
            self._set_status(f'{LABELS[part]} 없음', f'부품 없음: {LABELS[part]} — 빈 시트, 셰이프 요청 생략')
            self._parts_sequence.advance()
            self._next_part_sheet()
            return
        if part != 'BODY':
            # 생성 모델은 "없으면 빈 시트" 지시를 자주 어기고 검·방패 같은 부품을 지어낸다(실측). 원본 실루엣과
            # 같은 카메라·축척이므로, 부품 대부분이 원본 실루엣 밖이면 없는 부품으로 판정해 셰이프 비용을 쓰지 않는다.
            original = self._original_masks()
            ratio = outside_ratio(masks['front'], original['front'])
            if ratio >= HALLUCINATION_RATIO:
                self._set_status(f'{LABELS[part]} 없음',
                                 f'부품 없음: {LABELS[part]} — 시트 실루엣의 {ratio:.0%}가 원본 캐릭터 밖 (없는 부품을 그린 것으로 판정), 생략')
                self._parts_sequence.advance()
                self._next_part_sheet()
                return
        scale_note = ''
        if part != 'BODY' and not sizes_match(sizes, self._part_records['BODY']['sizes']):
            body_sizes = self._part_records['BODY']['sizes']
            if not sizes_match(sizes, body_sizes, tolerance=SIZE_SKIP_TOLERANCE):
                self._set_status(f'{LABELS[part]} 생략',
                                 f'부품 생략: {LABELS[part]} — 시트 격자가 몸체 시트와 {SIZE_SKIP_TOLERANCE:.0%} 이상 달라 공통 축척을 확인할 수 없음')
                self._parts_sequence.advance()
                self._next_part_sheet()
                return
            # bbox 는 칸 안에서 정규화된 좌표라 칸 크기 차이는 배치에 영향이 없다 — 기록만 남긴다
            scale_note = ' (시트 격자 크기가 몸체와 다름, 정규화 좌표로 진행)'
        archived = multiview.archive(path, f'{self.request}_{part}')
        self._part_records[part] = {'sheet': archived or path, 'boxes': boxes, 'sizes': sizes}
        self._set_status(f'{LABELS[part]} 셰이프 생성중...{scale_note}', phase='GEN')
        self._submit_ai(lambda: shapegen.generate(
            views, os.path.join(directory, 'shape.glb'), max(self.prefs.timeout, 600),
            lambda result, error=None: self._on_part_shape(part, result, error),
            job_key=self.uid, face_count=0, preserve_parts=True))

    def _on_part_shape(self, part, path, error):
        from . import scheduler
        if not self._parts_alive(part):
            return
        scheduler.release_ai(self.uid)
        if not path:
            self._parts_failed(f'{LABELS[part]} 셰이프: {error}')
            return
        self._submit_blender(lambda: self._retopo_part(part, path))

    def _retopo_part(self, part, path):
        import bpy
        from ..lowpoly import retopo, set_session
        set_session(self.collection_name)
        coll = bpy.data.collections.get(self.collection_name)
        if coll is None:
            coll = bpy.data.collections.new(self.collection_name)
            bpy.data.scenes[self.scene_name].collection.children.link(coll)
        record = self._part_records[part]
        body = self._part_records['BODY']
        height = float(getattr(self.prefs, 'character_height', 1.8))
        target = placement(body['boxes']['front'], record['boxes']['front'], height)
        method = str(getattr(self.prefs, 'shapegen_method', 'TEMPLATE'))
        if part != 'BODY' and method == 'TEMPLATE':
            method = 'QUADRIFLOW'
        base_faces = int(getattr(self.prefs, 'shapegen_faces', 12000))
        # 부품 예산은 몸체 대비 실루엣 면적비로 — 몸체만 기준값을 그대로 받는다
        faces = base_faces if part == 'BODY' else part_face_budget(
            base_faces, body['boxes']['front'], record['boxes']['front'])
        info = retopo.process_glb(
            path, f'{self.collection_name}_{part}', coll, height=target['height'],
            target_faces=faces, method=method,
            template_id=self.character_template, preserve_parts=True,
            adaptive=bool(getattr(self.prefs, 'shapegen_adaptive', False)))
        obj = info['obj']
        # 정면 칸의 가로/세로 비율을 실제 좌표 비율에 반영한다.
        width, image_height = record['sizes']['front']
        obj.location.x += target['x'] * width / image_height
        obj.location.z += target['z']
        if part != 'BODY' and body['boxes']['left'] and record['boxes']['left']:
            side = placement(body['boxes']['left'], record['boxes']['left'], height)
            sw, sh = record['sizes']['left']
            obj.location.y -= side['x'] * sw / sh
        carve_note = ''
        if part == 'OUTFIT' and body.get('object') and bool(getattr(self.prefs, 'shapegen_carve_outfit', False)):
            body_obj = bpy.data.objects.get(body['object'])
            if body_obj is not None:
                carve = retopo.carve_body(obj, body_obj, height)
                carve_note = (f', 몸체 차감 {carve["before"]:,}→{carve["faces"]:,}면(파편 {carve["dropped"]}개 제거)'
                              if carve['ok'] else f', 몸체 차감 취소({carve.get("reason", "실패")}) — 옷 입은 전신 셸 유지, 옷 속 몸체 면은 수동 삭제')
        elif part == 'OUTFIT':
            carve_note = ' — 옷 입은 전신 셸(옷 속 몸체 면은 모델링 완료 단계에서 직접 삭제, 자동 삭제는 환경설정에서 실험 옵션)'
        obj['lp3d_character_part'] = part
        # 매핑 단계가 세션 없이(리토폴로지 후 사용자 편집을 거쳐) 돌아도 부품 시트를 색 참조로 찾을 수 있게 남긴다
        obj['lp3d_part_sheet'] = record['sheet']
        record['object'] = obj.name
        self._set_status(f'{LABELS[part]} 생성 완료',
                         f'{LABELS[part]}: {retopo_method_note(info)}, 목표 {faces:,}면 → {info["faces"]:,}면 ({info["tris"]} tris), 시트 축척에 맞춰 정렬{carve_note}')
        self._parts_sequence.advance()
        self._next_part_sheet()
