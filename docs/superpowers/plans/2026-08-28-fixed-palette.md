# 고정 팔레트 텍스처 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 생성할 때마다 달라지던 팔레트 텍스처를 공식으로 결정되는 고정 256x256 PNG로 바꾸고, 임의 RGB 요청을 Oklab 최근접 스와치로 스냅한다.

**Architecture:** 순수 파이썬 생성 스크립트가 PNG와 색 데이터 모듈을 함께 뽑는다. 색 수학과 스냅 로직은 `bpy` 없는 별도 모듈에 두어 Blender 없이 테스트한다. `lowpoly/palette.py`는 팔레트를 읽기만 하므로 스냅샷/복원/그리드 이전 같은 가변 상태 기계가 전부 사라진다.

**Tech Stack:** Python 3.11+ (표준 라이브러리만 — `zlib`, `struct`, `math`, `json`, `unittest`), Blender 4.2 Python API (`bpy`)

**Spec:** `docs/superpowers/specs/2026-08-28-fixed-palette-design.md`

## Global Constraints

- **주석·독스트링은 한국어로 작성한다.** 변수명/함수명은 영어. (`CLAUDE.md` 언어 규칙)
- **외부 의존성 금지.** 생성 스크립트와 테스트는 파이썬 표준 라이브러리만 쓴다. 이 리포에는 pytest가 설치되어 있지 않으므로 테스트는 `unittest`로 작성하고 `python -m unittest`로 돌린다.
- **Blender 확장 검증기 제약.** `exec`, `eval`, `threading`을 새로 도입하지 않는다. (기존 커밋 `5e92923` 참조)
- **팔레트 상수는 영구 고정이다.** 그리드 32x32, 셀 8x8 픽셀, 텍스처 256x256, 무채색 2행, 색상 15개, 채도 4단, 명도 16단. 이 값들은 바꾸지 않는다.
- **`bpy`를 임포트하는 모듈은 테스트에서 임포트하지 않는다.** 테스트 대상 로직은 `bpy` 없는 모듈에 있어야 한다.
- **셀 0은 좌하단**(Blender UV 원점 기준), 셀 인덱스는 행 우선으로 증가한다. PNG는 위에서 아래로 저장되므로 기록 시 행을 뒤집는다.
- 대상 버전: **v0.5.0** (기존 에셋의 색이 바뀌는 파괴적 변경)

---

## File Structure

| 파일 | 책임 | 상태 |
|---|---|---|
| `scripts/gen_palette.py` | 팔레트 색 공식 정의 + PNG/데이터 모듈 생성 (개발 도구, 애드온에 미포함) | 생성 |
| `lowpoly/LP3D_Palette.png` | 고정 팔레트 텍스처. 리포에 커밋, 애드온이 로드, 익스포트 시 복사 | 생성(자동) |
| `lowpoly/palette_data.py` | 1024개 셀의 RGB 정수 튜플. `bpy` 미의존 | 생성(자동) |
| `lowpoly/colorsnap.py` | sRGB↔Oklab 변환과 최근접 셀 탐색. `bpy` 미의존 순수 함수 | 생성 |
| `lowpoly/palette.py` | Blender 이미지/머티리얼 관리, `set_color` 공개 API | 대폭 축소 |
| `core/executor.py` | 롤백에서 팔레트 상태 처리 제거 | 수정 |
| `pipeline/export.py` | 팔레트 PNG를 저장이 아닌 복사로 내보내기 | 수정 |
| `blender_manifest.toml` | 버전 상향, `/docs/` 빌드 제외 | 수정 |
| `tests/test_palette.py` | 팔레트 아티팩트·스냅 자동 테스트 | 생성 |
| `tests/fixtures/legacy_colors.json` | 기존 사용 색 181개 (품질 회귀 기준) | 생성 |
| `tests/manual_scenarios.md` | 팔레트 고정 확인 시나리오 추가 | 수정 |

**설계 주의:** 스펙 8절은 "Oklab 변환과 `_snap_cell`을 `bpy` 미의존 순수 함수로 유지"하라고만 했으나, `palette.py`는 최상단에서 `bpy`를 임포트하므로 같은 파일에 두면 테스트가 불가능하다. 따라서 이 계획은 해당 로직을 `lowpoly/colorsnap.py`로 분리한다. 스펙의 의도를 만족하는 구현 결정이다.

---

### Task 1: 팔레트 생성 스크립트와 아티팩트

`scripts/gen_palette.py`가 PNG와 `palette_data.py`를 한 번의 실행으로 뽑는다. 두 아티팩트가 어긋날 수 없게 만드는 것이 이 태스크의 핵심이다.

**Files:**
- Create: `scripts/gen_palette.py`
- Create(자동생성): `lowpoly/LP3D_Palette.png`, `lowpoly/palette_data.py`
- Test: `tests/test_palette.py`

**Interfaces:**
- Consumes: 없음 (첫 태스크)
- Produces:
  - `scripts/gen_palette.py` 모듈 레벨 함수:
    - `build_cells() -> list[tuple[int, int, int]]` — 길이 1024, 각 원소는 0~255 정수 3튜플
    - `render_png(cells: list[tuple[int, int, int]]) -> bytes` — 256x256 PNG 바이트
    - `render_data_module(cells: list[tuple[int, int, int]]) -> str` — `palette_data.py` 소스 문자열
    - `main() -> None` — 두 아티팩트를 리포 경로에 기록
  - `lowpoly/palette_data.py`: `CELLS: list[tuple[int, int, int]]` (길이 1024), `GRID = 32`, `CELL_PX = 8`, `SIZE = 256`

- [ ] **Step 1: 테스트 디렉터리에 패키지 마커와 실패 테스트를 만든다**

`tests/test_palette.py` 를 생성한다:

```python
# 팔레트 아티팩트 검증 테스트 (Blender 없이 순수 파이썬으로 실행)
import importlib.util
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다**

Run: `python -m unittest tests.test_palette -v`
Expected: FAIL — `FileNotFoundError` 또는 `scripts/gen_palette.py` 없음

- [ ] **Step 3: 생성 스크립트를 작성한다**

`scripts/gen_palette.py` 를 생성한다:

```python
#!/usr/bin/env python3
# 고정 팔레트 아티팩트 생성기 (개발 도구 — 애드온 패키지에는 포함되지 않는다)
#
# 실행: python scripts/gen_palette.py
# 출력: lowpoly/LP3D_Palette.png, lowpoly/palette_data.py
#
# 모든 색은 Oklab 좌표에서 공식으로 결정된다. 사람이 손으로 고르는 색은 없으므로
# 몇 번을 실행해도 바이트 단위로 동일한 결과가 나온다.
# 설계 근거: docs/superpowers/specs/2026-08-28-fixed-palette-design.md
import math
import os
import struct
import zlib

# ---------- 팔레트 상수 (영구 고정 — 변경 금지) ----------
GRID = 32          # 32x32 = 1024 셀
CELL_PX = 8        # 셀당 픽셀
SIZE = GRID * CELL_PX  # 256x256

NEUTRAL_ROWS = 2   # 하단 2행 = 무채색 램프
HUE_COUNT = 15     # 유채색: 색상 15개 x 2행(64칸)
CHROMA_STEPS = 4
L_STEPS = 16
L_MIN, L_MAX = 0.16, 0.97          # 유채색 명도 범위
NEUTRAL_L_MIN, NEUTRAL_L_MAX = 0.06, 0.99
# 채도는 절대값이 아니라 해당 (명도, 색상)에서 sRGB 안에 들어가는 최대 채도의 비율.
# 절대값을 쓰면 게멋 밖 조합이 클램핑되어 같은 색이 여러 칸에 중복 생성된다.
CHROMA_FRACTIONS = (0.16, 0.38, 0.64, 0.92)
# 무채색 램프 2종: (채도, 색상 각도) — 순수 그레이 / 따뜻한 그레이
NEUTRAL_TINTS = ((0.0, 0.0), (0.018, 70.0))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PNG_PATH = os.path.join(_ROOT, "lowpoly", "LP3D_Palette.png")
_DATA_PATH = os.path.join(_ROOT, "lowpoly", "palette_data.py")


# ---------- 색 공간 변환 ----------

def _lin_to_srgb(c: float) -> float:
    """선형 RGB 성분을 sRGB 감마로 변환한다."""
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def _oklab_to_linear(light: float, a: float, b: float):
    """Oklab 좌표를 선형 RGB로 변환한다 (Björn Ottosson 계수)."""
    l_ = (light + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (light - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (light - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        +4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )


def _srgb_of(light: float, chroma: float, hue: float):
    """Oklch(명도, 채도, 색상 라디안)를 sRGB 0~1 3튜플로 변환한다."""
    a, b = chroma * math.cos(hue), chroma * math.sin(hue)
    return tuple(_lin_to_srgb(c) for c in _oklab_to_linear(light, a, b))


def _in_gamut(rgb, eps: float = 1e-4) -> bool:
    """sRGB 큐브 안에 들어오는지 판정한다."""
    return all(-eps <= c <= 1 + eps for c in rgb)


def _max_chroma(light: float, hue: float) -> float:
    """주어진 명도/색상에서 sRGB 안에 들어가는 최대 채도를 이분 탐색으로 찾는다."""
    low, high = 0.0, 0.45
    for _ in range(22):   # 22회면 1e-7 정밀도 — 8비트 양자화에 충분하다
        mid = (low + high) / 2
        if _in_gamut(_srgb_of(light, mid, hue)):
            low = mid
        else:
            high = mid
    return low


def _quantize(rgb):
    """sRGB 0~1을 0~255 정수로 양자화한다."""
    return tuple(min(255, max(0, round(c * 255))) for c in rgb)


# ---------- 셀 생성 ----------

def build_cells():
    """1024개 셀의 RGB 정수 튜플 리스트를 만든다. 셀 0 = 좌하단, 행 우선."""
    cells = []
    # 하단 2행: 무채색 램프
    for row in range(NEUTRAL_ROWS):
        chroma, hue_deg = NEUTRAL_TINTS[row]
        hue = math.radians(hue_deg)
        for col in range(GRID):
            light = NEUTRAL_L_MIN + (NEUTRAL_L_MAX - NEUTRAL_L_MIN) * col / (GRID - 1)
            cells.append(_quantize(_srgb_of(light, min(chroma, _max_chroma(light, hue)), hue)))
    # 나머지 30행: 색상 15개 x (채도 4단 x 명도 16단)
    for index in range(HUE_COUNT):
        hue = 2 * math.pi * index / HUE_COUNT
        for fraction in CHROMA_FRACTIONS:
            for step in range(L_STEPS):
                light = L_MIN + (L_MAX - L_MIN) * step / (L_STEPS - 1)
                chroma = fraction * _max_chroma(light, hue)
                cells.append(_quantize(_srgb_of(light, chroma, hue)))
    return cells


# ---------- PNG 출력 ----------

def _chunk(tag: bytes, data: bytes) -> bytes:
    """PNG 청크 하나를 직렬화한다."""
    body = tag + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def render_png(cells) -> bytes:
    """셀 리스트를 256x256 8비트 트루컬러 PNG 바이트로 렌더링한다.

    셀 0이 좌하단이므로 PNG 행(위->아래)을 뒤집어 기록한다."""
    rows = []
    for py in range(SIZE):
        cell_y = GRID - 1 - (py // CELL_PX)
        line = bytearray([0])   # 필터 타입 0 (None) — 압축 결과를 결정적으로 유지
        for px in range(SIZE):
            line += bytes(cells[cell_y * GRID + (px // CELL_PX)])
        rows.append(bytes(line))
    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + _chunk(b"IEND", b""))


# ---------- 데이터 모듈 출력 ----------

def render_data_module(cells) -> str:
    """palette_data.py 소스를 만든다."""
    lines = [
        "# 자동 생성 파일 — 직접 수정하지 말 것.",
        "# 재생성: python scripts/gen_palette.py",
        "#",
        "# 고정 팔레트 텍스처(LP3D_Palette.png)의 셀별 색상값.",
        "# 셀 0 = 좌하단, 행 우선으로 증가한다.",
        "",
        "GRID = %d" % GRID,
        "CELL_PX = %d" % CELL_PX,
        "SIZE = %d" % SIZE,
        "",
        "CELLS = [",
    ]
    for index in range(0, len(cells), 8):
        chunk = cells[index:index + 8]
        lines.append("    " + " ".join("(%d, %d, %d)," % c for c in chunk))
    lines.append("]")
    lines.append("")
    return "\n".join(lines)


def main():
    cells = build_cells()
    with open(_PNG_PATH, "wb") as handle:
        handle.write(render_png(cells))
    with open(_DATA_PATH, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_data_module(cells))
    print("생성 완료: %d셀 / 고유색 %d개" % (len(cells), len(set(cells))))
    print("  " + _PNG_PATH)
    print("  " + _DATA_PATH)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 테스트가 통과하는지 확인한다**

Run: `python -m unittest tests.test_palette -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 아티팩트를 생성한다**

Run: `python scripts/gen_palette.py`
Expected 출력: `생성 완료: 1024셀 / 고유색 1022개`

- [ ] **Step 6: 아티팩트 최신성·동기화 테스트를 추가한다**

`tests/test_palette.py` 의 `TestPaletteArtifacts` 클래스 안에 다음 메서드를 추가한다:

```python
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
```

그리고 `tests/test_palette.py` 상단의 `gen = _load(...)` 줄 바로 위에 PNG 디코더를 추가한다:

```python
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
```

- [ ] **Step 7: 테스트를 실행한다**

Run: `python -m unittest tests.test_palette -v`
Expected: PASS (6 tests)

- [ ] **Step 8: `.gitignore`가 아티팩트를 막지 않는지 확인한다**

Run: `git check-ignore -v lowpoly/LP3D_Palette.png lowpoly/palette_data.py; echo "exit=$?"`
Expected: 출력 없음, `exit=1` (무시되지 않음)

- [ ] **Step 9: 커밋**

```bash
git add scripts/gen_palette.py lowpoly/LP3D_Palette.png lowpoly/palette_data.py tests/test_palette.py
git commit -m "팔레트 생성 스크립트와 고정 아티팩트 추가

Oklab 균등 격자 1024칸(무채색 2행 + 색상 15개 x 채도 4 x 명도 16)을
공식으로 생성한다. PNG와 palette_data.py를 한 번의 실행으로 뽑아
두 아티팩트가 어긋날 수 없게 했다."
```

---

### Task 2: Oklab 스냅 모듈

임의 RGB를 지각적으로 가장 가까운 셀로 매핑한다. `bpy`에 의존하지 않으므로 Blender 없이 테스트한다.

**Files:**
- Create: `lowpoly/colorsnap.py`
- Create: `tests/fixtures/legacy_colors.json`
- Modify: `tests/test_palette.py` (테스트 클래스 추가)

**Interfaces:**
- Consumes: `lowpoly/palette_data.py`의 `CELLS`, `GRID`
- Produces:
  - `srgb_to_oklab(rgb: tuple[float, float, float]) -> tuple[float, float, float]` — 입력은 0~1 범위 sRGB
  - `snap_cell(color) -> int` — 0~1 범위 RGB(길이 3 이상 시퀀스)를 받아 0~1023 셀 인덱스 반환
  - `cell_uv(cell: int) -> tuple[float, float]` — 셀 중앙 UV 좌표

- [ ] **Step 1: 품질 기준 픽스처를 만든다**

`tests/fixtures/legacy_colors.json` 을 생성한다. 기존 `AssetLibrary_Palette.png`에서 추출한 실사용 색 181개로, 스냅 품질 회귀를 막는 기준이다:

```json
[[20,22,23],[29,29,29],[34,50,62],[36,43,43],[37,37,39],[39,39,42],[40,72,54],[42,44,46],[44,82,51],[46,41,26],[46,61,69],[47,47,51],[47,49,55],[49,76,116],[50,50,54],[50,52,55],[50,52,58],[51,88,57],[52,64,39],[52,95,57],[56,66,64],[57,99,63],[62,105,68],[64,71,63],[64,94,97],[65,66,73],[66,66,71],[69,61,59],[70,104,56],[70,115,74],[70,121,73],[71,53,37],[71,64,61],[71,95,65],[72,55,40],[73,67,61],[73,75,79],[73,97,67],[74,54,40],[75,76,83],[76,84,73],[76,117,78],[76,118,172],[77,61,46],[77,115,133],[78,82,88],[78,112,61],[79,60,43],[79,94,60],[80,81,88],[81,79,78],[82,51,31],[82,62,44],[83,85,89],[83,90,90],[83,98,63],[83,109,61],[87,66,46],[87,67,47],[87,79,56],[87,86,84],[87,88,97],[88,89,93],[89,61,41],[90,67,48],[90,128,72],[91,72,53],[92,71,51],[92,82,77],[92,97,79],[93,70,50],[93,96,98],[93,101,90],[93,137,93],[94,99,104],[96,91,83],[97,71,48],[97,87,82],[98,98,89],[99,70,54],[101,95,88],[101,137,78],[102,77,54],[102,92,87],[103,106,111],[103,154,195],[104,93,78],[104,103,101],[104,130,76],[105,81,59],[105,83,62],[105,142,82],[106,135,95],[107,97,71],[107,112,122],[108,107,106],[110,70,49],[110,115,115],[110,137,97],[111,114,119],[112,82,56],[112,112,117],[112,179,197],[113,141,79],[114,124,109],[115,99,64],[117,84,51],[117,87,59],[117,90,72],[119,148,87],[120,54,55],[120,120,120],[121,119,108],[122,112,87],[122,115,117],[125,57,58],[125,115,77],[128,82,46],[128,102,71],[128,112,77],[128,117,98],[128,131,137],[129,132,140],[130,129,117],[131,180,197],[132,64,44],[132,119,98],[133,128,97],[135,110,84],[135,139,147],[138,69,49],[138,164,95],[140,143,148],[142,140,137],[146,69,69],[146,75,69],[146,84,69],[148,107,66],[148,133,87],[148,150,156],[151,99,45],[152,81,53],[153,126,98],[153,148,115],[154,158,163],[155,178,110],[156,150,117],[156,155,150],[158,133,97],[159,84,77],[160,147,123],[163,160,145],[166,153,117],[166,160,145],[168,69,64],[172,111,62],[173,51,50],[173,148,107],[176,174,170],[182,142,82],[183,92,89],[183,148,75],[184,60,58],[184,168,122],[185,101,61],[185,143,79],[186,106,66],[194,103,99],[194,148,63],[195,156,68],[196,168,86],[201,199,193],[212,206,186],[213,202,152],[213,207,190],[217,179,64],[218,218,211],[221,211,167],[224,227,230],[235,214,89],[255,219,107]]
```

- [ ] **Step 2: 실패 테스트를 추가한다**

`tests/test_palette.py` 의 맨 아래, `if __name__ == "__main__":` 블록 **위에** 다음 클래스를 추가한다:

```python
class TestColorSnap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # colorsnap의 임포트 폴백이 sys.modules의 palette_data를 찾으므로
        # 반드시 palette_data를 먼저 로드해야 한다
        cls.data = _load("palette_data", "lowpoly/palette_data.py")
        cls.snap = _load("colorsnap", "lowpoly/colorsnap.py")

    # 팔레트에 있는 색은 반드시 자기 자신으로 스냅되어야 한다
    def test_palette_colors_snap_to_themselves(self):
        for cell, rgb in enumerate(self.data.CELLS):
            got = self.snap.snap_cell(tuple(c / 255 for c in rgb))
            self.assertEqual(self.data.CELLS[got], rgb, "셀 %d가 다른 색으로 스냅됨" % cell)

    # 기존 실사용 색의 스냅 오차가 지각 한계 안에 들어와야 한다 (품질 회귀 방지)
    def test_legacy_colors_snap_within_tolerance(self):
        import json
        import math
        path = os.path.join(_ROOT, "tests", "fixtures", "legacy_colors.json")
        with open(path, encoding="utf-8") as handle:
            legacy = json.load(handle)
        self.assertEqual(len(legacy), 181)
        worst = 0.0
        for rgb in legacy:
            normalized = tuple(c / 255 for c in rgb)
            cell = self.snap.snap_cell(normalized)
            target = self.snap.srgb_to_oklab(normalized)
            picked = self.snap.srgb_to_oklab(tuple(c / 255 for c in self.data.CELLS[cell]))
            worst = max(worst, math.dist(target, picked))
        # 측정된 최대 오차는 0.036. 0.04를 넘으면 팔레트 품질이 나빠진 것이다
        self.assertLess(worst, 0.04, "최대 스냅 오차 %.4f" % worst)

    # UV는 셀 중앙을 가리켜야 필터링 번짐이 없다
    def test_cell_uv_is_cell_center(self):
        self.assertAlmostEqual(self.snap.cell_uv(0)[0], 0.5 / 32)
        self.assertAlmostEqual(self.snap.cell_uv(0)[1], 0.5 / 32)
        self.assertAlmostEqual(self.snap.cell_uv(1023)[0], 31.5 / 32)
        self.assertAlmostEqual(self.snap.cell_uv(1023)[1], 31.5 / 32)
```

- [ ] **Step 3: 테스트가 실패하는지 확인한다**

Run: `python -m unittest tests.test_palette.TestColorSnap -v`
Expected: FAIL — `lowpoly/colorsnap.py` 없음

- [ ] **Step 4: 스냅 모듈을 구현한다**

`lowpoly/colorsnap.py` 를 생성한다:

```python
# 색 스냅: 임의 RGB를 고정 팔레트의 가장 가까운 셀로 매핑한다.
#
# 이 모듈은 bpy에 의존하지 않는 순수 함수만 담는다 (Blender 없이 테스트 가능).
# 거리 계산에 Oklab을 쓰는 이유: 단순 RGB 유클리드 거리는 지각적으로 균등하지
# 않아 어두운 색끼리 엉뚱하게 붙는다.
import math

try:                      # 애드온으로 로드될 때
    from .palette_data import CELLS, GRID
except ImportError:       # 파일 단위로 직접 로드될 때 (테스트)
    from palette_data import CELLS, GRID


def _srgb_to_linear(value: float) -> float:
    """sRGB 감마 성분을 선형 RGB로 변환한다."""
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def srgb_to_oklab(rgb):
    """sRGB 0~1 3튜플을 Oklab 좌표로 변환한다 (Björn Ottosson 계수)."""
    red, green, blue = (_srgb_to_linear(c) for c in rgb[:3])
    long_ = 0.4122214708 * red + 0.5363325363 * green + 0.0514459929 * blue
    medium = 0.2119034982 * red + 0.6806995451 * green + 0.1073969566 * blue
    short = 0.0883024619 * red + 0.2817188376 * green + 0.6299787005 * blue
    long_, medium, short = (_cbrt(long_), _cbrt(medium), _cbrt(short))
    return (
        0.2104542553 * long_ + 0.7936177850 * medium - 0.0040720468 * short,
        1.9779984951 * long_ - 2.4285922050 * medium + 0.4505937099 * short,
        0.0259040371 * long_ + 0.7827717662 * medium - 0.8086757660 * short,
    )


def _cbrt(value: float) -> float:
    """음수도 처리하는 세제곱근."""
    return value ** (1 / 3) if value >= 0 else -((-value) ** (1 / 3))


# 팔레트 1024색의 Oklab 좌표를 임포트 시 한 번만 계산해 캐시한다
_CELL_LAB = tuple(srgb_to_oklab(tuple(c / 255 for c in rgb)) for rgb in CELLS)


def snap_cell(color) -> int:
    """0~1 범위 RGB를 받아 Oklab 최근접 셀 인덱스(0~1023)를 반환한다.

    1024개 선형 탐색이지만 모델당 색이 10개 안팎이라 비용은 무시할 수준이다."""
    target_l, target_a, target_b = srgb_to_oklab(color)
    best_cell, best_distance = 0, None
    for cell, (light, a_axis, b_axis) in enumerate(_CELL_LAB):
        distance = ((light - target_l) ** 2
                    + (a_axis - target_a) ** 2
                    + (b_axis - target_b) ** 2)
        if best_distance is None or distance < best_distance:
            best_cell, best_distance = cell, distance
    return best_cell


def cell_uv(cell: int):
    """셀 중앙의 UV 좌표를 반환한다.

    페이스의 UV를 셀 중앙 한 점으로 모으므로 텍스처 필터링 번짐이 없다."""
    return (cell % GRID + 0.5) / GRID, (cell // GRID + 0.5) / GRID
```

- [ ] **Step 5: 테스트가 통과하는지 확인한다**

Run: `python -m unittest tests.test_palette -v`
Expected: PASS (9 tests)

- [ ] **Step 6: 커밋**

```bash
git add lowpoly/colorsnap.py tests/fixtures/legacy_colors.json tests/test_palette.py
git commit -m "Oklab 최근접 색 스냅 모듈 추가

임의 RGB를 고정 팔레트의 가장 가까운 셀로 매핑한다. RGB 유클리드
거리는 지각적으로 균등하지 않아 Oklab을 쓴다. bpy 미의존이라
Blender 없이 테스트한다. 기존 실사용 181색 기준 최대 오차 dE 0.036."
```

---

### Task 3: `lowpoly/palette.py` 재작성

팔레트가 읽기 전용이 되면서 가변 상태 기계(셀 배정, 픽셀 쓰기, 그리드 이전, UV 재매핑, 스냅샷/복원)가 전부 사라진다.

**Files:**
- Modify: `lowpoly/palette.py` (전체 재작성)

**Interfaces:**
- Consumes: `lowpoly/colorsnap.py`의 `snap_cell`, `cell_uv`; `lowpoly/palette_data.py`의 `CELLS`, `SIZE`
- Produces:
  - `set_color(obj, color, faces=None)` — 시그니처·동작 변경 없음 (기존 API 유지)
  - `save_palette_png(directory: str) -> str` — 번들 PNG를 복사하고 저장 경로 반환
  - `PALETTE_IMAGE = "LP3D_Palette"` — `core/executor.py`가 롤백 예외 처리에 쓴다
  - **삭제됨:** `snapshot_state`, `restore_state` (호출부는 Task 4에서 제거)

- [ ] **Step 1: 현재 파일을 대체할 새 구현을 작성한다**

`lowpoly/palette.py` 의 **전체 내용**을 다음으로 교체한다:

```python
# 팔레트 머티리얼: 고정 256x256 팔레트 텍스처 + UV 셀 매핑
#
# 모든 생성 모델이 하나의 팔레트 텍스처/머티리얼을 공유 → Unity에서 드로우콜 1개.
# 페이스의 UV를 색상 셀 중앙 한 점으로 모으는 방식이라 텍스처 필터링 번짐이 없다.
#
# 팔레트는 읽기 전용이다. 색→셀 매핑이 공식으로 결정되므로 생성 순서와 무관하게
# 항상 같은 텍스처가 나오고, 사용자는 에셋 라이브러리에서 머티리얼만 교체해도
# 색이 그대로 유지된다. 그리드/셀 크기는 영구 고정이며 변경하지 않는다.
# 설계 근거: docs/superpowers/specs/2026-08-28-fixed-palette-design.md
import os
import shutil

import bpy

from .colorsnap import cell_uv, snap_cell
from .palette_data import CELLS, SIZE

PALETTE_IMAGE = "LP3D_Palette"     # executor의 롤백 예외 처리에서 참조한다
PALETTE_MATERIAL = "LP3D_Palette"

_PNG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "LP3D_Palette.png")
# 구버전(순서 기반 팔레트)이 이미지에 남긴 커스텀 프로퍼티 — 있으면 교체 대상이다
_LEGACY_PROPS = ("lp3d_colors", "lp3d_grid")


# ---------- 이미지 ----------

def _expected_pixels() -> list:
    """고정 팔레트의 픽셀 버퍼(선형 float RGBA 평면 리스트)를 만든다.

    Blender의 이미지 픽셀은 선형이지만, 이미지 컬러스페이스를 sRGB로 두면
    파일에서 로드한 것과 동일하게 해석된다. 이 함수는 PNG를 로드할 수 없는
    예외 상황의 대비책으로만 쓴다."""
    from .colorsnap import _srgb_to_linear
    grid = SIZE // 8
    buffer = [0.0] * (SIZE * SIZE * 4)
    for py in range(SIZE):
        cell_y = py // 8
        for px in range(SIZE):
            red, green, blue = CELLS[cell_y * grid + (px // 8)]
            offset = (py * SIZE + px) * 4
            buffer[offset:offset + 4] = [_srgb_to_linear(red / 255),
                                         _srgb_to_linear(green / 255),
                                         _srgb_to_linear(blue / 255), 1.0]
    return buffer


def _is_stale(img) -> bool:
    """기존 이미지가 고정 팔레트와 다른지 판정한다."""
    if tuple(img.size) != (SIZE, SIZE):
        return True
    if any(prop in img.keys() for prop in _LEGACY_PROPS):
        return True
    grid = SIZE // 8
    buffer = [0.0] * len(img.pixels)
    img.pixels.foreach_get(buffer)
    # 대표 셀 몇 개만 대조한다 (전체 대조는 불필요하게 비싸다)
    for cell in (0, grid + 1, len(CELLS) - 1):
        col, row = cell % grid, cell // grid
        x, y = col * 8 + 4, row * 8 + 4
        offset = (y * SIZE + x) * 4
        expected = CELLS[cell]
        for channel in range(3):
            actual = round(_linear_to_srgb(buffer[offset + channel]) * 255)
            if abs(actual - expected[channel]) > 1:   # 8비트 왕복 오차 1 허용
                return True
    return False


def _linear_to_srgb(value: float) -> float:
    """선형 RGB 성분을 sRGB 감마로 변환한다."""
    if value <= 0.0031308:
        return 12.92 * value
    return 1.055 * (max(value, 0.0) ** (1 / 2.4)) - 0.055


def _get_image() -> bpy.types.Image:
    """고정 팔레트 이미지를 반환한다. 없거나 낡았으면 번들 PNG로 채운다.

    구버전 이미지를 교체할 때 데이터블록을 제거하지 않고 픽셀만 덮어쓴다.
    머티리얼 노드의 이미지 참조가 끊기는 것을 막기 위함이다."""
    img = bpy.data.images.get(PALETTE_IMAGE)
    if img is None:
        img = bpy.data.images.load(_PNG_PATH)
        img.name = PALETTE_IMAGE
        img.colorspace_settings.name = 'sRGB'
        img.pack()   # .blend를 옮겨도 텍스처가 살아 있도록 임베드한다
        return img
    if _is_stale(img):
        for prop in _LEGACY_PROPS:
            if prop in img.keys():
                del img[prop]
        if tuple(img.size) != (SIZE, SIZE):
            img.scale(SIZE, SIZE)
        img.colorspace_settings.name = 'sRGB'
        img.pixels.foreach_set(_expected_pixels())
        img.update()
        img.pack()
    return img


# ---------- 머티리얼 ----------

def _get_material() -> bpy.types.Material:
    """팔레트 머티리얼을 반환한다(없으면 생성).

    노드 설정(Closest 보간 / sRGB / Roughness 0.9)은 에셋 라이브러리 머티리얼과
    맞춰야 하는 값이다. 어긋나면 머티리얼 교체 시 색이 미묘하게 달라진다."""
    mat = bpy.data.materials.get(PALETTE_MATERIAL)
    if mat is None:
        mat = bpy.data.materials.new(PALETTE_MATERIAL)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        bsdf = nodes.get("Principled BSDF")
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = _get_image()
        tex.interpolation = 'Closest'   # 셀 경계 번짐 방지
        tex.location = (-300, 300)
        links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        bsdf.inputs["Roughness"].default_value = 0.9   # 캐주얼 톤: 무광
    else:
        # 기존 머티리얼이 낡은 이미지를 가리키고 있을 수 있으므로 다시 연결한다
        img = _get_image()
        for node in mat.node_tree.nodes:
            if node.type == 'TEX_IMAGE':
                node.image = img
                node.interpolation = 'Closest'
    return mat


# ---------- 공개 API ----------

def set_color(obj, color, faces=None):
    """오브젝트(또는 일부 페이스)에 팔레트 색을 입힌다.

    color=(r,g,b) 0~1 범위. faces=None이면 전체, 아니면 페이스 인덱스 리스트.
    요청한 색은 고정 팔레트에서 지각적으로 가장 가까운 스와치로 스냅된다.
    예: lp.set_color(barrel, (0.55, 0.35, 0.18))  # 나무색
        lp.set_color(barrel, (0.4, 0.4, 0.45), faces=band_faces)  # 금속 밴드만"""
    mesh = obj.data
    mat = _get_material()
    if mat.name not in [m.name for m in mesh.materials if m]:
        mesh.materials.clear()
        mesh.materials.append(mat)
    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")
    uv_layer = mesh.uv_layers.active.data
    u, v = cell_uv(snap_cell(color))
    target = set(faces) if faces is not None else None
    for poly in mesh.polygons:
        if target is not None and poly.index not in target:
            continue
        for loop_idx in poly.loop_indices:
            uv_layer[loop_idx].uv = (u, v)
    return obj


def save_palette_png(directory: str) -> str:
    """익스포트 시 팔레트 텍스처를 PNG로 저장하고 경로를 반환.

    Blender의 저장 경로를 거치지 않고 번들 PNG를 그대로 복사하므로,
    내보낸 파일은 항상 리포의 원본과 바이트 단위로 동일하다."""
    path = os.path.join(directory, "%s.png" % PALETTE_IMAGE)
    shutil.copyfile(_PNG_PATH, path)
    return path
```

- [ ] **Step 2: 문법과 미해결 참조를 검사한다**

Run: `python -m py_compile lowpoly/palette.py lowpoly/colorsnap.py scripts/gen_palette.py`
Expected: 출력 없음 (성공)

- [ ] **Step 3: 삭제 대상이 모두 사라졌는지 확인한다**

Run: `grep -n "_assign_cell\|_paint_cell\|_write_pixels\|_migrate\|_remap_uvs\|_sample_cell\|_nearest_cell\|_new_image\|snapshot_state\|restore_state\|lp3d_colors\|lp3d_grid" lowpoly/palette.py`
Expected: `_LEGACY_PROPS` 정의와 `_is_stale` 안의 `lp3d_colors`/`lp3d_grid` 참조만 나온다. `_assign_cell`, `_paint_cell`, `_write_pixels`, `_migrate`, `_remap_uvs`, `_sample_cell`, `_nearest_cell`, `_new_image`, `snapshot_state`, `restore_state`는 **한 건도 나오면 안 된다.**

- [ ] **Step 4: 기존 자동 테스트가 여전히 통과하는지 확인한다**

Run: `python -m unittest tests.test_palette -v`
Expected: PASS (9 tests) — `palette.py`는 `bpy` 의존이라 테스트하지 않지만, `colorsnap`/아티팩트 테스트가 깨지지 않았는지 본다

- [ ] **Step 5: 커밋**

```bash
git add lowpoly/palette.py
git commit -m "palette.py를 읽기 전용 고정 팔레트로 재작성

색에서 셀이 공식으로 결정되므로 셀 배정·픽셀 쓰기·그리드 이전·
UV 재매핑·스냅샷/복원이 모두 불필요해졌다. set_color의 공개
시그니처와 동작은 그대로라 에이전트 코드와 프롬프트는 손대지 않는다."
```

---

### Task 4: 호출부 정리 (executor, export)

`palette.snapshot_state` / `restore_state`가 사라졌으므로 호출부를 제거하고, 롤백이 팔레트 이미지를 지우지 않게 막는다.

**Files:**
- Modify: `core/executor.py` (`snapshot`, `rollback`)
- Modify: `pipeline/export.py` (임포트 경로 확인)

**Interfaces:**
- Consumes: `lowpoly/palette.py`의 `PALETTE_IMAGE`, `save_palette_png`
- Produces: 없음 (호출부 정리)

- [ ] **Step 1: `snapshot()`에서 팔레트 상태 보존을 제거한다**

`core/executor.py` 의 `snapshot()` 함수를 다음으로 교체한다:

```python
def snapshot():
    # 팔레트는 읽기 전용 고정 텍스처이므로 별도로 보존할 상태가 없다
    return {kind: set(getattr(bpy.data, kind).keys()) for kind in _DATA_KINDS}
```

- [ ] **Step 2: `rollback()`에서 복원 호출을 제거하고 팔레트 이미지를 보호한다**

`core/executor.py` 의 `rollback()` 함수를 다음으로 교체한다:

```python
def rollback(snap):
    """스냅샷 이후 생긴 데이터블록을 제거한다. 오브젝트 → 컬렉션 → 데이터 순."""
    from ..lowpoly.palette import PALETTE_IMAGE

    for name in set(bpy.data.objects.keys()) - snap["objects"]:
        obj = bpy.data.objects.get(name)
        if obj:
            bpy.data.objects.remove(obj)
    for name in set(bpy.data.collections.keys()) - snap["collections"]:
        coll = bpy.data.collections.get(name)
        if coll:
            bpy.data.collections.remove(coll)
    for kind in ("meshes", "materials", "images", "node_groups"):
        data = getattr(bpy.data, kind)
        for name in set(data.keys()) - snap[kind]:
            # 실행이 실패해도 고정 팔레트 이미지는 지우지 않는다
            if kind == "images" and name == PALETTE_IMAGE:
                continue
            block = data.get(name)
            if block and block.users == 0:
                data.remove(block)
```

- [ ] **Step 3: 남은 참조가 없는지 확인한다**

Run: `grep -rn "snapshot_state\|restore_state\|_palette" --include="*.py" .`
Expected: 출력 없음

- [ ] **Step 4: `pipeline/export.py`가 그대로 동작하는지 확인한다**

`pipeline/export.py`의 `from ..lowpoly.palette import save_palette_png`와 `save_palette_png(out_dir)` 호출은 **변경하지 않는다.** 함수명과 반환값이 유지되었기 때문이다.

Run: `grep -n "save_palette_png" pipeline/export.py`
Expected: 2줄 (임포트 1, 호출 1)

- [ ] **Step 5: 전체 문법 검사와 테스트**

Run: `python -m compileall -q core lowpoly pipeline agents ui && python -m unittest tests.test_palette -v`
Expected: 컴파일 오류 없음, PASS (9 tests)

- [ ] **Step 6: 커밋**

```bash
git add core/executor.py
git commit -m "롤백에서 팔레트 상태 처리 제거

팔레트가 읽기 전용이라 실행이 셀을 소비하지 않으므로 스냅샷/복원이
불필요하다. 대신 실행 실패 시 롤백이 고정 팔레트 이미지를 지우지
않도록 예외 처리한다."
```

---

### Task 5: 패키징·버전·문서

확장 패키지에 PNG가 들어가는지 확인하고, 파괴적 변경을 릴리스 노트와 수동 시나리오에 명시한다.

**Files:**
- Modify: `blender_manifest.toml`
- Modify: `tests/manual_scenarios.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: 없음
- Produces: 없음

- [ ] **Step 1: 매니페스트의 버전을 올리고 `/docs/`를 빌드에서 제외한다**

`blender_manifest.toml` 에서 두 곳을 수정한다.

버전 줄:

```toml
version = "0.5.0"
```

`paths_exclude_pattern` 배열의 `"/tests/",` 바로 다음 줄에 추가:

```toml
  "/docs/",
```

- [ ] **Step 2: 리포에 남은 구버전 표기가 없는지 확인한다**

`__init__.py`에는 버전 문자열이 없다(확인함). 버전은 매니페스트에만 있다.

Run: `grep -rn "0\.4\.4" --include="*.py" --include="*.toml" --include="*.md" . | grep -v docs/superpowers`
Expected: 출력 없음

- [ ] **Step 3: PNG가 패키지에 포함되는지 확인한다**

Run: `python - <<'PY'
import fnmatch
patterns = ["__pycache__/", "/.git/", "/*.zip", "/.github/", "/.gitignore",
            "/scripts/", "/tests/", "/docs/", "/dist/", "/Generate/", "*.blend",
            "/.claude/", "/.omc/", "*.py[cod]", ".DS_Store", "*.blend1"]
target = "/lowpoly/LP3D_Palette.png"
hit = [p for p in patterns if fnmatch.fnmatch(target, p) or p.strip("/") in target.split("/")]
print("제외 매칭:", hit or "없음 (패키지에 포함됨)")
PY`
Expected: `제외 매칭: 없음 (패키지에 포함됨)`

- [ ] **Step 4: 수동 시나리오에 팔레트 고정 확인 항목을 추가한다**

`tests/manual_scenarios.md` 의 번호 목록 맨 끝(항목 10 다음)에 추가한다:

```markdown
11. **팔레트 고정**: 서로 다른 프롬프트로 모델 2개 생성 → 각각 FBX 익스포트 → 두 폴더의 `LP3D_Palette.png`가 바이트 단위로 동일한지 확인 (`cmp` 또는 해시 비교). 리포의 `lowpoly/LP3D_Palette.png`와도 동일해야 한다.
12. **머티리얼 교체**: 생성한 모델의 머티리얼을 에셋 라이브러리의 팔레트 머티리얼로 교체 → 색이 그대로 유지되는지 확인. 전제: 라이브러리 텍스처가 `lowpoly/LP3D_Palette.png`와 동일하고, 노드 설정이 Closest 보간 / sRGB / Roughness 0.9여야 한다.
13. **구버전 .blend 열기**: v0.4.x로 만든 `.blend`를 열고 생성 실행 → 팔레트 이미지가 고정 팔레트로 교체되고, 머티리얼 노드 연결이 끊기지 않는지 확인. 기존 모델의 색은 바뀐다(의도된 파괴적 변경).
```

- [ ] **Step 5: README에 팔레트 재생성 방법을 적는다**

`README.md` 의 맨 끝에 다음 절을 추가한다:

```markdown
## 팔레트 텍스처

모든 생성 모델은 고정된 256x256 팔레트 텍스처 하나를 공유한다. 색은 Oklab
좌표에서 공식으로 결정되므로 생성 순서와 무관하게 항상 동일하다 — 덕분에
에셋 라이브러리에서 머티리얼만 교체해도 색이 그대로 유지된다.

- 텍스처: `lowpoly/LP3D_Palette.png` (자동 생성, 커밋됨)
- 색 데이터: `lowpoly/palette_data.py` (자동 생성, 커밋됨)
- 재생성: `python scripts/gen_palette.py`
- 검증: `python -m unittest tests.test_palette -v`

두 아티팩트는 직접 수정하지 않는다. 팔레트 공식을 바꾸면 기존에 만든 모든
에셋의 색이 바뀌므로 파괴적 변경으로 취급한다.
```

- [ ] **Step 6: 전체 테스트를 돌린다**

Run: `python -m unittest discover -s tests -p "test_*.py" -v`
Expected: PASS (9 tests)

- [ ] **Step 7: 커밋**

```bash
git add blender_manifest.toml tests/manual_scenarios.md README.md
git commit -m "v0.5.0: 고정 팔레트 텍스처

파괴적 변경 — 팔레트 색 배치가 바뀌므로 v0.4.x로 만든 기존 에셋의
색이 달라진다. 에셋 라이브러리의 팔레트 텍스처를
lowpoly/LP3D_Palette.png로 교체해야 한다."
```

---

## 실행 후 사용자 작업

구현이 끝나면 사용자가 직접 해야 하는 일이 하나 있다. 마지막에 안내한다:

`lowpoly/LP3D_Palette.png`를
`D:\Project_Work\Blender\Blender_Asset_Library\Textures\AssetLibrary_Palette.png`
로 복사한다. 이 시점에 기존 에셋의 색이 바뀐다(스펙 3절 범위 밖 — 사용자가
호환 불필요를 명시적으로 결정했다).

에셋 라이브러리 머티리얼의 노드 설정도 확인한다: 텍스처 보간 `Closest`,
이미지 컬러스페이스 `sRGB`, Principled BSDF `Roughness` 0.9.
