# 이미지→3D 셰이프 생성 클라이언트 (로컬 Hunyuan3D 서버)
#
# 캐릭터를 프리미티브 코드로 조립하면 원화와 닮지 않는다. 턴어라운드 시트의 정면·뒷면·측면을
# 이미지→3D 모델(Hunyuan3D-2mv)에 넣어 "찰흙으로 빚은" 하이폴리 셰이프를 받고, 그 메시를
# 리토폴로지(lowpoly/retopo.py)해서 게임용 메시로 만든다. 텍스처는 기존 6면도 베이크가 맡는다.
#
# 서버는 D:/Tools/Hunyuan3D-2/lp3d_h3d_server.py (POST /generate, base64 뷰 → GLB).
# Blender MCP의 Hunyuan LOCAL_API 모드와 같은 주소·형식을 쓴다.
import base64
import json
import logging
import os
import urllib.error
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


def is_enabled() -> bool:
    from .. import preferences
    return bool(getattr(preferences.get_prefs(), "use_shapegen", True))


def is_available(timeout: float = 1.5) -> bool:
    """서버가 떠 있는가. 없으면 기존 코드 모델링 경로로 조용히 폴백한다."""
    if not is_enabled():
        return False
    try:
        with urllib.request.urlopen(server_url() + "/status", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


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
               face_count: int = 0, seed: int = 7) -> dict:
    body = {"octree_resolution": int(octree), "num_inference_steps": int(steps),
            "guidance_scale": float(guidance), "face_count": int(face_count), "seed": int(seed),
            "texture": False, "type": "glb"}
    for name in SEND_VIEWS:
        path = views.get(name)
        if path and os.path.isfile(path):
            body[name] = _b64(path)
    if "front" not in body:
        raise ValueError("정면(front) 뷰가 없다")
    return body


def request_shape(views: dict, out_path: str, timeout: int = 900, **params):
    """셰이프를 생성해 out_path(.glb)에 저장한다 (블로킹 — 워커 스레드에서 호출).

    (경로, None) 또는 (None, 오류 문자열)."""
    try:
        body = build_body(views, **params)
    except (ValueError, OSError) as e:
        return None, "셰이프 요청 구성 실패: %s" % e
    req = urllib.request.Request(server_url() + "/generate", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
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
