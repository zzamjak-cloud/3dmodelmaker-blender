"""캐릭터 부품을 각각 독립 잡처럼 생성한다 — 부품마다 자체 턴어라운드 → 셰이프 → 리토폴로지 → 나란히 배치.

이전(v0.19~v0.21.0)의 결합 구조(원본 턴어라운드와 같은 카메라·축척을 강제하고 실루엣으로 자동 배치·면수 배분·환각
게이트)는 부품마다 다시 그려진 시트와 따로 재구성된 셰이프가 수 cm 씩 어긋나 조합 결과가 통합 생성보다 나빴다(실측
2026-09-18). 지금은 부품 사이의 결합을 모두 없애고 각 부품을 캔버스에 크게, 전체 면수 예산으로 만든다. 조합(위치·
축척 미세 조정)은 사용자가 모델링 완료 단계에서 직접 한다."""
import os

# 순서가 곧 생성 순서다. 악세사리는 의상에 포함한다 — 벨트·고리처럼 몸을 감싸는 소품만 따로 생성하면
# TRELLIS 가 고리 안을 채워 슬랩이 된다(실측)
PARTS = ('BODY', 'OUTFIT', 'WEAPON')
LABELS = {'BODY': '몸체', 'OUTFIT': '의상·악세사리', 'WEAPON': '무기·방어구'}

# 부품 키(캐릭터 키 대비) — 원화 판독이 비율을 주지 못했을 때의 기본값. 몸체는 환경설정 키 그대로
DEFAULT_HEIGHT_RATIO = {'BODY': 1.0, 'OUTFIT': 0.8, 'WEAPON': 0.6}
ROW_GAP = 0.3   # 부품을 나란히 놓을 때 간격(m)


def retopo_method_note(info):
    """실제 처리 방법과 대체 처리 사유를 사용자 로그용으로 반환한다."""
    note = str(info['method'])
    if info.get('fallback_reason'):
        note += f' (대체 처리: {info["fallback_reason"]})'
    return note


PART_SUBJECT = {
    # 표현 주의: "의상 제거·가려진 몸체 복원·밀착"은 이미지 모델 안전 필터에 걸린다(실측: 소년 캐릭터 요청 거부). 마네킹 기본형으로 요청한다
    'BODY': ('캐릭터의 기본 몸체 마네킹만. 갑옷·악세사리·무기·겉옷 없이, 단색(회색 또는 베이지) 반팔 상의와 반바지를 입은 '
             '단순한 마네킹 형태의 T-포즈(팔을 좌우로 수평, 다리 살짝 벌림). 머리카락·얼굴·손·발은 캐릭터 그대로, 장식·무늬 없음.'),
    'OUTFIT': ('의상과 악세사리만. 옷·신발·장갑·모자와 배낭·가방·벨트·주머니·스카프·장신구·안경·리본 같은 착용 소품을 '
               '몸에 입혀진 그대로의 입체 형태(T-포즈 배치)로 그리되 몸체·머리카락·피부·무기·방어구는 그리지 않는다. 옷 안쪽은 비어 보이지 않게 두께를 준다.'),
    'WEAPON': ('무기와 방어구만. 손에 들거나 등에 장착한 무기(검·창·활·지팡이·총 등)와 방패·투구·갑옷·견갑을 모두, '
               '서로 겹치지 않게 한 칸 안에 나란히 세워 그린다. 몸체·의상은 그리지 않는다.'),
}


def part_prompt(request, part):
    """부품 하나를 독립 오브젝트로 그리는 3x2 턴어라운드 요청. 첨부 참조는 디자인·색을 맞추는 용도다."""
    return (f'{request}\n대상: {LABELS[part]} — {PART_SUBJECT[part]}\n'
            '첨부 참조 이미지의 디자인·비율·색을 그대로 따른다(다른 디자인으로 바꾸지 않는다). '
            '출력은 3x2 칸 턴어라운드: 윗줄 정면(FRONT), 뒷면(BACK), 좌측면(LEFT); 아랫줄 우측면(RIGHT), 상면(TOP), 3/4뷰. '
            '모든 칸에서 대상이 같은 축척으로 칸의 80~90%를 차지하도록 크게 그리고 칸 중앙에 둔다. '
            '각 칸 상단 12%에만 라벨을 표기하고 그 아래에는 글자를 넣지 않는다. 배경은 순백색, 그림자·바닥·격자 잔재 금지. '
            '해당 요소가 참조에 없으면 라벨을 제외한 모든 칸을 완전히 흰색으로 둔다. 없는 것을 만들어내지 않는다.')


def part_texture_prompt(request, part):
    """출력 격자는 가이드가 정하고 부품 범위와 색 참조만 요청에 추가한다."""
    prompt = (f'{request}\n채색 대상은 {LABELS[part]} 부품만이다. 첨부 부품 원화의 색과 재질을 유지한다. '
              '가이드에 없는 몸체·얼굴·다른 장비를 새로 그리지 않는다.')
    if part == 'BODY':
        prompt += ' 몸체의 장비에 가려진 면도 몸체 재질로 채색한다.'
    return prompt


WHITE = .90       # RGB 최소값이 이보다 밝으면 배경
MIN_INK = .0003   # 칸 픽셀의 이 비율 미만만 어두우면 빈 칸(라벨 글자만 있는 정도)


def has_content(pixels, width, height, label_zone: float = 0.12) -> bool:
    """칸에 라벨을 제외한 그림이 있는가. bpy 픽셀은 좌하단 원점 — 라벨은 위쪽(마지막 행들)에 있다."""
    top = int(height * (1 - label_zone))
    ink = 0
    for y in range(0, top):
        base = y * width
        for x in range(width):
            i = (base + x) * 4
            if pixels[i + 3] > .1 and min(pixels[i], pixels[i + 1], pixels[i + 2]) < WHITE:
                ink += 1
    return ink >= max(4, width * height * MIN_INK)


# 시트를 만들기 전에 원화를 비전 모델에 보여 부품 유무와 대략 크기를 먼저 묻는다. 이미지 생성 모델은 "없으면 빈 시트"
# 지시를 자주 어기고 검·방패를 지어낸다(실측). 질문 한 번은 시트 1장·셰이프 1건보다 훨씬 싸다.
PRESENCE_TARGET = {
    'OUTFIT': '옷·신발·장갑·모자, 또는 배낭·가방·벨트·주머니·스카프·장신구·안경·리본 같은 착용 소품',
    'WEAPON': '손에 들거나 등에 장착한 무기(검·창·활·지팡이·총 등), 또는 방패·투구·갑옷·견갑 같은 방어구',
}


def presence_prompt(request: str, part: str) -> str:
    return (f'첨부 이미지는 다음 캐릭터의 턴어라운드 원화다: {request}\n'
            f'질문: 이 캐릭터가 {PRESENCE_TARGET[part]}를 착용하거나 소지하고 있는가?\n'
            '그림에 실제로 보이는 것만 근거로 판단한다. 있으면 "YES 비율" 형식으로 답한다 — 비율은 그 요소들을 세워 놓았을 때의 '
            '세로 높이를 캐릭터 키로 나눈 값(예: YES 0.65). 없으면 NO 한 단어만. 다른 설명은 쓰지 않는다.')


def presence_command(exe: str, work_dir: str, image: str) -> list:
    """codex exec 읽기 전용 — 이미지 1장 첨부, 프롬프트는 stdin, 최종 답은 last_message.txt."""
    return [exe, 'exec', '-s', 'read-only', '--skip-git-repo-check', '--cd', work_dir,
            '-o', os.path.join(work_dir, 'last_message.txt'), '-i', image, '-']


def parse_presence(text):
    """모델 답 → (있는가, 키 대비 비율 또는 None). YES/NO 를 못 읽으면 (None, None)."""
    raw = text or ''
    words = [w.strip('.') for w in ''.join(ch if ch.isalnum() or ch == '.' else ' ' for ch in raw).upper().split()]
    yes, no = 'YES' in words, 'NO' in words
    if yes == no:
        return None, None
    if no:
        return False, None
    ratio = None
    for w in words[words.index('YES') + 1:]:
        try:
            value = float(w)
        except ValueError:
            continue
        if 0.05 <= value <= 3.0:
            ratio = value
        break
    return True, ratio


def presence_cli():
    """유무 판독에 쓸 CLI 경로. 없으면 '' — 판독을 건너뛰고 시트 생성으로 간다(검증 스크립트가 패치하기도 한다)."""
    from .. import preferences
    return preferences.resolve_cli_path('CODEX') or ''


def row_layout(widths: list, gap: float = ROW_GAP) -> list:
    """부품을 x 축으로 나란히 놓는 중심 x 좌표들. 첫 부품(몸체)이 원점, 다음은 오른쪽으로 폭 절반씩 + 간격."""
    xs, cursor = [], 0.0
    for i, w in enumerate(widths):
        if i == 0:
            xs.append(0.0)
            cursor = w / 2
            continue
        cursor += gap + w / 2
        xs.append(cursor)
        cursor += w / 2
    return xs


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
    """세션 스케줄러를 사용하는 부품별 독립 시트→셰이프→리토폴로지 파이프라인."""
    def _start_character_parts(self):
        self._parts_sequence = PartSequence()
        self._part_records = {}
        self._presence_checked = set()
        self._part_heights = {}   # 원화 판독이 준 키 대비 비율
        self._next_part_sheet()

    def _parts_alive(self, part):
        return (not self._stale() and not getattr(self, '_cancel_requested', False)
                and self._parts_sequence.current == part)

    def _parts_failed(self, detail):
        self._parts_sequence.stop()
        self._finish(f'부품 분리 실패: {detail}', ok=False)

    def _part_reference(self):
        """부품 시트의 디자인 참조 — 원본 턴어라운드(6면)가 있으면 그것, 없으면 사용자의 원화."""
        for candidate in (self.multiview, getattr(self, 'ref_image', None)):
            if candidate and os.path.exists(candidate):
                return candidate
        return None

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
        prompt = part_prompt(self.request, part)
        self._submit_ai(lambda: multiview.generate(
            prompt, directory, self.prefs.timeout,
            lambda path, error=None: self._on_part_sheet(part, path, error),
            ref_image=self._part_reference(), job_key=self.uid,
            style_note=styles.image_note(self.style), sheet='TURNAROUND',
            prompt_override=prompt + '\n' + styles.image_note(self.style)))

    def _check_part_presence(self, part, directory):
        """원화를 비전 모델에 보여 부품 유무·크기를 묻는다. CLI 나 참조가 없으면 False(바로 시트 생성)."""
        from . import runner
        exe = presence_cli()
        self._presence_checked.add(part)
        reference = self._part_reference()
        if not exe or not reference:
            return False
        self._set_status(f'{LABELS[part]} 유무 판독중 (원화)...', phase='GEN')
        self._submit_ai(lambda: runner.run_cli_async(
            presence_command(exe, directory, reference), directory, min(int(self.prefs.timeout), 180),
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
        present, ratio = parse_presence(text)
        if present is False:
            self._set_status(f'{LABELS[part]} 없음', f'부품 없음: {LABELS[part]} — 원화 판독 결과 없음(NO), 시트·셰이프 생략')
            self._parts_sequence.advance()
            self._next_part_sheet()
            return
        if present is None:
            self._set_status(f'{LABELS[part]} 유무 판독 실패', f'{LABELS[part]} 유무 판독 실패({error or "답을 읽을 수 없음"}) — 시트 생성으로 진행')
        elif ratio:
            self._part_heights[part] = ratio
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
        image = bpy.data.images.load(views['front'], check_existing=False)
        try:
            filled = has_content(list(image.pixels), *image.size)
        finally:
            bpy.data.images.remove(image)
        if not filled:
            if part == 'BODY':
                self._parts_failed('몸체 시트의 정면 칸이 비어 있습니다')
                return
            self._set_status(f'{LABELS[part]} 없음', f'부품 없음: {LABELS[part]} — 빈 시트, 셰이프 요청 생략')
            self._parts_sequence.advance()
            self._next_part_sheet()
            return
        archived = multiview.archive(path, f'{self.request}_{part}')
        self._part_records[part] = {'sheet': archived or path}
        self._set_status(f'{LABELS[part]} 셰이프 생성중...', phase='GEN')
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

    def _part_height(self, part):
        base = float(getattr(self.prefs, 'character_height', 1.8))
        return base * float(self._part_heights.get(part, DEFAULT_HEIGHT_RATIO.get(part, 1.0)))

    def _retopo_part(self, part, path):
        import bpy
        from ..lowpoly import retopo, set_session
        set_session(self.collection_name)
        coll = bpy.data.collections.get(self.collection_name)
        if coll is None:
            coll = bpy.data.collections.new(self.collection_name)
            bpy.data.scenes[self.scene_name].collection.children.link(coll)
        record = self._part_records[part]
        method = str(getattr(self.prefs, 'shapegen_method', 'TEMPLATE'))
        if part != 'BODY' and method == 'TEMPLATE':
            method = 'QUADRIFLOW'
        faces = int(getattr(self.prefs, 'shapegen_faces', 12000))   # 부품마다 전체 예산 — 독립 오브젝트다
        height = self._part_height(part)
        info = retopo.process_glb(
            path, f'{self.collection_name}_{part}', coll, height=height,
            target_faces=faces, method=method,
            template_id=self.character_template, preserve_parts=True,
            adaptive=bool(getattr(self.prefs, 'shapegen_adaptive', False)))
        obj = info['obj']
        # 몸체 원점, 나머지는 오른쪽으로 나란히 — 조합은 사용자가 모델링 완료 단계에서 한다
        bpy.context.view_layer.update()
        placed = [bpy.data.objects.get(r['object']) for r in self._part_records.values() if r.get('object')]
        widths = [o.dimensions.x for o in placed if o is not None] + [obj.dimensions.x]
        obj.location.x += row_layout(widths)[-1]
        obj['lp3d_character_part'] = part
        # 매핑 단계가 세션 없이(리토폴로지 후 사용자 편집을 거쳐) 돌아도 부품 시트를 색 참조로 찾을 수 있게 남긴다
        obj['lp3d_part_sheet'] = record['sheet']
        record['object'] = obj.name
        if part == 'BODY':
            height_note = f'키 {height:.2f}m'
        else:
            height_note = f'키 {height:.2f}m ({"원화 판독 비율" if part in self._part_heights else "기본 비율"})'
        self._set_status(f'{LABELS[part]} 생성 완료',
                         f'{LABELS[part]}: {retopo_method_note(info)}, 목표 {faces:,}면 → {info["faces"]:,}면 ({info["tris"]} tris), {height_note}, 나란히 배치')
        self._parts_sequence.advance()
        self._next_part_sheet()
