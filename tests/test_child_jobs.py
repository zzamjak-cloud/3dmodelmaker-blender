# 배경 잡과 에셋 자식 잡의 큐 관리 테스트 (Blender 없이 순수 파이썬으로 실행)
#
# 자식 잡은 부모 배경 세션이 직접 구동한다 — 큐 전체 실행에 섞여 들어가거나
# 부모가 사라진 뒤 좀비로 남으면 실행 버튼이 영영 잠긴다.
import importlib.util
import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG = "child_jobs_addon"


def _install_modules():
    """jobs.py가 상대 import를 풀 수 있도록 최소 패키지 골격을 세운다."""
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [_ROOT]
    core = types.ModuleType(f"{_PKG}.core")
    core.__path__ = [os.path.join(_ROOT, "core")]
    pkg.core = core
    sys.modules[_PKG] = pkg
    sys.modules[f"{_PKG}.core"] = core

    # session은 무거우므로 대역으로 대체한다 — 여기서 검증할 것은 큐 조작이다
    session = types.ModuleType(f"{_PKG}.core.session")
    session.active_uids = set()
    session.cancelled = []
    session.is_active = lambda uid=None: uid in session.active_uids
    session.cancel_session = lambda uid: session.cancelled.append(uid)
    sys.modules[session.__name__] = session
    core.session = session

    bpy = types.ModuleType("bpy")
    bpy.data = SimpleNamespace(collections={})
    spec = importlib.util.spec_from_file_location(
        f"{_PKG}.core.jobs", os.path.join(_ROOT, "core", "jobs.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with patch.dict(sys.modules, {"bpy": bpy}):
        spec.loader.exec_module(module)
    core.jobs = module
    return module, session


jobs, SESSION = _install_modules()


class _Job:
    """LP3DJobItem의 기본값만 흉내 낸 대역."""

    def __init__(self):
        self.uid = 0
        self.prompt = ""
        self.ref_image_path = ""
        self.creation_mode = 'OBJECT'
        self.scene_size = 'M'
        self.parent_uid = ""
        self.modeling_type = 'PALETTE'
        self.style = 'LOWPOLY'
        self.character_type = 'AUTO'
        self.state = 'PENDING'
        self.status = "대기 중"
        self.status_hint = ""
        self.phase = ""
        self.log = ""
        self.iteration = 0
        self.lane = 0
        self.requested_model = ""
        self.effective_model = ""
        self.model_fallback = False


class _JobCollection(list):
    """bpy CollectionProperty의 add/remove(index)/move 의미를 흉내 낸다."""

    def add(self):
        item = _Job()
        self.append(item)
        return item

    def remove(self, index):
        del self[index]

    def move(self, src, dst):
        self.insert(dst, self.pop(src))


def _props():
    return SimpleNamespace(jobs=_JobCollection(), job_index=0, next_uid=1)


def _context(props):
    return SimpleNamespace(scene=SimpleNamespace(lp3d=props))


def _prompts(props):
    return [job.prompt for job in props.jobs]


class TestCharacterDuplicate(unittest.TestCase):
    def test_duplicate_preserves_character_template_and_parts(self):
        """복제/변형 작업에서 사용자 템플릿과 분리 선택을 잃지 않는다."""
        props = _props()
        source = jobs.add_job(props, "늑대 전사")
        source.creation_mode = 'CHARACTER'
        source.character_template = 'user_wolf'
        result = jobs.duplicate_job(props, 0)
        self.assertEqual(getattr(result, 'character_template', None), 'user_wolf')


class ChildJobTestCase(unittest.TestCase):
    def setUp(self):
        SESSION.active_uids = set()
        SESSION.cancelled = []


class TestAddChildJob(ChildJobTestCase):
    def test_child_is_inserted_right_after_parent(self):
        props = _props()
        parent = jobs.add_job(props, "포로 수용소")
        jobs.add_job(props, "다른 잡")
        jobs.add_child_job(props, parent.uid, "감시탑")
        self.assertEqual(_prompts(props), ["포로 수용소", "감시탑", "다른 잡"])

    def test_siblings_keep_plan_order(self):
        props = _props()
        parent = jobs.add_job(props, "포로 수용소")
        jobs.add_job(props, "다른 잡")
        for name in ("감시탑", "철조망", "막사"):
            jobs.add_child_job(props, parent.uid, name)
        self.assertEqual(_prompts(props),
                         ["포로 수용소", "감시탑", "철조망", "막사", "다른 잡"])

    def test_child_defaults_are_forced(self):
        props = _props()
        parent = jobs.add_job(props, "포로 수용소")
        parent.creation_mode = 'SCENE'
        child = jobs.add_child_job(props, parent.uid, "감시탑")
        self.assertEqual(child.parent_uid, str(parent.uid))
        self.assertEqual(child.creation_mode, 'OBJECT')
        self.assertEqual(child.modeling_type, 'PALETTE')
        self.assertEqual(child.lane, 0)  # 에셋은 부모 씬 안에서 조립된다

    def test_child_uid_is_unique(self):
        props = _props()
        parent = jobs.add_job(props, "포로 수용소")
        a = jobs.add_child_job(props, parent.uid, "감시탑")
        b = jobs.add_child_job(props, parent.uid, "철조망")
        self.assertEqual(len({parent.uid, a.uid, b.uid}), 3)

    def test_selection_follows_the_same_item(self):
        props = _props()
        parent = jobs.add_job(props, "포로 수용소")
        other = jobs.add_job(props, "다른 잡")
        props.job_index = 1  # '다른 잡' 선택 상태
        jobs.add_child_job(props, parent.uid, "감시탑")
        self.assertIs(props.jobs[props.job_index], other)


class TestChildLookup(ChildJobTestCase):
    def test_children_of_returns_only_own_children(self):
        props = _props()
        a = jobs.add_job(props, "수용소")
        b = jobs.add_job(props, "고대 성")
        jobs.add_child_job(props, a.uid, "감시탑")
        jobs.add_child_job(props, b.uid, "성벽")
        self.assertEqual([j.prompt for j in jobs.children_of(props, a.uid)], ["감시탑"])
        self.assertEqual([j.prompt for j in jobs.children_of(props, b.uid)], ["성벽"])

    def test_empty_parent_uid_has_no_children(self):
        props = _props()
        jobs.add_job(props, "수용소")
        self.assertEqual(jobs.children_of(props, ""), [])


class TestRemoveChildren(ChildJobTestCase):
    def test_remove_children_keeps_parent(self):
        props = _props()
        parent = jobs.add_job(props, "수용소")
        jobs.add_child_job(props, parent.uid, "감시탑")
        jobs.add_child_job(props, parent.uid, "철조망")
        self.assertEqual(jobs.remove_children(props, parent.uid), 2)
        self.assertEqual(_prompts(props), ["수용소"])

    def test_running_child_session_is_cancelled(self):
        props = _props()
        parent = jobs.add_job(props, "수용소")
        child = jobs.add_child_job(props, parent.uid, "감시탑")
        SESSION.active_uids = {child.uid}
        jobs.remove_children(props, parent.uid)
        self.assertEqual(SESSION.cancelled, [child.uid])

    def test_remove_job_takes_children_with_it(self):
        props = _props()
        parent = jobs.add_job(props, "수용소")
        jobs.add_child_job(props, parent.uid, "감시탑")
        keep = jobs.add_job(props, "남길 잡")
        jobs.remove_job(_context(props), 0)
        self.assertEqual(_prompts(props), [keep.prompt])


class TestStartAll(ChildJobTestCase):
    def test_children_are_not_started_by_queue_run(self):
        props = _props()
        parent = jobs.add_job(props, "수용소")
        jobs.add_child_job(props, parent.uid, "감시탑")
        started = []
        with patch.object(jobs, "start_one",
                          side_effect=lambda ctx, job: started.append(job.prompt) or ""):
            count, error = jobs.start_all(_context(props))
        self.assertEqual(started, ["수용소"])
        self.assertEqual((count, error), (1, ""))

    def test_top_level_jobs_all_start(self):
        props = _props()
        jobs.add_job(props, "배럴")
        jobs.add_job(props, "상자")
        with patch.object(jobs, "start_one", return_value=""):
            count, _ = jobs.start_all(_context(props))
        self.assertEqual(count, 2)


class TestRetryAndStale(ChildJobTestCase):
    def test_retry_parent_drops_old_children(self):
        props = _props()
        parent = jobs.add_job(props, "수용소")
        parent.state = 'FAILED'
        jobs.add_child_job(props, parent.uid, "감시탑")
        with patch.object(jobs, "start_one", return_value=""):
            self.assertEqual(jobs.retry_job(_context(props), 0), "")
        self.assertEqual(_prompts(props), ["수용소"])
        self.assertEqual(props.jobs[0].state, 'PENDING')

    def test_reset_stale_fails_orphaned_children(self):
        props = _props()
        parent = jobs.add_job(props, "수용소")
        parent.state = 'RUNNING'
        child = jobs.add_child_job(props, parent.uid, "감시탑")
        child.state = 'RUNNING'
        pending = jobs.add_child_job(props, parent.uid, "철조망")
        self.assertEqual(jobs.reset_stale(props), 3)
        self.assertEqual(parent.state, 'PENDING')
        self.assertEqual(child.state, 'FAILED')
        self.assertEqual(pending.state, 'FAILED')

    def test_reset_stale_leaves_live_parents_children_alone(self):
        props = _props()
        parent = jobs.add_job(props, "수용소")
        parent.state = 'RUNNING'
        child = jobs.add_child_job(props, parent.uid, "감시탑")
        child.state = 'RUNNING'
        SESSION.active_uids = {parent.uid, child.uid}
        self.assertEqual(jobs.reset_stale(props), 0)
        self.assertEqual((parent.state, child.state), ('RUNNING', 'RUNNING'))


class TestDuplicate(ChildJobTestCase):
    def test_scene_fields_survive_duplication(self):
        props = _props()
        src = jobs.add_job(props, "수용소")
        src.creation_mode = 'SCENE'
        src.scene_size = 'L'
        copy = jobs.duplicate_job(props, 0)
        self.assertEqual(copy.creation_mode, 'SCENE')
        self.assertEqual(copy.scene_size, 'L')

    def test_duplicate_of_child_becomes_top_level(self):
        props = _props()
        parent = jobs.add_job(props, "수용소")
        jobs.add_child_job(props, parent.uid, "감시탑")
        copy = jobs.duplicate_job(props, 1)
        self.assertEqual(copy.parent_uid, "")
        self.assertEqual(jobs.children_of(props, parent.uid)[0].prompt, "감시탑")
        self.assertEqual(len(jobs.children_of(props, parent.uid)), 1)


class TestChildJobGuards(ChildJobTestCase):
    """에셋 잡은 부모 배경 세션만이 구동한다 — 단독 조작을 막아야 한다."""

    def test_child_cannot_be_retried_alone(self):
        props = _props()
        parent = jobs.add_job(props, "수용소")
        jobs.add_child_job(props, parent.uid, "감시탑")
        props.jobs[1].state = 'FAILED'
        with patch.object(jobs, "start_one", return_value="") as start:
            error = jobs.retry_job(_context(props), 1)
        self.assertTrue(error)
        start.assert_not_called()
        self.assertEqual(props.jobs[1].state, 'FAILED')  # 대기로 되돌리지도 않는다

    def test_parent_retry_still_works(self):
        props = _props()
        parent = jobs.add_job(props, "수용소")
        parent.state = 'FAILED'
        jobs.add_child_job(props, parent.uid, "감시탑")
        with patch.object(jobs, "start_one", return_value=""):
            self.assertEqual(jobs.retry_job(_context(props), 0), "")

    def test_child_cannot_move_above_parent(self):
        props = _props()
        parent = jobs.add_job(props, "수용소")
        jobs.add_child_job(props, parent.uid, "감시탑")
        self.assertEqual(jobs.move_job(props, 1, -1), 1)
        self.assertEqual(_prompts(props), ["수용소", "감시탑"])

    def test_top_level_move_unaffected(self):
        props = _props()
        jobs.add_job(props, "배럴")
        jobs.add_job(props, "상자")
        self.assertEqual(jobs.move_job(props, 1, -1), 0)
        self.assertEqual(_prompts(props), ["상자", "배럴"])


class _Object:
    """레인 오프셋 검사에 필요한 bpy 오브젝트 동작만 흉내 낸다."""

    def __init__(self):
        self.location = SimpleNamespace(y=0.0)
        self.parent = None
        self._custom = {}

    def get(self, key, default=None):
        return self._custom.get(key, default)

    def __setitem__(self, key, value):
        self._custom[key] = value


class TestLaneSpacingPassthrough(ChildJobTestCase):
    """배경 씬은 결과가 수십 미터라 기본 4m 간격으로는 옆 레인과 겹친다."""

    def test_spacing_argument_reaches_lanes(self):
        obj = _Object()
        coll = SimpleNamespace(objects=[obj], all_objects=[obj])
        with patch.dict(jobs.bpy.data.collections, {"LP3D_Scene": coll}, clear=True):
            jobs.apply_lane_offset("LP3D_Scene", 2, spacing=20.0)
        self.assertEqual(obj.location.y, 40.0)

    def test_default_spacing_unchanged(self):
        obj = _Object()
        coll = SimpleNamespace(objects=[obj], all_objects=[obj])
        with patch.dict(jobs.bpy.data.collections, {"LP3D_Scene": coll}, clear=True):
            jobs.apply_lane_offset("LP3D_Scene", 2)
        self.assertEqual(obj.location.y, jobs.lanes.lane_dy(2))

    def test_reapplying_same_lane_does_not_accumulate(self):
        obj = _Object()
        coll = SimpleNamespace(objects=[obj], all_objects=[obj])
        with patch.dict(jobs.bpy.data.collections, {"LP3D_Scene": coll}, clear=True):
            jobs.apply_lane_offset("LP3D_Scene", 2, spacing=20.0)
            jobs.apply_lane_offset("LP3D_Scene", 2, spacing=20.0)
        self.assertEqual(obj.location.y, 40.0)


if __name__ == "__main__":
    unittest.main()
