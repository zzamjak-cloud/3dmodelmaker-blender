# 이미지→3D 셰이프 생성 클라이언트 (셰이프 서버 HTTP)
#
# 캐릭터를 프리미티브 코드로 조립하면 원화와 닮지 않는다. 턴어라운드 시트의 정면을
# 이미지→3D 모델(TRELLIS.2)에 넣어 셰이프와 PBR 텍스처를 한 번에 받는다 — 리메시·데시메이트·
# UV 언랩·텍스처 굽기까지 서버가 끝내므로 애드온은 결과를 씬에 올리기만 한다(lowpoly/retopo.py).
#
# 서버 계약: GET /status → {"capabilities": {...}}, POST /generate (base64 뷰 + 파라미터) → GLB 바이트.
# 서버 구현은 scripts/trellis3d/ — 각 사용자가 자기 Modal 계정에 배포하고 주소·토큰을 환경설정에 넣는다.
# 코드에는 어떤 서버 주소·토큰도 들어 있지 않다(OpenRouter 키와 같은 원칙).
import base64
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

# 턴어라운드 시트(3x2) 칸 배치 — multiview.SHEET_LAYOUT["TURNAROUND"]과 같은 순서
TURNAROUND_CELLS = {
    "front": (0, 0), "back": (1, 0), "left": (2, 0),
    "right": (0, 1), "top": (1, 1), "quarter": (2, 1),
}
# 서버에 보내는 뷰 (top·3/4은 멀티뷰 모델이 받지 않는다)
SEND_VIEWS = ("front", "back", "left", "right")
# 시트 분할 규칙 — 생성 모델은 격자를 정확한 1/3·1/2 위치에 그리지 않고(실측 ±5%), 라벨을 칸 위 또는 아래에 붙인다.
# 등분으로 자르면 옆 칸의 라벨·격자선이 섞여 들어와 셰이프에 두께 0 의 판으로 복원된다(실측).
DARK = 0.90            # min(RGB) 가 이보다 어두우면 배경(흰색)이 아니다 — 격자선 회색(≈0.82) 포함
GRID_BAND = 0.12       # 격자선을 찾는 범위: 등분 위치 ±이 비율
GRID_MIN_FRAC = 0.5    # 행/열의 이 비율 이상이 어두워야 격자선으로 인정
GRID_LINE_HALO = 8     # 격자선은 얇다 — 이만큼 떨어진 이웃 줄과의 어두움 차이가 GRID_LINE_CONTRAST 이상이어야 한다
GRID_LINE_CONTRAST = 0.25   # 캐릭터 몸통 열(이웃도 같이 어두움)을 격자선으로 오검출하지 않기 위한 대비 하한(실측 v7)
CELL_MARGIN = 8        # 셀 가장자리 격자 잔재를 흰색으로 덧칠하는 폭(px)
LABEL_MAX_FRAC = 0.12  # 라벨 블록 최대 높이 비율
LABEL_ZONE = 0.18      # 라벨은 칸의 위/아래 이 비율 안에서만 나타난다


def _darkness(px, w, h):
    """행·열별 '배경 아닌' 픽셀 비율. 격자선은 행(가로선) 또는 열(세로선) 전체가 어둡다."""
    rows = [0] * h
    cols = [0] * w
    for y in range(h):
        base = y * w
        for x in range(w):
            i = (base + x) * 4
            if min(px[i], px[i + 1], px[i + 2]) < DARK:
                rows[y] += 1
                cols[x] += 1
    return [r / w for r in rows], [c / h for c in cols]


def grid_cuts(fractions, parts: int) -> list:
    """내부 격자선 위치(parts-1개). 등분 위치 ±GRID_BAND 에서 충분히 어둡고 얇은(이웃 대비) 줄 중 가장 어두운 것을 고르고, 없으면 등분."""
    n = len(fractions)
    halo = GRID_LINE_HALO if n > 4 * GRID_LINE_HALO else 1

    def contrast(i):
        # 얇은 선: 자기 줄은 어둡고 halo 만큼 떨어진 양쪽 이웃은 밝다. 몸통·방패처럼 넓은 덩어리는 이웃도 어두워 걸러진다
        return fractions[i] - max(fractions[max(0, i - halo)], fractions[min(n - 1, i + halo)])

    cuts = []
    for k in range(1, parts):
        guess = n * k // parts
        lo, hi = max(1, int(guess - GRID_BAND * n)), min(n - 2, int(guess + GRID_BAND * n))
        candidates = [i for i in range(lo, hi + 1) if fractions[i] >= GRID_MIN_FRAC and contrast(i) >= GRID_LINE_CONTRAST]
        cuts.append(max(candidates, key=lambda i: fractions[i]) if candidates else guess)
    return cuts


def _blocks(flags):
    out, start = [], None
    for i, v in enumerate(flags):
        if v and start is None:
            start = i
        if not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(flags)))
    return out


def label_rows(occupied, height: int) -> list:
    """지워야 할 행 목록 — 칸 위/아래 가장자리의 작은 분리 블록(라벨 글자·격자 잔재). 가운데 작은 조각은 남긴다."""
    blocks = _blocks(occupied)
    if not blocks:
        return []
    body = max(blocks, key=lambda b: b[1] - b[0])
    zone = height * LABEL_ZONE
    rows = []
    for b in blocks:
        small = (b[1] - b[0]) < height * LABEL_MAX_FRAC
        at_edge = b[0] < zone or b[1] > height - zone
        if small and at_edge and (b is not body or len(blocks) == 1):
            rows.extend(range(b[0], b[1]))
    return rows


def clean_cell(px, w, h):
    """셀 픽셀(RGBA float 리스트, 좌하단 원점)에서 가장자리 여백과 라벨 블록을 흰색으로 덧칠한다. 크기는 그대로 둔다 —
    서버가 알파 bbox 로 크롭하므로 칸을 다시 자르면 뷰끼리 축척이 어긋난다."""
    m = CELL_MARGIN if w > 4 * CELL_MARGIN and h > 4 * CELL_MARGIN else 0
    white = [1.0, 1.0, 1.0, 1.0]

    def paint_row(y):
        base = y * w * 4
        px[base:base + w * 4] = white * w

    for y in list(range(m)) + list(range(h - m, h)):
        paint_row(y)
    if m:
        for y in range(h):
            base = y * w * 4
            px[base:base + m * 4] = white * m
            px[base + (w - m) * 4:base + w * 4] = white * m
    occupied = []
    for y in range(h):
        base = y * w
        occupied.append(any(min(px[(base + x) * 4], px[(base + x) * 4 + 1], px[(base + x) * 4 + 2]) < DARK
                            for x in range(m, w - m)))
    for y in label_rows(occupied, h):
        paint_row(y)
    return px


def server_url() -> str:
    from .. import preferences
    prefs = preferences.get_prefs()
    return (getattr(prefs, "shapegen_url", "") or "http://127.0.0.1:8081").rstrip("/")


def auth_headers() -> dict:
    """클라우드 서버 인증 헤더. 토큰이 없으면(로컬 서버) 빈 dict.

    `키:시크릿` 형식은 Modal 프록시 인증(Modal-Key/Modal-Secret) — 토큰 없는 요청은 Modal 엣지에서
    거절되어 GPU 컨테이너가 뜨지 않으므로 주소가 노출돼도 비용이 발생하지 않는다."""
    from .. import preferences
    token = (getattr(preferences.get_prefs(), "shapegen_token", "") or "").strip()
    if not token:
        return {}
    if ":" in token:
        key, secret = token.split(":", 1)
        return {"Modal-Key": key.strip(), "Modal-Secret": secret.strip()}
    return {"Authorization": "Bearer " + token}


def is_enabled() -> bool:
    from .. import preferences
    return bool(getattr(preferences.get_prefs(), "use_shapegen", True))


def status_timeout() -> float:
    """/status 점검 타임아웃 — 로컬은 즉답이지만 클라우드 웹 컨테이너는 유휴 정지에서 깨어나는 데 수 초 걸린다."""
    return 20.0 if server_url().startswith("https://") else 1.5


def is_available(timeout: float = None) -> bool:
    """서버가 떠 있는가. 없으면 기존 코드 모델링 경로로 조용히 폴백한다."""
    if not is_enabled():
        return False
    if timeout is None:
        timeout = status_timeout()
    try:
        req = urllib.request.Request(server_url() + "/status", headers=auth_headers())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


SIDE_WIDTH_LIMIT = 0.85   # 측면 실루엣 폭 / 정면 실루엣 폭. 실측(2026-09-19): 정상 시트 0.28~0.62,
                          # 칸마다 자세가 다른 시트 0.98~0.99. 옆에서 본 몸은 정면보다 뚜렷이 얇다.


def silhouette_extent(pixels, width: int, height: int) -> tuple:
    """배경(흰색)이 아닌 픽셀의 가로·세로 범위를 0~1 비율로. 내용이 없으면 (0, 0).

    bpy 픽셀(RGBA float, 좌하단 원점)을 그대로 받는다."""
    m = CELL_MARGIN if width > 4 * CELL_MARGIN and height > 4 * CELL_MARGIN else 0
    min_x, max_x, min_y, max_y = width, -1, height, -1
    for y in range(m, height - m):
        base = y * width
        for x in range(m, width - m):
            i = (base + x) * 4
            if pixels[i + 3] > .1 and min(pixels[i], pixels[i + 1], pixels[i + 2]) < DARK:
                if x < min_x: min_x = x
                if x > max_x: max_x = x
                if y < min_y: min_y = y
                if y > max_y: max_y = y
    if max_x < 0:
        return 0.0, 0.0
    return (max_x - min_x + 1) / width, (max_y - min_y + 1) / height


def pose_mismatch(views: dict) -> float:
    """측면 실루엣이 정면만큼 넓으면(자세가 다르거나 무기를 앞으로 내밀었으면) 그 비율을 돌려준다. 정상이면 0.

    칸마다 자세가 다른 턴어라운드는 4방향을 합칠 때 다리가 늘어나고 무기가 공중에 뜬 별도 덩어리가 된다 —
    셰이프 비용을 쓰기 전에 걸러낸다."""
    import bpy
    widths = {}
    for name in ('front', 'left', 'right'):
        path = views.get(name)
        if not path:
            continue
        image = bpy.data.images.load(path, check_existing=False)
        try:
            widths[name] = silhouette_extent(list(image.pixels), *image.size)[0]
        finally:
            bpy.data.images.remove(image)
    front = widths.get('front', 0.0)
    if front <= 0.0:
        return 0.0
    sides = [w for k, w in widths.items() if k != 'front']
    worst = max((w / front for w in sides), default=0.0)
    return worst if worst > SIDE_WIDTH_LIMIT else 0.0


def split_turnaround(sheet_path: str, out_dir: str) -> dict:
    """턴어라운드 시트를 칸별 PNG로 잘라 {view: path}를 돌려준다 (bpy 이미지 API, PIL 불필요).

    칸 경계는 격자선을 찾아 정하고(등분 가정 안 함), 각 칸의 라벨 글자·격자 잔재는 흰색으로 덧칠한다."""
    import bpy
    os.makedirs(out_dir, exist_ok=True)
    img = bpy.data.images.load(sheet_path)
    try:
        w, h = img.size
        px = list(img.pixels)
        rows, cols = _darkness(px, w, h)
        xs = [0] + grid_cuts(cols, 3) + [w]
        ys = [0] + grid_cuts(rows, 2) + [h]      # 좌하단 원점: ys[0..1] 이 아래 칸, ys[1..2] 가 위 칸
        out = {}
        for name, (cx, cy) in TURNAROUND_CELLS.items():
            x0, x1 = xs[cx], xs[cx + 1]
            y0, y1 = (ys[1], ys[2]) if cy == 0 else (ys[0], ys[1])
            cw, ch = x1 - x0, y1 - y0
            buf = []
            for y in range(y0, y1):
                row = (y * w + x0) * 4
                buf.extend(px[row:row + cw * 4])
            clean_cell(buf, cw, ch)
            cell = bpy.data.images.new("lp3d_cell_" + name, cw, ch, alpha=True)
            try:
                cell.pixels = buf
                path = os.path.join(out_dir, "view_%s.png" % name)
                cell.filepath_raw = path
                cell.file_format = 'PNG'
                cell.save()
                out[name] = path
            finally:
                bpy.data.images.remove(cell)
        return out
    finally:
        bpy.data.images.remove(img)


def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


CRUST_FACE_FACTOR = 2   # 서버가 쓰는 공식 내보내기(듀얼 컨투어링 리메시)는 바깥·안쪽 두 겹을 만든다.
                        # 임포트에서 안쪽 겹을 지우면 절반 가까이 사라지므로(실측 59,053 → 26,493면),
                        # 사용자가 지정한 목표 면수를 맞추려면 서버에는 두 배로 요청해야 한다.


def build_body(views: dict, octree: int = 1024, steps: int = 12, guidance: float = 7.5,
               face_count: int = 0, seed: int = 7, multiview: bool = False,
               texture: bool = False, texture_size: int = 2048) -> dict:
    """셰이프 요청 본문. 기본값은 TRELLIS.2 표준(12스텝·guidance 7.5·1024 캐스케이드) —
    이전 Hunyuan 기본(30스텝·5.0·256)은 TRELLIS 에서 시간·비용만 2.5배 들고 품질 이득이 없다.

    multiview=True 는 실험용이다: TRELLIS.2 는 공식적으로 이미지 1장만 받으며, 여러 뷰의 토큰을 이어붙이면
    모델이 학습한 적 없는 입력이 되어 형상이 뭉개진다(같은 시트 비교: 4뷰 → 셸 4개·무기 분리, 정면 1장 → 셸 1개)."""
    body = {"octree_resolution": int(octree), "num_inference_steps": int(steps),
            "guidance_scale": float(guidance), "face_count": int(face_count), "seed": int(seed),
            "texture": bool(texture), "texture_size": int(texture_size), "type": "glb"}
    # 기본은 정면 1장 — TRELLIS.2 는 단일 이미지 전용이라 여러 뷰를 함께 넣으면 서로 뭉개진다(실측 2026-09-19)
    names = SEND_VIEWS if multiview else ("front",)
    for name in names:
        path = views.get(name)
        if path and os.path.isfile(path):
            body[name] = _b64(path)
    if "front" not in body:
        raise ValueError("정면(front) 뷰가 없다")
    if multiview:
        body["multiview"] = True
    return body


def _worker_module():
    """http_worker 를 가져온다. 패키지 밖(단위 테스트)에서 상대 임포트가 안 되면 파일로 로드한다(sys.modules 는 건드리지 않음)."""
    try:
        from . import http_worker
        return http_worker
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_lp3d_http_worker", os.path.join(os.path.dirname(os.path.abspath(__spec__.origin)), "http_worker.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def build_spec(views: dict, out_path: str, timeout: int = 900, **params):
    """HTTP 워커용 요청 spec — POST /generate, 202 면 /result/{job_id} 폴링(Modal 웹 요청 150초 한계).

    (spec, None) 또는 (None, 오류 문자열)."""
    try:
        body = build_body(views, **params)
    except (ValueError, OSError) as e:
        return None, "셰이프 요청 구성 실패: %s" % e
    body_file = out_path + ".request.json"
    try:
        with open(body_file, "w", encoding="utf-8") as f:
            json.dump(body, f)
    except OSError as e:
        return None, "셰이프 요청 본문 저장 실패: %s" % e
    headers = {"Content-Type": "application/json", **auth_headers()}
    return {"url": server_url() + "/generate", "method": "POST", "body_file": body_file,
            "out_file": out_path + ".response.bin", "work_dir": os.path.dirname(os.path.abspath(out_path)),
            "timeout": timeout, "headers": headers,
            "poll": {"url_prefix": server_url() + "/result/", "job_id_field": "job_id", "pending_status": 202,
                     "interval": 3.0, "timeout": timeout, "headers": auth_headers(), "request_timeout": 60}}, None


def handle_result(result: dict, spec: dict, out_path: str):
    """워커 결과 → GLB 저장. (경로, None) 또는 (None, 오류 문자열)."""
    if not result:
        return None, "셰이프 서버 응답 없음"
    kind = result.get("kind")
    if kind == "http":
        code = int(result.get("status") or 0)
        if code in (401, 403):
            return None, "셰이프 서버 인증 실패(%d): 환경설정의 셰이프 서버 토큰을 확인하세요" % code
        return None, "셰이프 서버 오류 %d: %s" % (code, result.get("detail", "")[:300])
    if kind == "url":
        return None, "셰이프 서버에 연결할 수 없습니다 (%s): %s" % (server_url(), result.get("reason", ""))
    if kind == "timeout":
        return None, "셰이프 생성 대기 시간 초과: %s" % result.get("reason", "")
    if kind:
        return None, "셰이프 요청 실패: %s" % result.get("reason", "")
    try:
        with open(spec["out_file"], "rb") as f:
            data = f.read()
    except OSError as e:
        return None, "셰이프 응답 읽기 실패: %s" % e
    if not data or data[:4] != b"glTF":
        return None, "셰이프 서버가 GLB가 아닌 응답을 보냈습니다"
    with open(out_path, "wb") as f:
        f.write(data)
    for temp in (spec.get("body_file"), spec.get("out_file")):
        try:
            if temp and os.path.exists(temp):
                os.remove(temp)
        except OSError:
            pass
    return out_path, None


def request_shape(views: dict, out_path: str, timeout: int = 900, **params):
    """셰이프를 생성해 out_path(.glb)에 저장한다 (블로킹 — 스모크·테스트용, 애드온 안에서는 generate 를 쓴다).

    (경로, None) 또는 (None, 오류 문자열)."""
    http_worker = _worker_module()
    spec, error = build_spec(views, out_path, timeout=timeout, **params)
    if spec is None:
        return None, error
    return handle_result(http_worker.perform(spec), spec, out_path)


def generate(views: dict, out_path: str, timeout: int, on_done, job_key=None, **params):
    """비동기 셰이프 생성 — 완료 시 메인 스레드에서 on_done(경로|None, 오류|None)."""
    from . import runner
    spec, error = build_spec(views, out_path, timeout=timeout, **params)
    if spec is None:
        runner._finish_now(on_done, None, error)
        return
    runner.run_http_async(spec, lambda result, err=None: on_done(*(handle_result(result, spec, out_path) if not err else (None, err))),
                          job_key=job_key)
