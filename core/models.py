"""에이전트 모델 선택, 오류 판별, UI 표시 정책을 제공하는 모듈."""


ASTRA_ID = "gpt-6-astra"
CODEX_DEFAULT_LABEL = "Codex CLI 기본 모델"
NO_MODEL_RECORD_LABEL = "모델 기록 없음"

# 턴 역할 — 역할마다 모델·추론 강도를 따로 고른다. Astra는 토큰 소모가 빨라 모든 턴에
# 쓰면 비용이 급증하므로, 형태를 처음 잡는 생성 턴만 Astra로 돌리고 나머지는 하위 모델로 돌린다.
ROLE_GENERATE = "generate"   # 초기 생성 · 변형 · 배경 배치
ROLE_REVIEW = "review"       # 시각 검토 판정 · 6면도 대조
ROLE_FIX = "fix"             # 오류 수정 · 검토 지적 수정 · 형식 재요청
ROLE_PLAN = "plan"           # 배경 플랜
ROLES = (ROLE_GENERATE, ROLE_REVIEW, ROLE_FIX, ROLE_PLAN)
ROLE_LABELS = {ROLE_GENERATE: "생성", ROLE_REVIEW: "검토", ROLE_FIX: "수정", ROLE_PLAN: "플랜"}

# 환경설정 드롭다운 항목 — 식별자는 persist JSON에 그대로 저장되므로 바꾸지 않는다
MODEL_CHOICES = (
    ('ASTRA', "GPT-6 Astra", "가장 비싼 모델 — 형태·비율을 처음 잡는 턴에 적합"),
    ('DEFAULT', "Codex CLI 기본 모델", "~/.codex/config.toml 의 model (예: gpt-5.5) — 저렴"),
)
EFFORT_CHOICES = (
    ('low', "low", "추론 토큰 최소 — 단순 수정"),
    ('medium', "medium", "균형"),
    ('high', "high", "추론 토큰 최대 — 시트 비율 측정이 필요한 생성 턴"),
)
# 역할별 기본 라우팅 (모델 선택, 추론 강도)
DEFAULT_ROUTING = {
    ROLE_GENERATE: ('ASTRA', 'high'),
    ROLE_REVIEW: ('DEFAULT', 'medium'),
    ROLE_FIX: ('DEFAULT', 'medium'),
    ROLE_PLAN: ('DEFAULT', 'medium'),
}


def model_from_choice(choice: str) -> str:
    """환경설정 선택값을 Codex -m 인자로 바꾼다. 기본 모델은 빈 문자열(플래그 생략)."""
    return ASTRA_ID if str(choice or "").strip().upper() == 'ASTRA' else ""


def routing_from_prefs(prefs) -> dict:
    """환경설정에서 역할별 (model_id, effort) 표를 만든다. 설정이 없으면 기본 라우팅."""
    routing = {}
    for role in ROLES:
        choice, effort = DEFAULT_ROUTING[role]
        choice = getattr(prefs, f"{role}_model", choice) or choice
        effort = getattr(prefs, f"{role}_effort", effort) or effort
        routing[role] = (model_from_choice(choice), str(effort))
    return routing


def routing_summary(routing: dict) -> str:
    """로그용 한 줄 — '생성 GPT-6 Astra·high / 검토 Codex CLI 기본 모델·medium / ...'."""
    parts = []
    for role in ROLES:
        model_id, effort = routing.get(role, ("", ""))
        parts.append(f"{ROLE_LABELS[role]} {model_label('CODEX', model_id)}·{effort}")
    return " / ".join(parts)


def add_usage(total: dict, usage: dict) -> dict:
    """턴 사용량을 누계에 더한다 (키: input_tokens·cached_input_tokens·output_tokens)."""
    for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
        total[key] = int(total.get(key, 0) or 0) + int(usage.get(key, 0) or 0)
    total["turns"] = int(total.get("turns", 0) or 0) + 1
    return total


def format_usage(usage: dict) -> str:
    """'입력 12,345 (캐시 8,000) / 출력 2,345' 형태의 짧은 문구."""
    if not usage:
        return ""
    inp = int(usage.get("input_tokens", 0) or 0)
    cached = int(usage.get("cached_input_tokens", 0) or 0)
    out = int(usage.get("output_tokens", 0) or 0)
    cache = f" (캐시 {cached:,})" if cached else ""
    return f"입력 {inp:,}{cache} / 출력 {out:,}"

_TERMINAL_FAILURE_PHRASES = (
    "unauthorized",
    "invalid_token",
    "authentication_error",
    "401",
    "rate_limit",
    "rate limit",
    "quota",
    "429",
    "connection refused",
    "network is unreachable",
    "dns error",
    "timeout",
    "timed out",
    "시간 초과",
)

_MODEL_UNAVAILABLE_PHRASES = (
    "model not found",
    "model-not-found",
    "model_not_found",
    "does not exist",
    "unsupported model",
    "model unsupported",
    "is not supported",
    "do not have access",
    "doesn't have access",
    "not available to",
    "no access to model",
)

# 서버측 일시적 용량 부족 — 모델명을 언급하지 않고 "Selected model is at capacity."
# 처럼만 오므로 모델 컨텍스트 요구를 면제하고 곧바로 폴백 대상으로 본다.
_MODEL_CAPACITY_PHRASES = ("at capacity",)

_MCP_NOISE_PHRASES = ("rmcp::transport", "mcp client", "worker quit with fatal")


def model_label(agent: str, model_id: str) -> str:
    """job 상태와 로그에 사용할 안정적인 모델 표시명을 반환한다."""
    if model_id == ASTRA_ID:
        return "GPT-6 Astra"
    return model_id or CODEX_DEFAULT_LABEL


def job_model_label(requested_model: str, effective_model: str, fallback: bool) -> str:
    """job의 예정 모델 또는 실제 사용 모델을 UI 문구로 만든다."""
    if effective_model:
        suffix = " (Astra 사용 불가)" if fallback else ""
        return f"사용 모델: {effective_model}{suffix}"
    return f"예정 모델: {requested_model or '기본 모델'}"


def generation_model_label(state: str, requested_model: str,
                           effective_model: str) -> str:
    """실행 기록을 우선하고, 새 대기 항목에는 Astra를 예정 모델로 표시한다."""
    generation = effective_model or requested_model
    if generation:
        return generation
    if state == "PENDING":
        return model_label("CODEX", ASTRA_ID)
    return NO_MODEL_RECORD_LABEL


def is_model_unavailable(error: str, model_id: str) -> bool:
    """명시한 모델 자체의 계정 접근 불가 오류인지 보수적으로 판정한다."""
    text = (error or "").lower()
    model = (model_id or "").lower()
    text = "\n".join(
        line for line in text.splitlines()
        if not any(noise in line for noise in _MCP_NOISE_PHRASES)
    )
    if not model or any(phrase in text for phrase in _TERMINAL_FAILURE_PHRASES):
        return False
    # 혼잡 오류는 어느 줄에 있든 지정 모델(-m)의 문제다 — 모델명이 안 실려 온다
    if any(phrase in text for phrase in _MODEL_CAPACITY_PHRASES):
        return True
    for line in text.splitlines():
        has_model_context = model in line or (model == ASTRA_ID and "astra" in line)
        if not has_model_context:
            continue
        if "model metadata" in line and "fallback metadata" in line:
            continue
        if any(phrase in line for phrase in _MODEL_UNAVAILABLE_PHRASES):
            return True
    return False
