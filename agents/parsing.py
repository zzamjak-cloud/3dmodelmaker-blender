# 에이전트 응답 파싱: STATUS 헤더 + 펜스 코드 블록 추출
# (확장 검증기의 백슬래시 검사 때문에 코드 블록은 정규식 대신 문자열 파싱 사용)
import re

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


_VERDICT_RE = re.compile(r"^[\s*_`#>]*VERDICT\s*:\s*[*_`]*\s*(OK|FIX)\b[*_`]*\s*(.*)$",
                         re.IGNORECASE)
_LIST_MARK_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def parse_verdict(text: str):
    """검토 판정 턴 응답 → (ok, defects). ok는 True(이상 없음)/False(수정 필요)/None(형식 위반).

    판정 턴은 코드를 쓰지 않고 `VERDICT: OK` 또는 `VERDICT: FIX` 한 줄과 결함 목록만 돌려준다 —
    전체 코드 재작성이라는 가장 비싼 출력을 결함이 있을 때만 일으키기 위해서다.
    모델이 헤더를 굵게·백틱으로 감싸거나 결함을 같은 줄에 적어도 받아들인다."""
    ok = None
    defects = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if ok is None:
            m = _VERDICT_RE.match(stripped)
            if not m:
                continue
            ok = m.group(1).upper() == "OK"
            tail = m.group(2).strip(" -—:*_`")
            if not ok and tail:
                defects.append(tail)   # "VERDICT: FIX — 바퀴 떠 있음"
            continue
        if ok is False and _LIST_MARK_RE.match(stripped):
            # "- 결함", "1. 결함", "1) 결함" — 목록 표식만 떼고 "0.2m 내려라" 같은 본문 숫자는 남긴다
            body = _LIST_MARK_RE.sub("", stripped, count=1).strip()
            if body:
                defects.append(body)
    return ok, defects
