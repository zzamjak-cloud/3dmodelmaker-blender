# 시스템/유저 프롬프트 조립 + lp 헬퍼 API 레퍼런스 자동 추출
import inspect
import os

_PROMPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")


def _read(filename: str) -> str:
    with open(os.path.join(_PROMPT_DIR, filename), encoding="utf-8") as f:
        return f.read()


def _api_reference() -> str:
    """lowpoly 패키지 __all__의 시그니처+독스트링으로 API 문서를 생성한다."""
    from .. import lowpoly
    lines = []
    for name in lowpoly.__all__:
        fn = getattr(lowpoly, name)
        try:
            sig = str(inspect.signature(fn))
        except (TypeError, ValueError):
            sig = "(...)"
        doc = inspect.getdoc(fn) or ""
        lines.append(f"### lp.{name}{sig}")
        if doc:
            lines.append(doc)
        lines.append("")
    return "\n".join(lines)


def build_system_prompt() -> str:
    return _read("system_lowpoly.md") + "\n\n## lp 헬퍼 API 레퍼런스\n\n" + _api_reference()


def build_initial_prompt(user_request: str) -> str:
    return (
        f"다음 로우폴리 모델을 만들어라: {user_request}\n\n"
        "시스템 지침의 출력 형식(STATUS 헤더 + python 코드 블록 1개)을 반드시 지켜라."
    )


def build_error_prompt(traceback_text: str) -> str:
    return (
        "직전 코드 실행이 실패했다. traceback:\n\n"
        f"```\n{traceback_text}\n```\n\n"
        "원인을 수정한 **전체 코드**를 다시 작성하라 (이전 생성물은 자동 삭제된다). "
        "출력 형식: 첫 줄 `STATUS: REVISE` + python 코드 블록 1개."
    )


def build_critique_prompt(image_names: list, stats: dict, iteration: int, max_iterations: int) -> str:
    template = _read("critique.md")
    stats_text = "\n".join(f"- {k}: {v}" for k, v in stats.items())
    return template.format(
        images=", ".join(image_names),
        stats=stats_text,
        iteration=iteration,
        max_iterations=max_iterations,
    )


def build_improve_prompt(original_request: str, current_code: str, feedback: str,
                         image_names: list, stats: dict) -> str:
    stats_text = "\n".join(f"- {k}: {v}" for k, v in stats.items())
    fb = f"\n\n**사용자 피드백 (최우선으로 반영하라)**: {feedback}" if feedback else ""
    return (
        f"이전에 다음 요청으로 로우폴리 모델을 만들었다: {original_request}\n\n"
        f"현재 모델의 전체 코드:\n```python\n{current_code}\n```\n\n"
        f"현재 모델을 여러 앵글로 캡처했다: {', '.join(image_names)}\n"
        f"통계:\n{stats_text}{fb}\n\n"
        "캡처 이미지를 확인하고 완성도 규칙 기준으로 한 단계 개선한 **전체 코드**를 작성하라. "
        "잘된 부분은 유지하고 문제 부분만 고쳐라 (전면 재설계 금지). "
        "출력 형식: 첫 줄 `STATUS: REVISE` + python 코드 블록 1개."
    )


def build_variation_prompt(original_request: str, final_code: str, count: int) -> str:
    return (
        f"이전에 다음 요청으로 모델을 만들었다: {original_request}\n\n"
        f"그때의 최종 코드:\n```python\n{final_code}\n```\n\n"
        f"같은 스타일을 유지하면서 치수·비율·색·디테일을 다르게 한 변형(variation) {count}개를 "
        "한 코드 안에서 X축으로 나란히 배치해 생성하라. "
        "출력 형식: 첫 줄 `STATUS: REVISE` + python 코드 블록 1개."
    )
