# CLI 실행 실패를 사용자가 조치할 수 있는 메시지로 바꾼다
#
# runner는 "CLI 종료 코드 1\n<stderr 마지막 1500자>" 형태로 오류를 넘긴다.
# 예전에는 상태줄에 첫 줄만 찍어서 "CLI 종료 코드 1"만 보였고, 정작 원인인
# 401 토큰 만료가 완전히 가려졌다. 여기서 원인을 분류해 조치 문구를 붙이고,
# 분류에 실패해도 최소한 의미 있는 상세 줄을 함께 노출한다.
import re

AUTH = 'AUTH'        # 로그인/토큰 만료
QUOTA = 'QUOTA'      # 사용량·크레딧 소진
NETWORK = 'NETWORK'  # 연결 실패
TIMEOUT = 'TIMEOUT'  # 시간 초과
MISSING = 'MISSING'  # 실행 파일 없음
UNKNOWN = ''

# 분류 규칙 — 위에서부터 먼저 맞는 것을 쓴다 (구체적인 패턴이 앞)
_RULES = (
    (AUTH, (
        "refresh_token_invalidated", "token_revoked", "invalid_token",
        "unauthorized", "401", "authentication_error", "invalid api key",
        "please log out and sign in again", "your session has ended",
        "not logged in", "please run /login", "oauth token",
    )),
    (QUOTA, (
        "insufficient_quota", "rate_limit", "quota exceeded", "credit balance",
        "usage limit", "429",
    )),
    (NETWORK, (
        "connection refused", "failed to lookup address", "dns error",
        "network is unreachable", "connection reset", "timed out connecting",
    )),
    (TIMEOUT, ("시간 초과",)),
    (MISSING, ("실행 파일 없음",)),
)

# 조치 안내 — {cli}에 실제 CLI 이름이 들어간다
# 사이드바 상태줄은 폭이 좁아 한 줄에 원인+조치를 넣으면 가운데가 잘린다.
# (원인, 조치) 두 조각으로 나눠 패널이 각각 다른 줄에 그리게 한다.
_HINTS = {
    AUTH: ("{cli} 로그인 만료", "터미널에서 `{login}` 실행 후 다시 시도"),
    QUOTA: ("{cli} 사용량 소진", "잠시 후 다시 시도하거나 요금제를 확인하세요"),
    NETWORK: ("네트워크 연결 실패", "인터넷·프록시 설정을 확인하세요"),
    TIMEOUT: ("{cli} 응답 시간 초과", "환경설정에서 타임아웃을 늘려보세요"),
    MISSING: ("{cli} 실행 파일 없음", "환경설정에서 CLI 경로를 지정하세요"),
}

_LOGIN_CMD = {"codex": "codex login"}

# stderr 잡음(MCP 서버 연결 실패 등)은 원인이 아니므로 상세 줄에서 제외한다
_NOISE = ("rmcp::transport", "mcp client", "worker quit with fatal")

# "2026-09-02T01:31:04.484875Z ERROR codex_login::auth::manager: " 같은 로그 접두어
_LOG_PREFIX = re.compile(
    r"^\d{4}-\d\d-\d\dT[\d:.]+Z?\s+(?:ERROR|WARN|WARNING|INFO|DEBUG|TRACE)\s+[\w:.]+:\s*",
    re.IGNORECASE,
)
# JSON 조각만 남은 줄(`{`, `}`, `"param": null,`)은 사람이 읽을 정보가 없다
_JSON_FRAGMENT = re.compile(r'^[{}\[\],]*$|^"[\w_]+"\s*:\s*(null|\{|\[)?,?$')


def _norm(text: str) -> str:
    return (text or "").lower()


def tail(text: str, limit: int = 1500) -> str:
    """끝에서 limit자를 잘라내되, 중간에서 잘린 첫 줄은 버린다.

    그냥 [-limit:]만 하면 첫 줄이 "horized, url: wss://..."처럼 조각으로 남아
    상세 표시가 오히려 더 헷갈린다."""
    text = text or ""
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    head, sep, rest = cut.partition("\n")
    return rest if sep else cut


def classify(error: str) -> str:
    """오류 문자열에서 원인 종류를 판별한다. 모르면 UNKNOWN('')."""
    text = _norm(error)
    if not text:
        return UNKNOWN
    # MCP별 OAuth 실패는 Codex 본체 로그인 상태와 무관하다. 한 프로세스의 stderr에
    # 함께 섞이므로 상세 표시뿐 아니라 원인 분류에서도 해당 줄을 제외한다.
    text = "\n".join(
        line for line in text.splitlines()
        if not any(noise in line for noise in _NOISE)
    )
    for kind, needles in _RULES:
        if any(n in text for n in needles):
            return kind
    return UNKNOWN


def _fill(text: str, agent: str) -> str:
    cli = (agent or "CLI").lower()
    cli = "codex" if "codex" in cli else cli
    return text.format(cli=cli, login=_LOGIN_CMD.get(cli, f"{cli} login"))


def hint(kind: str, agent: str = "") -> str:
    """분류 결과의 짧은 원인 문구. 분류 불가면 빈 문자열."""
    pair = _HINTS.get(kind)
    return _fill(pair[0], agent) if pair else ""


def action(error: str, agent: str = "") -> str:
    """사용자가 실제로 할 일. 분류 불가면 빈 문자열."""
    pair = _HINTS.get(classify(error))
    return _fill(pair[1], agent) if pair else ""


def _messages(error: str):
    """오류 본문에서 원인일 가능성이 높은 줄만 골라낸다 (잡음 제거)."""
    picked = []
    for line in (error or "").splitlines():
        line = _LOG_PREFIX.sub("", line.strip()).strip()
        if not line or _JSON_FRAGMENT.match(line):
            continue
        if any(n in line.lower() for n in _NOISE):
            continue
        # JSON 이벤트/응답 안의 "message" 값이 가장 사람이 읽기 좋다
        found = re.findall(r'"message"\s*:\s*"([^"]{4,300})"', line)
        picked.extend(f.strip() for f in found)
        if not found:
            picked.append(line)
    # 중복 제거(같은 401이 여러 번 찍힌다) — 순서는 유지
    return list(dict.fromkeys(picked))


def describe(error: str, agent: str = "") -> str:
    """상태줄 한 줄용 요약. 원인을 알면 짧은 원인을, 모르면 첫 줄+상세 한 조각."""
    if not error:
        return "알 수 없는 오류"
    known = hint(classify(error), agent)
    if known:
        return known
    lines = _messages(error)
    if not lines:
        return error.strip().splitlines()[0][:160]
    head = lines[0]
    # "CLI 종료 코드 1"처럼 그 자체로는 정보가 없는 머리말에는 상세를 덧붙인다.
    # 파이썬 트레이스백은 마지막 줄에 진짜 예외가 오므로 뒤에서 고른다.
    if len(lines) > 1 and re.fullmatch(r"CLI 종료 코드 -?\d+", head):
        return f"{head} — {lines[-1][:120]}"
    return head[:160]


def detail_lines(error: str, limit: int = 4) -> list:
    """로그 패널에 남길 상세 줄 (잡음 제거 후 앞쪽 limit개)."""
    return _messages(error)[:limit]
