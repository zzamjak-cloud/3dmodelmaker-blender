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


def _ref_note(ref_image: str) -> str:
    return (
        f"\n\n사용자가 참조 이미지를 제공했다: {ref_image}\n"
        "코드를 쓰기 전에 참조 이미지를 반드시 확인하고, 형태·비율·실루엣·색 구성을 "
        "참조에 최대한 가깝게 만들어라. 로우폴리로 단순화하되 참조의 시그니처 요소와 비율은 유지하라."
    )


def _multiview_note(multiview: str) -> str:
    return (
        f"\n\n{multiview} 는 이 대상의 멀티뷰 참조 시트다 "
        "(좌상=정면, 우상=측면, 좌하=상면, 우하=3/4뷰). "
        "코드를 쓰기 전에 반드시 확인하고, 각 뷰의 실루엣·비율·색을 그대로 따라 모델링하라."
    )


def build_initial_prompt(user_request: str, ref_image: str = None, multiview: str = None) -> str:
    return (
        f"다음 로우폴리 모델을 만들어라: {user_request}\n\n"
        "시스템 지침의 출력 형식(STATUS 헤더 + python 코드 블록 1개)을 반드시 지켜라."
    ) + (_ref_note(ref_image) if ref_image else "") \
      + (_multiview_note(multiview) if multiview else "")


def build_error_prompt(traceback_text: str) -> str:
    return (
        "직전 코드 실행이 실패했다. traceback:\n\n"
        f"```\n{traceback_text}\n```\n\n"
        "원인을 수정한 **전체 코드**를 다시 작성하라 (이전 생성물은 자동 삭제된다). "
        "출력 형식: 첫 줄 `STATUS: REVISE` + python 코드 블록 1개."
    )


# 비평 턴의 종료 선언 규칙 — allow_done 여부에 따라 갈린다
_DONE_ALLOWED = (
    "고칠 점이 있으면 첫 줄 `STATUS: REVISE` + 개선한 **전체 코드**를,\n"
    "에셋 스토어 수준이면 첫 줄 `STATUS: DONE`만 반환하라.\n"
    "단, 치명 결함(뜬 파트·요청 누락·뚫린 면)이 없고 남은 개선이 사소하면 DONE을 선언하라 — 반복은 비용이다."
)
_DONE_FORBIDDEN = (
    "아직 최소 개선 턴에 도달하지 않았다 — `STATUS: DONE`을 선언하지 마라.\n"
    "남은 결함이 없어 보여도 실루엣 과장·2톤 배색·디테일 밀도에서 개선점을 찾아\n"
    "첫 줄 `STATUS: REVISE` + 개선한 **전체 코드**로 답하라."
)


def build_critique_prompt(image_names: list, stats: dict, iteration: int, max_iterations: int,
                          allow_done: bool = True, ref_image: str = None,
                          multiview: str = None) -> str:
    template = _read("critique.md")
    stats_text = "\n".join(f"- {k}: {v}" for k, v in stats.items())
    out = template.format(
        images=", ".join(image_names),
        stats=stats_text,
        iteration=iteration,
        max_iterations=max_iterations,
        done_rule=_DONE_ALLOWED if allow_done else _DONE_FORBIDDEN,
    )
    if ref_image:
        out += (
            f"\n\n{ref_image} 는 사용자가 제공한 참조 이미지다 — 캡처와 참조를 비교해 "
            "형태·비율·색이 참조에 가까워지도록 구체적으로 지적하라. 참조와 동떨어져 있으면 DONE이 아니다."
        )
    if multiview:
        out += (
            f"\n\n{multiview} 는 멀티뷰 참조 시트다 (좌상=정면, 우상=측면, 좌하=상면, 우하=3/4뷰). "
            "캡처의 각 앵글을 시트의 해당 뷰와 나란히 비교해 실루엣·비율·누락 파트를 구체적으로 지적하라. "
            "참조 시트와 실루엣이 동떨어져 있으면 DONE이 아니다."
        )
    return out


def build_improve_prompt(original_request: str, current_code: str, feedback: str,
                         image_names: list, stats: dict, ref_image: str = None) -> str:
    stats_text = "\n".join(f"- {k}: {v}" for k, v in stats.items())
    fb = f"\n\n**사용자 피드백 (최우선으로 반영하라)**: {feedback}" if feedback else ""
    if ref_image:
        fb += (f"\n\n{ref_image} 는 사용자가 제공한 참조 이미지다 — "
               "캡처와 비교해 형태·비율·색이 참조에 가까워지도록 개선하라.")
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
