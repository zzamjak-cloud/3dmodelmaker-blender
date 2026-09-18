"""리토폴로지·조합과 언랩·매핑을 분리하기 위한 매핑 컨텍스트 저장/복원.

세션은 `_finish`에서 사라지므로, 사용자가 메시를 손본 뒤 [매핑 시작]을 누를 때 필요한 정보를
컬렉션·오브젝트 커스텀 프로퍼티(ID 프로퍼티 — dict처럼 다룬다)에 남겨 둔다. bpy 없이 테스트할 수 있게
순수 dict 연산으로만 쓴다."""
from .character_parts import PARTS

COLLECTION_KEYS = ('lp3d_request', 'lp3d_multiview', 'lp3d_style', 'lp3d_system_mode',
                   'lp3d_character_type', 'lp3d_final_note')
PART_KEY = 'lp3d_character_part'
PART_SHEET_KEY = 'lp3d_part_sheet'


def save_context(store, request: str, multiview: str, style: str, system_mode: str,
                 character_type: str, final_note: str = "") -> None:
    """컬렉션(또는 dict)에 매핑 컨텍스트를 쓴다. 값은 모두 문자열 — ID 프로퍼티는 None 을 받지 않는다."""
    store['lp3d_request'] = request or ""
    store['lp3d_multiview'] = multiview or ""
    store['lp3d_style'] = style or ""
    store['lp3d_system_mode'] = system_mode or 'OBJECT'
    store['lp3d_character_type'] = character_type or 'AUTO'
    store['lp3d_final_note'] = final_note or ""


def load_context(store) -> dict:
    """저장된 컨텍스트를 dict 로. 요청문이 없으면 매핑을 시작할 수 없으므로 ValueError."""
    request = str(store.get('lp3d_request', "") or "")
    if not request:
        raise ValueError("이 컬렉션에는 매핑 컨텍스트가 없습니다 — 리토폴로지를 마친 잡의 결과여야 합니다")
    return {
        'request': request,
        'multiview': str(store.get('lp3d_multiview', "") or "") or None,
        'style': str(store.get('lp3d_style', "") or "") or None,
        'system_mode': str(store.get('lp3d_system_mode', 'OBJECT') or 'OBJECT'),
        'character_type': str(store.get('lp3d_character_type', 'AUTO') or 'AUTO'),
        'final_note': str(store.get('lp3d_final_note', "") or ""),
    }


def part_records_from_objects(objects) -> dict:
    """오브젝트들의 부품 표식으로 세션의 `_part_records` 를 재구성한다.

    objects: (이름, 프로퍼티 dict) 반복자. 같은 부품의 오브젝트가 여럿(사용자가 분할)이어도 시트는 하나다.
    부품 표식이 없는 컬렉션이면 빈 dict — 통합 생성물로 취급한다."""
    records = {}
    for name, props in objects:
        part = props.get(PART_KEY)
        if not part:
            continue
        rec = records.setdefault(str(part), {'sheet': None, 'objects': []})
        rec['objects'].append(name)
        sheet = props.get(PART_SHEET_KEY)
        if sheet and not rec['sheet']:
            rec['sheet'] = str(sheet)
    return records


def texture_order(records: dict) -> list:
    """매핑 순서 — PARTS 순서를 따르고, 시트가 없는 부품은 색 참조가 없어 뒤로 보낸다."""
    known = [p for p in PARTS if p in records]
    extra = sorted(p for p in records if p not in PARTS)
    ordered = known + extra
    return [p for p in ordered if records[p].get('sheet')] + [p for p in ordered if not records[p].get('sheet')]
