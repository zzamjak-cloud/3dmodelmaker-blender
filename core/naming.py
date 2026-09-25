# 결과 이름 짓기 — 프롬프트에서 무엇을 만들었는지 알아볼 수 있는 짧은 이름 (bpy 비의존)
#
# 예전에는 영문·숫자만 남겨 한국어 프롬프트가 전부 "LP3D_Model"이 됐다. 컬렉션·.blend·결과 폴더가
# 모두 같은 이름에 _001만 붙어 캠핑장과 거실을 구분할 수 없었다.
import re

FALLBACK = "LP3D_Model"
MAX_CHARS = 20
MAX_WORDS = 3

# 치수·수량 단어 — "폭 4.8m 깊이 1.9m의 소파"에서 이름이 될 것은 "소파"다
_MEASURE = {"폭", "깊이", "높이", "지름", "밑동", "약", "길이", "너비", "두께", "반지름", "크기", "규모"}
# 요청의 지시어 — 무엇을 만드는지가 아니라 어떻게 만들지다
_FILLER = {"만들어", "만들어줘", "만들기", "생성", "생성해줘", "그려줘", "하나", "한", "개", "처럼", "같은", "느낌"}


def _usable(word: str) -> bool:
    if any(ch.isdigit() for ch in word):
        return False
    if word in _MEASURE or word in _FILLER:
        return False
    # "참조 이미지처럼" 같은 참조 지시 — 결과를 설명하지 않는다
    return not (word.startswith("참조") or word.startswith("이미지"))


def result_name(request: str) -> str:
    """요청문의 첫 구절에서 핵심 명사구(끝쪽 단어 최대 3개)를 이름으로 쓴다.

    한국어 수식어는 명사 앞에 오므로 구절 끝이 '무엇'이다 — "낮고 넓은 주황색 삼인용 소파" → "주황색_삼인용_소파".
    너무 길면 앞 단어부터 버린다. 쓸 단어가 없으면 FALLBACK."""
    text = str(request or "")
    clause = re.split(r"[,.;:\n—()\[\]]", text, maxsplit=1)[0]
    words = [w for w in re.findall(r"[0-9A-Za-z가-힣]+", clause) if _usable(w)]
    if not words:
        words = [w for w in re.findall(r"[0-9A-Za-z가-힣]+", text) if _usable(w)]
    words = words[-MAX_WORDS:]
    while len(words) > 1 and len("_".join(words)) > MAX_CHARS:
        words = words[1:]
    name = "_".join(words)[:MAX_CHARS].strip("_")
    return name or FALLBACK
