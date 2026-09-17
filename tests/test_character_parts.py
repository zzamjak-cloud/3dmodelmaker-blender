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

    def test_each_part_uses_original_frame(self):
        for key in parts.PARTS:
            prompt = parts.part_prompt('기사', key)
            self.assertIn('원본', prompt)
            self.assertIn('축척', prompt)
        self.assertIn('가려진', parts.part_prompt('기사', 'BODY'))

    def test_texture_prompt_leaves_layout_to_guide(self):
        for part in parts.PARTS:
            prompt = parts.part_texture_prompt('기사', part)
            self.assertNotIn('3x2', prompt)
            self.assertNotIn('라벨', prompt)
            self.assertIn('가이드', prompt)

    def test_relative_weapon_height_and_position(self):
        result = parts.placement((.2, .1, .8, .9), (.75, .2, .85, .6), 1.8)
        self.assertAlmostEqual(result['height'], .9)
        self.assertAlmostEqual(result['x'], .675)
        self.assertAlmostEqual(result['z'], .225)

    def test_missing_body_is_error(self):
        with self.assertRaises(ValueError):
            parts.placement(None, (.1, .1, .2, .2), 1.8)

    def test_empty_image_is_explicitly_absent(self):
        self.assertIsNone(parts.silhouette_bbox([1.] * (8 * 8 * 4), 8, 8))

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
