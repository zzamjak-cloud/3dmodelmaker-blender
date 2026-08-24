# Asset Browser 등록: asset_mark + 프리뷰 + 카탈로그(blender_assets.cats.txt)
import os
import uuid

import bpy

from ..preferences import get_prefs

_CATS_FILE = "blender_assets.cats.txt"
_CATS_HEADER = (
    "# This is an Asset Catalog Definition file for Blender.\n"
    "#\n"
    "# Empty lines and lines starting with `#` will be ignored.\n"
    "# The first non-ignored line should be the version indicator.\n"
    '# Other lines are of the format "UUID:catalog/path/for/assets:simple catalog name"\n'
    "\nVERSION 1\n\n"
)

# 프롬프트 키워드 → 카탈로그 분류 (첫 매칭 우선)
_CATEGORY_KEYWORDS = [
    ("Buildings", ("건물", "집", "오두막", "탑", "성", "house", "building", "hut", "tower", "castle")),
    ("Nature", ("나무", "바위", "돌", "풀", "꽃", "수풀", "tree", "rock", "stone", "grass", "flower", "bush", "pine")),
    ("Props", ()),  # 기본값
]


def _pick_category(prompt: str) -> str:
    lowered = (prompt or "").lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(k in lowered for k in keywords):
            return category
    return "Props"


def _catalog_root() -> str:
    """카탈로그 파일 위치: 설정된 에셋 라이브러리 > 현재 blend 파일 폴더."""
    prefs = get_prefs()
    if prefs.asset_library_path:
        return bpy.path.abspath(prefs.asset_library_path)
    if bpy.data.filepath:
        return os.path.dirname(bpy.data.filepath)
    raise RuntimeError("blend 파일을 저장하거나 환경설정에서 에셋 라이브러리 경로를 지정하세요")


def _ensure_catalog(category: str) -> str:
    """cats.txt에 카탈로그가 없으면 추가하고 UUID를 반환."""
    root = _catalog_root()
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, _CATS_FILE)
    lines = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    catalog_path = f"LowPoly/{category}"
    for line in lines:
        if line.startswith("#") or ":" not in line:
            continue
        cat_uuid, cat_path, _ = line.split(":", 2)
        if cat_path == catalog_path:
            return cat_uuid
    new_uuid = str(uuid.uuid4())
    if not lines:
        content = _CATS_HEADER
    else:
        content = "\n".join(lines) + "\n"
    content += f"{new_uuid}:{catalog_path}:{category}\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return new_uuid


def register_asset(context, coll, prompt: str) -> str:
    """컬렉션을 에셋으로 마크하고 프리뷰·카탈로그·태그를 설정. 카탈로그 경로를 반환."""
    category = _pick_category(prompt)
    catalog_uuid = _ensure_catalog(category)

    coll.asset_mark()
    coll.asset_data.catalog_id = catalog_uuid
    coll.asset_data.description = prompt or coll.name
    # 프롬프트 키워드를 태그로 (2글자 이상 단어)
    existing = {t.name for t in coll.asset_data.tags}
    for word in (prompt or "").split():
        if len(word) >= 2 and word not in existing:
            coll.asset_data.tags.new(word)
    # 프리뷰 생성
    with context.temp_override(id=coll):
        bpy.ops.ed.lib_id_generate_preview()
    return f"LowPoly/{category}"
