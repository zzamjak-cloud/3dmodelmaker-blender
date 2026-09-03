# 씬 프로퍼티 잔재 검사: 잡 항목으로 옮긴 필드를 아직도 씬에 쓰는 코드를 잡는다
#
# 이 필드들은 LP3DSceneProps에서 LP3DJobItem으로 옮겼다. bpy_struct는 정의되지 않은
# RNA 프로퍼티에 대입하면 AttributeError를 내는데, 실행 경로가 드문 곳(Dev Reload 등)에
# 남아 있으면 리뷰를 여러 번 통과해도 살아남는다 — 실제로 한 줄이 그렇게 살아남았다.
import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 씬(LP3DSceneProps)에서 제거되어 잡 항목(LP3DJobItem)으로 옮겨간 필드들
MOVED_TO_JOB = (
    "prompt", "status", "status_hint", "log", "phase", "iteration", "total_turns",
    "started_at", "is_running", "improve_open", "last_collection", "last_code",
    "last_prompt", "last_entry_id", "multiview_path", "ref_image_path",
    "improve_feedback",
)

# 씬 프로퍼티 그룹을 가리키는 이름에 한정한다 — 같은 이름이 잡 항목에는 살아 있어서
# job.status·item.prompt 같은 정상 코드를 잡으면 안 된다
_PATTERN = re.compile(
    r"(?:\bprops|\.lp3d)\.(?:" + "|".join(MOVED_TO_JOB) + r")\s*=(?!=)")

# 정의 자체가 있는 곳과 계획·문서·배포본은 검사하지 않는다
_SKIP_DIRS = {"tests", "docs", "dist", ".superpowers", ".git", "__pycache__"}
_SKIP_FILES = {"properties.py"}


def _sources():
    for dirpath, dirnames, filenames in os.walk(_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py") and name not in _SKIP_FILES:
                yield os.path.join(dirpath, name)


class TestNoStaleSceneProps(unittest.TestCase):
    def test_no_write_to_moved_scene_property(self):
        hits = []
        for path in _sources():
            with open(path, encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    if _PATTERN.search(line):
                        rel = os.path.relpath(path, _ROOT)
                        hits.append(f"{rel}:{lineno}: {line.strip()}")
        self.assertEqual(hits, [], "씬에서 제거된 프로퍼티에 대입하고 있다 "
                                   "(잡 항목의 필드다 — active_job()/job_by_uid()로 접근하라):\n"
                                   + "\n".join(hits))

    def test_pattern_catches_the_known_regression(self):
        # 이 가드가 실제로 뭔가를 잡는지 확인 — 안 그러면 조용히 무력해진다
        self.assertTrue(_PATTERN.search("                scene.lp3d.status = message"))
        self.assertTrue(_PATTERN.search("props.prompt = ''"))

    def test_pattern_allows_job_item_fields(self):
        # 같은 이름이라도 잡 항목에 쓰는 건 정상이다
        for line in ("job.status = '대기 중'", "item.prompt = text",
                     "new_job.status = f'실패: {error}'", "self.log = ''",
                     "if props.status == 'x':"):
            self.assertIsNone(_PATTERN.search(line), line)


if __name__ == "__main__":
    unittest.main()
