"""사용자 데이터 파일 쓰기 헬퍼 — 쓰기 대상은 항상 bpy.utils.user_resource 아래(애드온 설치 폴더가 아님)."""
import json
from pathlib import Path


def write_json_atomic(path, data) -> None:
    """임시 파일에 쓴 뒤 교체한다 — 도중에 죽어도 기존 파일이 깨지지 않는다."""
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
