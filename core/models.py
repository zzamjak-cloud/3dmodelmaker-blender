"""에이전트 모델 선택, 오류 판별, UI 표시 정책을 제공하는 모듈."""


ASTRA_ID = "gpt-6-astra"
CODEX_DEFAULT_LABEL = "Codex CLI 기본 모델"
NO_MODEL_RECORD_LABEL = "모델 기록 없음"

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

_MCP_NOISE_PHRASES = ("rmcp::transport", "mcp client", "worker quit with fatal")


def model_label(agent: str, model_id: str) -> str:
    """job 상태와 로그에 사용할 안정적인 모델 표시명을 반환한다."""
    return "GPT-6 Astra" if model_id == ASTRA_ID else CODEX_DEFAULT_LABEL


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
    for line in text.splitlines():
        has_model_context = model in line or (model == ASTRA_ID and "astra" in line)
        if not has_model_context:
            continue
        if "model metadata" in line and "fallback metadata" in line:
            continue
        if any(phrase in line for phrase in _MODEL_UNAVAILABLE_PHRASES):
            return True
    return False
