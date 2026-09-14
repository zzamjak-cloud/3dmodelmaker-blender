# Blender ID 이름 안전화 — 점 뒤 긴 숫자 접미어는 Blender 5.2를 abort시킨다
#
# Blender는 "Name.123" 형태에서 마지막 점 뒤 숫자를 int로 파싱해 중복 회피 번호로
# 쓴다(BLI_string_split_name_number). 에이전트 코드가 f"Brace_{angle}"처럼 float를
# 이름에 넣으면 "Brace_0.7853981633974483"이 되어 stoi가 overflow → 프로세스 abort.
# 파이썬 예외가 아니라 크래시이므로 모든 ID 생성 지점에서 미리 걸러야 한다.

_INT32_MAX = 2147483647


def safe_id_name(name, fallback: str = "Object") -> str:
    """bpy.data.*.new()에 넘겨도 안전한 이름을 반환한다.

    마지막 점 뒤가 전부 숫자이고 int32 범위를 넘으면 그 점을 '_'로 바꾼다.
    빈 이름은 fallback으로 대체한다. 그 외 이름은 그대로 둔다."""
    text = str(name if name is not None else "").strip()
    if not text:
        return fallback
    dot = text.rfind(".")
    if dot == -1:
        return text
    tail = text[dot + 1:]
    if tail.isdigit() and int(tail) > _INT32_MAX:
        return text[:dot] + "_" + tail
    return text
