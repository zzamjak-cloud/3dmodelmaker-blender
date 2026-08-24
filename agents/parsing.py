# 에이전트 응답 파싱: STATUS 헤더 + python 펜스 코드 블록 추출
import re

# STATUS: DONE | REVISE — 응답 어디에 있어도 허용하되 첫 등장을 채택
_STATUS_RE = re.compile(r"^\s*STATUS:\s*(DONE|REVISE)\s*$", re.MULTILINE)
_FENCE_RE = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.DOTALL)


def parse_agent_reply(text: str):
    """(status, code) 반환. status는 'DONE'/'REVISE'/None, code는 마지막 python 블록 또는 None."""
    if not text:
        return None, None
    status_match = _STATUS_RE.search(text)
    status = status_match.group(1) if status_match else None
    blocks = _FENCE_RE.findall(text)
    code = blocks[-1].strip() if blocks else None
    return status, code
