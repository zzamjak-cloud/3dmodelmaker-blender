"""리토폴로지와 언랩·매핑을 분리하기 위한 매핑 컨텍스트 저장/복원.

세션은 `_finish`에서 사라지므로, 사용자가 메시를 손본 뒤 [매핑 시작]을 누를 때 필요한 정보를
컬렉션 커스텀 프로퍼티(ID 프로퍼티 — dict처럼 다룬다)에 남겨 둔다. bpy 없이 테스트할 수 있게
순수 dict 연산으로만 쓴다."""
COLLECTION_KEYS = ('lp3d_request', 'lp3d_multiview', 'lp3d_style', 'lp3d_system_mode',
                   'lp3d_character_type', 'lp3d_final_note')


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
