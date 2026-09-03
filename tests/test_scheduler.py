# 스케줄러 정책 테스트 (Blender 없이 순수 파이썬으로 실행)
import importlib.util
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name: str, relpath: str):
    """리포 내 파일을 패키지 임포트 없이 모듈로 읽어들인다 (core/__init__.py의 bpy 회피)."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, relpath))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


scheduler = _load("scheduler", "core/scheduler.py")


class SchedulerTestBase(unittest.TestCase):
    def setUp(self):
        scheduler.reset()
        self.calls = []

    def _rec(self, tag):
        """호출 순서를 기록하는 작업 함수를 만든다."""
        return lambda: self.calls.append(tag)


class TestAiSlots(SchedulerTestBase):
    def test_runs_immediately_within_limit(self):
        # 한도 안이면 제출 즉시 실행된다 — 대기 없이 CLI가 떠야 한다
        scheduler.set_ai_limit(2)
        scheduler.submit_ai(1, self._rec("a"))
        scheduler.submit_ai(2, self._rec("b"))
        self.assertEqual(self.calls, ["a", "b"])

    def test_queues_beyond_limit(self):
        # 한도를 넘으면 대기 — 슬롯이 반환될 때까지 실행되지 않는다
        scheduler.set_ai_limit(2)
        scheduler.submit_ai(1, self._rec("a"))
        scheduler.submit_ai(2, self._rec("b"))
        scheduler.submit_ai(3, self._rec("c"))
        self.assertEqual(self.calls, ["a", "b"])
        self.assertEqual(scheduler.counts()["ai_waiting"], 1)

    def test_release_runs_next(self):
        scheduler.set_ai_limit(1)
        scheduler.submit_ai(1, self._rec("a"))
        scheduler.submit_ai(2, self._rec("b"))
        self.assertEqual(self.calls, ["a"])
        scheduler.release_ai(1)
        scheduler.pump()
        self.assertEqual(self.calls, ["a", "b"])

    def test_release_unknown_job_is_noop(self):
        # 이미 끝난 잡의 중복 반환이 슬롯 수를 음수로 만들면 안 된다
        scheduler.set_ai_limit(1)
        scheduler.release_ai(99)
        scheduler.release_ai(99)
        self.assertEqual(scheduler.counts()["ai_running"], 0)
        scheduler.submit_ai(1, self._rec("a"))
        scheduler.submit_ai(2, self._rec("b"))
        self.assertEqual(self.calls, ["a"])

    def test_one_slot_per_job(self):
        # 한 잡은 AI 슬롯을 최대 1개만 점유한다 (상태머신이 한 번에 한 요청)
        scheduler.set_ai_limit(3)
        scheduler.submit_ai(1, self._rec("a1"))
        scheduler.submit_ai(1, self._rec("a2"))
        self.assertEqual(self.calls, ["a1"])
        self.assertEqual(scheduler.counts()["ai_running"], 1)

    def test_fifo_order(self):
        # 기아 방지: 먼저 제출된 작업이 먼저 실행된다
        scheduler.set_ai_limit(1)
        scheduler.submit_ai(1, self._rec("a"))
        for key, tag in ((2, "b"), (3, "c"), (4, "d")):
            scheduler.submit_ai(key, self._rec(tag))
        for key in (1, 2, 3):
            scheduler.release_ai(key)
            scheduler.pump()
        self.assertEqual(self.calls, ["a", "b", "c", "d"])

    def test_raising_limit_starts_waiting(self):
        scheduler.set_ai_limit(1)
        scheduler.submit_ai(1, self._rec("a"))
        scheduler.submit_ai(2, self._rec("b"))
        scheduler.set_ai_limit(2)
        scheduler.pump()
        self.assertEqual(self.calls, ["a", "b"])


class TestBlenderQueue(SchedulerTestBase):
    def test_not_run_until_pump(self):
        # Blender 작업은 제출 즉시 실행하지 않는다 — 타이머 틱에서만 돈다
        scheduler.submit_blender(1, self._rec("a"))
        self.assertEqual(self.calls, [])
        scheduler.pump()
        self.assertEqual(self.calls, ["a"])

    def test_one_per_pump(self):
        # 틱당 1개만 — 작업 사이에 UI 리드로우가 들어가야 한다
        scheduler.submit_blender(1, self._rec("a"))
        scheduler.submit_blender(2, self._rec("b"))
        scheduler.pump()
        self.assertEqual(self.calls, ["a"])
        scheduler.pump()
        self.assertEqual(self.calls, ["a", "b"])

    def test_fifo_across_jobs(self):
        for key, tag in ((1, "a"), (2, "b"), (1, "c")):
            scheduler.submit_blender(key, self._rec(tag))
        for _ in range(3):
            scheduler.pump()
        self.assertEqual(self.calls, ["a", "b", "c"])

    def test_task_exception_does_not_stall_queue(self):
        # 한 작업이 터져도 큐가 멈추면 안 된다 (항목 실패해도 나머지는 계속)
        def boom():
            raise RuntimeError("의도된 실패")

        scheduler.submit_blender(1, boom)
        scheduler.submit_blender(2, self._rec("b"))
        scheduler.pump()
        scheduler.pump()
        self.assertEqual(self.calls, ["b"])

    def test_nested_submit_runs_next_pump(self):
        # 작업 중 새 작업을 제출해도 같은 틱에 연달아 실행되지 않는다
        scheduler.submit_blender(1, lambda: (self.calls.append("a"),
                                             scheduler.submit_blender(1, self._rec("b"))))
        scheduler.pump()
        self.assertEqual(self.calls, ["a"])
        scheduler.pump()
        self.assertEqual(self.calls, ["a", "b"])


class TestCancel(SchedulerTestBase):
    def test_cancel_removes_only_that_job(self):
        scheduler.set_ai_limit(1)
        scheduler.submit_ai(1, self._rec("a1"))
        scheduler.submit_ai(2, self._rec("a2"))
        scheduler.submit_blender(2, self._rec("b2"))
        scheduler.submit_blender(3, self._rec("b3"))
        removed = scheduler.cancel_job(2)
        self.assertEqual(removed, 2)
        scheduler.pump()
        self.assertEqual(self.calls, ["a1", "b3"])

    def test_cancel_releases_running_slot(self):
        scheduler.set_ai_limit(1)
        scheduler.submit_ai(1, self._rec("a"))
        scheduler.submit_ai(2, self._rec("b"))
        scheduler.cancel_job(1)
        scheduler.pump()
        self.assertEqual(self.calls, ["a", "b"])


class TestCounts(SchedulerTestBase):
    def test_counts_and_has_work(self):
        self.assertFalse(scheduler.has_work())
        scheduler.set_ai_limit(1)
        scheduler.submit_ai(1, self._rec("a"))
        scheduler.submit_ai(2, self._rec("b"))
        scheduler.submit_blender(3, self._rec("c"))
        self.assertEqual(scheduler.counts(),
                         {"ai_running": 1, "ai_waiting": 1, "blender_waiting": 1})
        self.assertTrue(scheduler.has_work())
        scheduler.reset()
        self.assertFalse(scheduler.has_work())
        self.assertEqual(scheduler.counts(),
                         {"ai_running": 0, "ai_waiting": 0, "blender_waiting": 0})


if __name__ == "__main__":
    unittest.main()
