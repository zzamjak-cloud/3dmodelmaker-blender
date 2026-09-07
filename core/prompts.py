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


def _fewshot_note(examples: list) -> str:
    """과거 합격 결과를 스타일 참고용 예시로 붙인다 (그대로 베끼지 않도록 명시)."""
    parts = ["\n\n## 참고: 이 프로젝트에서 이미 합격한 유사 모델의 코드"]
    for request, code in examples:
        parts.append(f"\n요청: {request}\n```python\n{code}\n```")
    parts.append(
        "\n위 코드는 스타일·구조(질량 위계, 헬퍼 사용법, 배색 방식) 참고용이다. "
        "그대로 베끼지 말고 이번 요청에 맞는 형태를 새로 설계하되, 검증된 패턴은 적극 활용하라."
    )
    return "\n".join(parts)


def build_initial_prompt(user_request: str, ref_image: str = None, multiview: str = None,
                         fewshot: list = None) -> str:
    return (
        f"다음 로우폴리 모델을 만들어라: {user_request}\n\n"
        "시스템 지침의 출력 형식(STATUS 헤더 + python 코드 블록 1개)을 반드시 지켜라."
    ) + (_ref_note(ref_image) if ref_image else "") \
      + (_multiview_note(multiview) if multiview else "") \
      + (_fewshot_note(fewshot) if fewshot else "")


def build_error_prompt(traceback_text: str) -> str:
    return (
        "직전 코드 실행이 실패했다. traceback:\n\n"
        f"```\n{traceback_text}\n```\n\n"
        "원인을 수정한 **전체 코드**를 다시 작성하라 (이전 생성물은 자동 삭제된다). "
        "출력 형식: 첫 줄 `STATUS: DONE` + python 코드 블록 1개."
    )


def build_variation_prompt(original_request: str, final_code: str, count: int) -> str:
    return (
        f"이전에 다음 요청으로 모델을 만들었다: {original_request}\n\n"
        f"그때의 최종 코드:\n```python\n{final_code}\n```\n\n"
        f"같은 스타일을 유지하면서 치수·비율·색·디테일을 다르게 한 변형(variation) {count}개를 "
        "한 코드 안에서 X축으로 나란히 배치해 생성하라. "
        "출력 형식: 첫 줄 `STATUS: DONE` + python 코드 블록 1개."
    )
