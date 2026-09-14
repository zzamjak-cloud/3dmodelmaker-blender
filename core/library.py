# 생성 라이브러리: 성공한 세션의 프롬프트·코드·캡처를 쌓아 다음 생성의 few-shot 예시로 재활용
#
# 축적이 목적이므로 저장은 항상 하고(세션 성공 시 자동), 사용자 평가(rating)는
# 나중에 붙을 수 있게 분리한다. few-shot 선택은 평가 높은 것 우선 → 최신 우선.
#
# 저장 위치: Blender config 폴더의 lp3d_library/ (persist.py와 같은 뿌리)
#   index.json                 — 항목 메타데이터 목록
#   entries/<id>/code.py       — 최종 생성 코드
#   entries/<id>/thumb.png     — 대표 캡처 (있을 때만)
#   entries/<id>/multiview.png — 멀티뷰 참조 시트 (있을 때만)
import json
import logging
import os
import re
import shutil
import time

log = logging.getLogger(__name__)

_INDEX = "index.json"
MAX_FEWSHOT_CHARS = 3500   # 프롬프트에 넣을 예시 코드 1개의 상한
_STOPWORDS = {"만들어줘", "만들어", "모델", "로우폴리", "하나", "개의", "좀", "그리고", "느낌", "스타일"}


def root_dir() -> str:
    import bpy
    return os.path.join(bpy.utils.user_resource('CONFIG'), "lp3d_library")


def _index_path() -> str:
    return os.path.join(root_dir(), _INDEX)


def load_index() -> list:
    try:
        with open(_index_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _write_index(entries: list):
    try:
        os.makedirs(root_dir(), exist_ok=True)
        with open(_index_path(), "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=1)
    except OSError:
        log.exception("라이브러리 인덱스 저장 실패")


def tokens(text: str) -> set:
    """유사도 비교용 토큰 — 한글/영문/숫자 덩어리만 남기고 흔한 말은 제거한다."""
    raw = re.findall(r"[0-9A-Za-z가-힣]+", (text or "").lower())
    return {t for t in raw if len(t) > 1 and t not in _STOPWORDS}


def similarity(a: str, b: str) -> float:
    """자카드 유사도 (0~1). 외부 의존 없이 가볍게 — 항목 수가 수천 개여도 충분히 빠르다."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def save_entry(request: str, code: str, stats: dict = None,
               thumbnail: str = None, multiview: str = None, agent: str = "",
               mode: str = "OBJECT") -> str:
    """성공한 생성 결과를 라이브러리에 저장하고 항목 id를 반환한다."""
    if not (request or "").strip() or not (code or "").strip():
        return ""
    entry_id = time.strftime("%Y%m%d-%H%M%S")
    entries = load_index()
    existing = {e.get("id") for e in entries}
    if entry_id in existing:  # 같은 초에 두 번 저장되는 경우
        entry_id = f"{entry_id}-{len(entries)}"
    entry_dir = os.path.join(root_dir(), "entries", entry_id)
    try:
        os.makedirs(entry_dir, exist_ok=True)
        with open(os.path.join(entry_dir, "code.py"), "w", encoding="utf-8") as f:
            f.write(code)
        for src, name in ((thumbnail, "thumb.png"), (multiview, "multiview.png")):
            if src and os.path.isfile(src):
                shutil.copy(src, os.path.join(entry_dir, name))
    except OSError:
        log.exception("라이브러리 항목 저장 실패")
        return ""
    entries.append({
        "id": entry_id,
        "request": request,
        "agent": agent,
        "tris": (stats or {}).get("tris", 0),
        "rating": 0,          # 0=미평가, 1=합격(개선 종료), 2=우수(사용자 지정)
        "mode": mode or "OBJECT",  # 제작 모드 — 오브젝트 예시가 배경 프롬프트에 섞이면 안 된다
        "created": entry_id,
    })
    _write_index(entries)
    return entry_id


def set_rating(entry_id: str, rating: int):
    entries = load_index()
    for e in entries:
        if e.get("id") == entry_id:
            e["rating"] = rating
            _write_index(entries)
            return True
    return False


def read_code(entry_id: str) -> str:
    try:
        with open(os.path.join(root_dir(), "entries", entry_id, "code.py"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def find_similar(request: str, limit: int = 3, min_score: float = 0.15,
                 mode: str = None) -> list:
    """요청과 비슷한 과거 성공 항목을 점수 순으로 반환한다.

    같은 점수면 평가 높은 것 → 최신 순. 유사도가 낮으면 아예 반환하지 않는다
    (엉뚱한 예시를 주면 오히려 생성을 망친다).

    mode를 주면 그 제작 모드의 항목만 본다 — 오브젝트 코드와 배경 배치 코드는
    규칙이 정반대라 섞어 주입하면 오히려 생성을 망친다. mode 필드가 없는
    구버전 항목은 오브젝트로 간주한다."""
    scored = []
    for e in load_index():
        if mode and (e.get("mode") or "OBJECT") != mode:
            continue
        score = similarity(request, e.get("request", ""))
        if score >= min_score:
            scored.append((score, e.get("rating", 0), e.get("id", ""), e))
    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    return [e for _, _, _, e in scored[:limit]]


def fewshot_examples(request: str, limit: int = 1, mode: str = "OBJECT") -> list:
    """few-shot 주입용 (요청, 코드) 목록. 코드가 너무 길면 제외한다."""
    out = []
    for entry in find_similar(request, limit=limit, mode=mode):
        code = read_code(entry.get("id", ""))
        if code and len(code) <= MAX_FEWSHOT_CHARS:
            out.append((entry.get("request", ""), code))
    return out


def stats() -> dict:
    entries = load_index()
    return {
        "count": len(entries),
        "rated": sum(1 for e in entries if e.get("rating", 0) > 0),
    }


def delete_entry(entry_id: str) -> bool:
    entries = load_index()
    remaining = [e for e in entries if e.get("id") != entry_id]
    if len(remaining) == len(entries):
        return False
    shutil.rmtree(os.path.join(root_dir(), "entries", entry_id), ignore_errors=True)
    _write_index(remaining)
    return True
