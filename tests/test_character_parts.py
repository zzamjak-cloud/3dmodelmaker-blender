"""캐릭터 부품 프롬프트와 정렬·진행 상태를 검증한다."""
import importlib.util
from pathlib import Path
import unittest
import sys
import types
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('character_parts_tested', Path(__file__).resolve().parents[1] / 'core/character_parts.py')
parts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parts)


class CharacterPartsTests(unittest.TestCase):
    def test_retopo_fallback_is_visible_in_method_log(self):
        note = parts.retopo_method_note({'method': 'decimate', 'fallback_reason': 'QuadriFlow 실패'})
        self.assertIn('decimate', note)
        self.assertIn('QuadriFlow 실패', note)
        self.assertIn('대체 처리', note)
        self.assertEqual(parts.retopo_method_note({'method': 'template'}), 'template')

    def test_each_part_is_an_independent_object_sheet(self):
        for key in parts.PARTS:
            prompt = parts.part_prompt('기사', key)
            self.assertIn('3x2', prompt); self.assertIn('80~90%', prompt); self.assertIn('참조', prompt)
            self.assertNotIn('원본 턴어라운드와 동일한', prompt)   # 공통 카메라·축척 강제(결합)는 제거됐다
        self.assertIn('마네킹', parts.part_prompt('기사', 'BODY'))
        self.assertIn('나란히', parts.part_prompt('기사', 'WEAPON'))

    def test_texture_prompt_leaves_layout_to_guide(self):
        for part in parts.PARTS:
            prompt = parts.part_texture_prompt('기사', part)
            self.assertNotIn('3x2', prompt)
            self.assertNotIn('라벨', prompt)
            self.assertIn('가이드', prompt)

    def test_row_layout_places_body_at_origin_and_others_right(self):
        xs = parts.row_layout([1.6, 1.0, 0.4], gap=0.3)
        self.assertEqual(xs[0], 0.0)
        self.assertAlmostEqual(xs[1], 0.8 + 0.3 + 0.5)
        self.assertAlmostEqual(xs[2], 0.8 + 0.3 + 1.0 + 0.3 + 0.2)

    def test_empty_cell_has_no_content_but_label_is_ignored(self):
        w = h = 20
        blank = [1.0] * (w * h * 4)
        self.assertFalse(parts.has_content(blank, w, h))
        labeled = list(blank)
        for y in range(18, 20):            # 상단 라벨 행(좌하단 원점이라 마지막 행들)
            for x in range(5, 15):
                labeled[(y * w + x) * 4:(y * w + x) * 4 + 3] = [0.0, 0.0, 0.0]
        self.assertFalse(parts.has_content(labeled, w, h))
        drawn = list(blank)
        for y in range(5, 12):
            for x in range(6, 14):
                drawn[(y * w + x) * 4:(y * w + x) * 4 + 3] = [0.3, 0.2, 0.1]
        self.assertTrue(parts.has_content(drawn, w, h))

    def test_sequence_stops_after_failure_or_cancel(self):
        sequence = parts.PartSequence()
        self.assertEqual(sequence.current, 'BODY')
        sequence.advance()
        self.assertEqual(sequence.current, 'OUTFIT')
        sequence.stop()
        self.assertIsNone(sequence.current)
        self.assertFalse(sequence.advance())


class CallbackTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = types.ModuleType('parts_test_runtime.scheduler')
        self.scheduler.release_ai = Mock()
        package = types.ModuleType('parts_test_runtime')
        package.scheduler = self.scheduler
        self.modules = patch.dict(sys.modules, {
            'parts_test_runtime': package,
            'parts_test_runtime.scheduler': self.scheduler,
        })
        self.modules.start()
        self.package = patch.object(parts, '__package__', 'parts_test_runtime')
        self.package.start()
        self.session = parts.CharacterPartsMixin()
        self.session.uid = 'test-uid'
        self.session._parts_sequence = parts.PartSequence()
        self.session._stale = lambda: False
        self.session._finish = Mock()
        self.session._submit_blender = Mock()
        self.session._prepare_part_shape = Mock()
        self.session._retopo_part = Mock()

    def tearDown(self):
        self.package.stop()
        self.modules.stop()

    def test_sheet_failure_releases_and_stops(self):
        self.session._on_part_sheet('BODY', None, '이미지 생성 실패')
        self.scheduler.release_ai.assert_called_once_with('test-uid')
        self.session._finish.assert_called_once()
        self.assertFalse(self.session._finish.call_args.kwargs['ok'])
        self.assertIsNone(self.session._parts_sequence.current)
        self.session._submit_blender.assert_not_called()

    def test_shape_failure_is_not_combined_fallback(self):
        self.session._on_part_shape('BODY', None, '서버 연결 실패')
        self.scheduler.release_ai.assert_called_once_with('test-uid')
        self.session._finish.assert_called_once()
        self.assertIsNone(self.session._parts_sequence.current)

    def test_callback_does_not_advance_before_retopo(self):
        self.session._on_part_shape('BODY', 'body.glb', None)
        self.assertEqual(self.session._parts_sequence.current, 'BODY')
        self.session._retopo_part.assert_not_called()
        self.session._submit_blender.call_args.args[0]()
        self.session._retopo_part.assert_called_once_with('BODY', 'body.glb')

    def test_cancelled_or_replaced_callback_cannot_release_new_slot(self):
        for stale, cancelled in ((True, False), (False, True)):
            with self.subTest(stale=stale, cancelled=cancelled):
                self.session._stale = lambda: stale
                self.session._cancel_requested = cancelled
                self.session._on_part_sheet('BODY', 'body.png', None)
                self.session._on_part_shape('BODY', 'body.glb', None)
        self.scheduler.release_ai.assert_not_called()
        self.session._submit_blender.assert_not_called()

    def test_late_previous_part_callback_is_ignored(self):
        self.session._parts_sequence.advance()
        self.session._on_part_shape('BODY', 'late.glb', None)
        self.scheduler.release_ai.assert_not_called()
        self.session._submit_blender.assert_not_called()


if __name__ == '__main__':
    unittest.main()
