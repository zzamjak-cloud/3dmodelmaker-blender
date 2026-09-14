# 에이전트 응답 파싱: STATUS 헤더 + 펜스 코드 블록 추출
# (확장 검증기의 백슬래시 검사 때문에 정규식 대신 문자열 파싱 사용)

_STATUS_VALUES = ("DONE", "REVISE", "PLAN")


def parse_agent_block(text: str, langs=("python", "py")):
    """(status, body) 반환 — 지정한 언어 태그가 붙은 마지막 펜스 블록의 본문을 고른다.

    langs를 바꾸면 같은 파싱 규칙을 다른 출력 형식에도 쓸 수 있다
    (배경 모드의 플랜 턴은 python 대신 json 블록을 돌려준다)."""
    if not text:
        return None, None

    wanted = tuple(lang.lower() for lang in langs)
    status = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("STATUS:"):
            value = stripped[len("STATUS:"):].strip().upper()
            if value in _STATUS_VALUES:
                status = value
                break

    # ``` 기준으로 나누면 홀수 인덱스가 펜스 내부 — 언어 태그가 맞는 마지막 블록 채택
    body = None
    parts = text.split("```")
    for i in range(1, len(parts), 2):
        block = parts[i]
        newline_at = block.find("\n")
        if newline_at == -1:
            continue
        lang = block[:newline_at].strip().lower()
        if lang in wanted:
            body = block[newline_at + 1:].strip()
    return status, body


def parse_agent_reply(text: str):
    """(status, code) 반환. status는 'DONE'/'REVISE'/'PLAN'/None, code는 마지막 python 블록 또는 None."""
    return parse_agent_block(text)
