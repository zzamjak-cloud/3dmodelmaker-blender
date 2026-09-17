# 이미지→3D 셰이프 생성 클라이언트 (셰이프 서버 HTTP)
#
# 캐릭터를 프리미티브 코드로 조립하면 원화와 닮지 않는다. 턴어라운드 시트의 정면·뒷면·측면을
# 이미지→3D 모델(TRELLIS.2)에 넣어 "찰흙으로 빚은" 하이폴리 셰이프를 받고, 그 메시를
# 리토폴로지(lowpoly/retopo.py)해서 게임용 메시로 만든다. 텍스처는 기존 6면도 베이크가 맡는다.
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
LABEL_TRIM = 0.12  # 각 칸 상단의 뷰 라벨 텍스트를 잘라낸다 — 셰이프에 글자가 튀어나온다


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


def is_available(timeout: float = 1.5) -> bool:
    """서버가 떠 있는가. 없으면 기존 코드 모델링 경로로 조용히 폴백한다."""
    if not is_enabled():
        return False
    try:
        req = urllib.request.Request(server_url() + "/status", headers=auth_headers())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def parts_support_error(timeout: float = 1.5) -> str:
    """이미지 생성 비용을 쓰기 전에 서버의 분리 부품 보존 기능을 확인한다."""
    if not is_enabled():
        return '환경설정에서 셰이프 생성을 켜세요'
    try:
        req = urllib.request.Request(server_url() + '/status', headers=auth_headers())
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        # 401/403은 서버가 살아 있고 토큰이 틀린 것 — 연결 문제로 오해하지 않게 따로 안내한다
        if exc.code in (401, 403):
            return '셰이프 서버 인증 실패: 환경설정의 셰이프 서버 토큰을 확인하세요'
        return f'셰이프 서버 오류 {exc.code}'
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return f'셰이프 서버에 연결할 수 없습니다: {exc}'
    if not isinstance(status, dict) or not isinstance(status.get('capabilities'), dict) or not status['capabilities'].get('preserve_parts'):
        return ('현재 셰이프 서버가 부품 보존을 지원하지 않습니다. '
                '저장소 scripts/trellis3d/ 의 최신 서버로 업데이트하고 재배포하세요')
    return ''


def split_turnaround(sheet_path: str, out_dir: str) -> dict:
    """턴어라운드 시트를 칸별 PNG로 잘라 {view: path}를 돌려준다 (bpy 이미지 API, PIL 불필요).

    라벨 텍스트가 있는 상단 12%는 잘라낸다."""
    import bpy
    os.makedirs(out_dir, exist_ok=True)
    img = bpy.data.images.load(sheet_path)
    try:
        w, h = img.size
        cw, ch = w // 3, h // 2
        px = list(img.pixels)
        out = {}
        trim = int(ch * LABEL_TRIM)
        keep_h = ch - trim
        for name, (cx, cy) in TURNAROUND_CELLS.items():
            cell = bpy.data.images.new("lp3d_cell_" + name, cw, keep_h, alpha=True)
            try:
                y0 = h - (cy + 1) * ch  # bpy pixels는 좌하단 원점
                buf = []
                for y in range(keep_h):
                    row = ((y0 + y) * w + cx * cw) * 4
                    buf.extend(px[row:row + cw * 4])
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


def build_body(views: dict, octree: int = 256, steps: int = 30, guidance: float = 5.0,
               face_count: int = 0, seed: int = 7, preserve_parts: bool = False) -> dict:
    body = {"octree_resolution": int(octree), "num_inference_steps": int(steps),
            "guidance_scale": float(guidance), "face_count": int(face_count), "seed": int(seed),
            "texture": False, "type": "glb"}
    if preserve_parts:
        body['preserve_parts'] = True
    for name in SEND_VIEWS:
        path = views.get(name)
        if path and os.path.isfile(path):
            body[name] = _b64(path)
    if "front" not in body:
        raise ValueError("정면(front) 뷰가 없다")
    return body


def _poll_result(job: dict, timeout: int, interval: float = 3.0) -> bytes:
    """202 응답의 job_id 로 GET /result/{id} 를 폴링한다. 202 는 진행 중, 200 은 GLB 바이트.

    서버 오류(500)는 HTTPError 로 올라가 호출자가 본문을 읽어 안내한다."""
    import time
    job_id = job.get("job_id")
    if not job_id:
        raise ValueError("셰이프 서버가 job_id 없이 202를 보냈습니다")
    url = server_url() + "/result/" + urllib.parse.quote(str(job_id))
    deadline = time.monotonic() + timeout
    while True:
        req = urllib.request.Request(url, headers=auth_headers())
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            if resp.status == 200:
                return body
        if time.monotonic() > deadline:
            raise OSError("셰이프 생성 대기 시간 초과 (%ds)" % timeout)
        time.sleep(interval)


def request_shape(views: dict, out_path: str, timeout: int = 900, **params):
    """셰이프를 생성해 out_path(.glb)에 저장한다 (블로킹 — 워커 스레드에서 호출).

    (경로, None) 또는 (None, 오류 문자열)."""
    try:
        body = build_body(views, **params)
    except (ValueError, OSError) as e:
        return None, "셰이프 요청 구성 실패: %s" % e
    req = urllib.request.Request(server_url() + "/generate", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **auth_headers()},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            # 클라우드 서버는 웹 요청 한계(Modal 150초) 때문에 202 + job_id 를 주고 결과를 폴링시킨다.
            # 로컬 서버처럼 200 GLB 를 바로 주면 이 분기를 타지 않는다.
            if resp.status == 202:
                data = _poll_result(json.loads(data.decode("utf-8")), timeout)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if e.code in (401, 403):
            return None, "셰이프 서버 인증 실패(%d): 환경설정의 셰이프 서버 토큰을 확인하세요" % e.code
        return None, "셰이프 서버 오류 %d: %s" % (e.code, detail)
    except urllib.error.URLError as e:
        return None, "셰이프 서버에 연결할 수 없습니다 (%s): %s" % (server_url(), e.reason)
    except OSError as e:
        return None, "셰이프 요청 실패: %s" % e
    if not data or data[:4] != b"glTF":
        return None, "셰이프 서버가 GLB가 아닌 응답을 보냈습니다"
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path, None


def generate(views: dict, out_path: str, timeout: int, on_done, job_key=None, **params):
    """비동기 셰이프 생성 — 완료 시 메인 스레드에서 on_done(경로|None, 오류|None)."""
    from . import runner
    runner.run_http_async(lambda: request_shape(views, out_path, timeout=timeout, **params),
                          on_done, job_key=job_key)
