# 팔레트 아티팩트 검증 테스트 (Blender 없이 순수 파이썬으로 실행)
import importlib.util
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _decode_png(path: str):
    """8비트 트루컬러 PNG를 (r, g, b) 튜플 리스트로 디코딩한다 (좌상단부터 행 우선).

    Pillow 없이 테스트를 돌리기 위한 최소 구현."""
    import struct as _struct
    import zlib as _zlib

    with open(path, "rb") as handle:
        blob = handle.read()
    offset, idat = 8, b""
    width = height = 0
    while offset < len(blob):
        length = _struct.unpack(">I", blob[offset:offset + 4])[0]
        tag = blob[offset + 4:offset + 8]
        data = blob[offset + 8:offset + 8 + length]
        if tag == b"IHDR":
            width, height, depth, color_type = _struct.unpack(">IIBB", data[:10])
            assert depth == 8 and color_type == 2, "8비트 트루컬러 PNG만 지원한다"
        elif tag == b"IDAT":
            idat += data
        offset += 12 + length
    raw = _zlib.decompress(idat)
    stride, bpp = width * 3, 3
    out, previous, pos = [], bytearray(stride), 0
    for _ in range(height):
        filter_type = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        for i in range(stride):
            left = line[i - bpp] if i >= bpp else 0
            up = previous[i]
            upleft = previous[i - bpp] if i >= bpp else 0
            if filter_type == 1:
                line[i] = (line[i] + left) & 255
            elif filter_type == 2:
                line[i] = (line[i] + up) & 255
            elif filter_type == 3:
                line[i] = (line[i] + (left + up) // 2) & 255
            elif filter_type == 4:
                estimate = left + up - upleft
                da, db, dc = (abs(estimate - left), abs(estimate - up), abs(estimate - upleft))
                if da <= db and da <= dc:
                    line[i] = (line[i] + left) & 255
                elif db <= dc:
                    line[i] = (line[i] + up) & 255
                else:
                    line[i] = (line[i] + upleft) & 255
        out.extend(tuple(line[i:i + 3]) for i in range(0, stride, bpp))
        previous = line
    return out


def _load(name: str, relpath: str):
    """리포 내 파일을 패키지 임포트 없이 모듈로 읽어들인다.

    `lowpoly/__init__.py`가 bpy를 임포트하므로 패키지 경로로는 불러올 수 없다."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(_ROOT, relpath))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gen = _load("gen_palette", "scripts/gen_palette.py")


class TestPaletteArtifacts(unittest.TestCase):
    # 셀 구성이 스펙과 일치하는지
    def test_cell_count_and_uniqueness(self):
        cells = gen.build_cells()
        self.assertEqual(len(cells), 1024)
        # 게멋 클램핑 대신 비율 채도를 쓰므로 1022색 이상이 고유해야 한다
        self.assertGreaterEqual(len(set(cells)), 1022)

    # 공식이 고정이므로 특정 셀의 색이 못 박혀 있어야 한다
    def test_reference_cells(self):
        cells = gen.build_cells()
        self.assertEqual(cells[0], (1, 1, 1))
        self.assertEqual(cells[31], (252, 252, 252))
        self.assertEqual(cells[32], (1, 1, 0))
        self.assertEqual(cells[63], (255, 251, 247))
        self.assertEqual(cells[64], (17, 11, 13))
        self.assertEqual(cells[1023], (254, 240, 250))

    # 같은 입력에서 항상 같은 바이트가 나와야 한다
    def test_png_is_reproducible(self):
        first = gen.render_png(gen.build_cells())
        second = gen.render_png(gen.build_cells())
        self.assertEqual(first, second)
        self.assertTrue(first.startswith(b"\x89PNG\r\n\x1a\n"))

    # 커밋된 PNG가 현재 스크립트 출력과 일치해야 한다 (아티팩트 최신성)
    def test_committed_png_matches_script(self):
        path = os.path.join(_ROOT, "lowpoly", "LP3D_Palette.png")
        with open(path, "rb") as handle:
            committed = handle.read()
        self.assertEqual(
            committed, gen.render_png(gen.build_cells()),
            "lowpoly/LP3D_Palette.png가 낡았습니다. python scripts/gen_palette.py 를 실행하세요.")

    # palette_data.py가 현재 스크립트 출력과 일치해야 한다
    def test_committed_data_module_matches_script(self):
        path = os.path.join(_ROOT, "lowpoly", "palette_data.py")
        with open(path, encoding="utf-8") as handle:
            committed = handle.read()
        self.assertEqual(
            committed.replace("\r\n", "\n"), gen.render_data_module(gen.build_cells()),
            "lowpoly/palette_data.py가 낡았습니다. python scripts/gen_palette.py 를 실행하세요.")

    # 두 아티팩트가 서로 어긋나지 않았는지 (PNG 셀 중앙 픽셀 대조)
    def test_data_module_matches_png_pixels(self):
        data = _load("palette_data", "lowpoly/palette_data.py")
        pixels = _decode_png(os.path.join(_ROOT, "lowpoly", "LP3D_Palette.png"))
        self.assertEqual(len(data.CELLS), data.GRID * data.GRID)
        for cell, expected in enumerate(data.CELLS):
            col, row = cell % data.GRID, cell // data.GRID
            x = col * data.CELL_PX + data.CELL_PX // 2
            # 셀 0이 좌하단이므로 PNG 행 좌표로 뒤집는다
            y = (data.GRID - 1 - row) * data.CELL_PX + data.CELL_PX // 2
            self.assertEqual(pixels[y * data.SIZE + x], expected, "셀 %d 불일치" % cell)


if __name__ == "__main__":
    unittest.main()
