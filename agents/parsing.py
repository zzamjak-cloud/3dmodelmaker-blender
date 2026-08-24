# 에이전트 응답 파싱: STATUS 헤더 + python 펜스 코드 블록 추출
# (확장 검증기의 백슬래시 검사 때문에 정규식 대신 문자열 파싱 사용)

_STATUS_VALUES = ("DONE", "REVISE")


def parse_agent_reply(text: str):
    """(status, code) 반환. status는 'DONE'/'REVISE'/None, code는 마지막 python 블록 또는 None."""
    if not text:
        return None, None

    status = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("STATUS:"):
            value = stripped[len("STATUS:"):].strip().upper()
            if value in _STATUS_VALUES:
                status = value
                break

    # ``` 기준으로 나누면 홀수 인덱스가 펜스 내부 — 언어 태그가 python/py인 마지막 블록 채택
    code = None
    parts = text.split("```")
    for i in range(1, len(parts), 2):
        block = parts[i]
        newline_at = block.find("\n")
        if newline_at == -1:
            continue
        lang = block[:newline_at].strip().lower()
        if lang in ("python", "py"):
            code = block[newline_at + 1:].strip()
    return status, code
