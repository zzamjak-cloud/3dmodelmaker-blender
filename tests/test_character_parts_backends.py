"""부품 보존 서버 계약과 이미지·텍스처 백엔드 전달을 검증한다."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.modules = patch.dict(sys.modules)
        self.modules.start()
        for name in ('parts_backend_test', 'parts_backend_test.core', 'parts_backend_test.texturing'):
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        self.preferences = self.fake('preferences')
        self.preferences.resolve_cli_path = Mock(return_value='codex')
        self.preferences.get_prefs = Mock(return_value=types.SimpleNamespace(use_shapegen=True))
        self.runner = self.fake('core.runner')
        self.runner.run_cli_async = Mock()
        self.imagegen = self.fake('core.imagegen')
        self.imagegen.generate = Mock()
        self.layout = self.load('texturing.layout', 'texturing/layout.py')
        self.mv = self.load('core.multiview', 'core/multiview.py')
        self.texgen = self.load('core.texgen', 'core/texgen.py')
        self.shape = self.load('core.shapegen', 'core/shapegen.py')
        self.temp = tempfile.TemporaryDirectory()
        self.directory = self.temp.name

    def tearDown(self):
        self.temp.cleanup()
        self.modules.stop()

    def fake(self, name):
        name = 'parts_backend_test.' + name
        module = types.ModuleType(name)
        sys.modules[name] = module
        parent, attr = name.rsplit('.', 1)
        setattr(sys.modules[parent], attr, module)
        return module

    def load(self, name, relative):
        fullname = 'parts_backend_test.' + name
        spec = importlib.util.spec_from_file_location(fullname, ROOT / relative)
        module = importlib.util.module_from_spec(spec)
        sys.modules[fullname] = module
        spec.loader.exec_module(module)
        parent, attr = fullname.rsplit('.', 1)
        setattr(sys.modules[parent], attr, module)
        return module

    def test_override_does_not_append_character_restoration_contract(self):
        with patch.object(self.mv, 'use_openrouter', return_value=True):
            self.mv.generate('무기', self.directory, 30, Mock(), ref_image='original.png',
                             sheet='TURNAROUND', prompt_override='무기만 남겨라')
        self.assertEqual(self.imagegen.generate.call_args.args[0], '무기만 남겨라')
        self.assertEqual(self.imagegen.generate.call_args.kwargs['refs'], ['original.png'])

    def test_cli_override_preserves_save_contract(self):
        with (patch.object(self.mv, 'use_openrouter', return_value=False),
              patch.object(self.mv.tempfile, 'mkdtemp', return_value=self.directory)):
            self.mv.generate('무기', self.directory, 30, Mock(), ref_image='original.png',
                             sheet='TURNAROUND', prompt_override='무기만 남겨라')
        prompt = self.runner.run_cli_async.call_args.kwargs['stdin_text']
        self.assertIn('무기만 남겨라', prompt)
        self.assertIn('multiview.png', prompt)
        self.assertNotIn('의상·장비·', prompt)

    def test_codex_texture_attaches_part_reference_without_face_requirement(self):
        guide = str(Path(self.directory) / 'guide.png')
        reference = str(Path(self.directory) / 'ref.png')
        Path(guide).write_bytes(b'guide')
        Path(reference).write_bytes(b'ref')
        with (patch.object(self.mv, 'use_openrouter', return_value=False),
              patch.object(self.texgen.tempfile, 'mkdtemp', return_value=self.directory)):
            self.texgen.generate('무기', guide, self.directory, 30, Mock(),
                                 reference=reference, has_face=False)
        command = self.runner.run_cli_async.call_args.args[0]
        self.assertEqual(command.count('-i'), 2)
        self.assertEqual(command[-1], '-')
        prompt = self.runner.run_cli_async.call_args.kwargs['stdin_text']
        self.assertNotIn('얼굴은 반드시', prompt)
        self.assertIn('장비 부품', prompt)

    def test_old_server_reports_actionable_upgrade(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        with patch.object(self.shape.urllib.request, 'urlopen', return_value=response):
            self.assertIn('업데이트', self.shape.parts_support_error())
        response.__enter__.return_value.read.return_value = json.dumps(
            {'ok': True, 'capabilities': {'preserve_parts': True}}).encode()
        with patch.object(self.shape.urllib.request, 'urlopen', return_value=response):
            self.assertEqual(self.shape.parts_support_error(), '')

    def test_preserve_parts_request_flag(self):
        front = Path(self.directory) / 'front.png'
        front.write_bytes(b'image')
        self.assertTrue(self.shape.build_body({'front': str(front)}, preserve_parts=True)['preserve_parts'])
        self.assertNotIn('preserve_parts', self.shape.build_body({'front': str(front)}))

    def test_nonface_view_never_requires_face(self):
        prompt = self.layout.build_view_prompt('무기', 'FRONT', has_reference=True, has_face=False)
        self.assertNotIn('얼굴은 반드시', prompt)
        self.assertIn('정사각(1:1)', prompt)

    def test_cancelled_texture_callback_does_not_retry(self):
        capture = self.fake('texturing.capture')
        capture.split_sheet = Mock(return_value={'FRONT': 'front.png'})
        active = [True]
        done = Mock()
        self.texgen._generate_per_view('무기', 'guide.png', self.directory, 30, done,
                                      is_active=lambda: active[0])
        callback = self.imagegen.generate.call_args.args[3]
        active[0] = False
        callback(None, '취소됨')
        self.assertEqual(self.imagegen.generate.call_count, 1)
        done.assert_not_called()

    def test_partial_texture_failure_is_explicit_in_strict_mode(self):
        capture = self.fake('texturing.capture')
        capture.split_sheet = Mock(return_value={'FRONT': 'front.png', 'BACK': 'back.png'})
        done = Mock()
        self.texgen._generate_per_view('무기', 'guide.png', self.directory, 30, done, strict_views=True)
        calls = self.imagegen.generate.call_args_list
        calls[0].args[3]('painted.png', None)
        calls[1].args[3](None, '실패')
        self.imagegen.generate.call_args.args[3](None, '재실패')
        self.assertIsNone(done.call_args.args[0])
        self.assertIn('BACK', done.call_args.args[1])


class ServerPostprocessTests(unittest.TestCase):
    def test_preserve_parts_skips_floater_removal_only(self):
        # GPU 모델을 로드하지 않고 실제 서버의 후처리 함수를 실행한다.
        tree = ast.parse((ROOT / 'scripts/trellis3d/server_core.py').read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_postprocess_mesh')
        floater, degenerate, reducer = Mock(), Mock(), Mock()
        degenerate.return_value.return_value = 'clean'
        namespace = {'FloaterRemover': floater, 'DegenerateFaceRemover': degenerate, 'FaceReducer': reducer}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<server-postprocess>', 'exec'), namespace)
        self.assertEqual(namespace['_postprocess_mesh']('mesh', {'preserve_parts': True}), 'clean')
        floater.assert_not_called()
        degenerate.return_value.assert_called_once_with('mesh')
        namespace['_postprocess_mesh']('mesh', {})
        floater.assert_called_once()


if __name__ == '__main__':
    unittest.main()
