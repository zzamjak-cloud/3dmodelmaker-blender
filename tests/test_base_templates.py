"""내장 베이스 메시의 연결성과 변형용 루프를 검증한다."""
import importlib.util
from pathlib import Path
import unittest


class BaseTemplatesTests(unittest.TestCase):
    def test_connected_closed_quads_with_face_and_joint_groups(self):
        path = Path(__file__).resolve().parents[1] / 'lowpoly/base_templates.py'
        spec = importlib.util.spec_from_file_location('base_templates_data', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for kind in ('HUMANOID', 'QUADRUPED'):
            vertices, faces, groups = module.build_template(kind)
            self.assertGreater(len(vertices), 100)
            edges = {}
            adjacent = {i: set() for i in range(len(vertices))}
            for face in faces:
                self.assertEqual(len(face), 4)
                self.assertEqual(len(set(face)), 4)
                for a, b in zip(face, face[1:] + face[:1]):
                    self.assertTrue(0 <= a < len(vertices))
                    edges[tuple(sorted((a, b)))] = edges.get(tuple(sorted((a, b))), 0) + 1
                    adjacent[a].add(b)
                    adjacent[b].add(a)
            self.assertTrue(all(count == 2 for count in edges.values()))
            seen, pending = set(), [0]
            while pending:
                index = pending.pop()
                if index not in seen:
                    seen.add(index)
                    pending.extend(adjacent[index] - seen)
            self.assertEqual(len(seen), len(vertices))
            self.assertGreater(len(groups['face']), 40)
            self.assertTrue(any(key.startswith('joint_') for key in groups))


if __name__ == '__main__':
    unittest.main()
