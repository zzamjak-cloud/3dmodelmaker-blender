"""캐릭터 부품을 순차 생성하고 공통 시트 좌표로 배치한다."""
import os

PARTS = ('BODY', 'OUTFIT', 'WEAPON')
LABELS = {'BODY': '몸체', 'OUTFIT': '의상·갑옷', 'WEAPON': '무기'}


def retopo_method_note(info):
    """실제 처리 방법과 대체 처리 사유를 사용자 로그용으로 반환한다."""
    note = str(info['method'])
    if info.get('fallback_reason'):
        note += f' (대체 처리: {info["fallback_reason"]})'
    return note


def part_prompt(request, part):
    """원본 턴어라운드의 카메라와 축척을 고정한 부품 요청을 만든다."""
    detail = {
        'BODY': '의상·갑옷·무기를 제거하고 가려진 몸체 전체를 복원한다. 머리카락·털은 몸체에 포함한다. 인간형은 불투명하고 장식 없는 밀착형 기본복을 입혀 몸체를 표현한다.',
        'OUTFIT': '의상·갑옷·신발·착용 장비만 남기고 몸체·머리카락·무기는 제거한다. 빈 공간은 흰색이다.',
        'WEAPON': '손에 들거나 등에 장착한 무기만 원래 위치에 남기고 몸체·의상·갑옷은 제거한다.',
    }[part]
    return (f'{request}\n부품 분리: {LABELS[part]}. {detail}\n'
            '첨부 원본 턴어라운드와 동일한 3x2 칸, 카메라, 자세, 축척, 여백, 발바닥 위치를 유지한다. '
            '윗줄은 정면(FRONT), 뒷면(BACK), 좌측면(LEFT); 아랫줄은 우측면(RIGHT), 상면(TOP), 3/4뷰이다. '
            '각 칸 상단 12%에만 라벨을 표기하고 그 아래에는 글자를 넣지 않는다. '
            '작은 부품을 확대하거나 중앙으로 이동하지 않는다. 모든 배경은 순백색, 그림자 금지. '
            '해당 부품이 원본에 없으면 라벨을 제외한 모든 칸을 완전히 흰색으로 둔다. 없는 장비를 만들어내지 않는다.')


def silhouette_bbox(pixels, width, height):
    """흰 배경 이미지의 불투명 실루엣 범위를 정규화 좌표로 반환한다."""
    xs, ys = [], []
    for y in range(height):
        for x in range(width):
            i = (y * width + x) * 4
            if pixels[i + 3] > .1 and min(pixels[i:i + 3]) < .90:
                xs.append(x)
                ys.append(y)
    if len(xs) < max(4, width * height * .0003):
        return None
    return min(xs) / width, min(ys) / height, (max(xs) + 1) / width, (max(ys) + 1) / height


def part_texture_prompt(request, part):
    """출력 격자는 가이드가 정하고 부품 범위와 색 참조만 요청에 추가한다."""
    prompt = (f'{request}\n채색 대상은 {LABELS[part]} 부품만이다. 첨부 부품 원화의 색과 재질을 유지한다. '
              '가이드에 없는 몸체·얼굴·다른 장비를 새로 그리지 않는다.')
    if part == 'BODY':
        prompt += ' 몸체의 장비에 가려진 면도 몸체 재질로 채색한다.'
    return prompt


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
        self._next_part_sheet()

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
        self._set_status(f'{LABELS[part]} 분리 시트 생성중...', phase='GEN')
        self._submit_ai(lambda: multiview.generate(
            part_prompt(self.request, part), directory, self.prefs.timeout,
            lambda path, error=None: self._on_part_sheet(part, path, error),
            ref_image=self.multiview, job_key=self.uid,
            style_note=styles.image_note(self.style), sheet='TURNAROUND',
            prompt_override=part_prompt(self.request, part) + '\n' + styles.image_note(self.style)))

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
        boxes, sizes = {}, {}
        for view in ('front', 'back', 'left', 'right'):
            image = bpy.data.images.load(views[view], check_existing=False)
            try:
                width, height = image.size
                sizes[view] = (width, height)
                boxes[view] = silhouette_bbox(list(image.pixels), width, height)
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
        if part != 'BODY' and sizes != self._part_records['BODY']['sizes']:
            self._parts_failed(f'{LABELS[part]} 시트 크기가 몸체 시트와 달라 공통 축척을 확인할 수 없습니다')
            return
        archived = multiview.archive(path, f'{self.request}_{part}')
        self._part_records[part] = {'sheet': archived or path, 'boxes': boxes, 'sizes': sizes}
        self._set_status(f'{LABELS[part]} Hunyuan3D 생성중...', phase='GEN')
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
        info = retopo.process_glb(
            path, f'{self.collection_name}_{part}', coll, height=target['height'],
            target_faces=int(getattr(self.prefs, 'shapegen_faces', 12000)), method=method,
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
        obj['lp3d_character_part'] = part
        record['object'] = obj.name
        self._set_status(f'{LABELS[part]} 생성 완료',
                         f'{LABELS[part]}: {retopo_method_note(info)}, {info["tris"]} tris, 시트 축척에 맞춰 정렬')
        self._parts_sequence.advance()
        self._next_part_sheet()
