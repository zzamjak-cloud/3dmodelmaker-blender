# AI LowPoly ModelMaker용 Hunyuan3D 로컬 셰이프 서버 (멀티뷰 지원)
#
# 원본 api_server.py와의 차이:
#   1) 멀티뷰 입력 — {"front","back","left","right"} base64 를 받으면 Hunyuan3D-2mv 파이프라인에
#      뷰 딕셔너리로 넘긴다 (원본은 단일 "image"만). 턴어라운드 시트에서 잘라낸 3~4면을 그대로 쓴다.
#   2) 후처리 항상 적용 — FloaterRemover / DegenerateFaceRemover / FaceReducer. 원본은 texture=True일
#      때만 돌려서 떠다니는 조각이 그대로 남았다.
#   3) 요청 형식은 원본과 호환 — Blender MCP의 LOCAL_API 모드(단일 "image")도 그대로 동작한다.
#
# 실행: .venv\Scripts\python.exe lp3d_h3d_server.py --port 8081
import argparse
import base64
import logging
import os
import tempfile
import threading
import uuid
from io import BytesIO

import torch
import trimesh
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image

from hy3dgen.rembg import BackgroundRemover
from hy3dgen.shapegen import (DegenerateFaceRemover, FaceReducer, FloaterRemover,
                              Hunyuan3DDiTFlowMatchingPipeline)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("lp3d_h3d")

SAVE_DIR = os.path.join(tempfile.gettempdir(), "lp3d_h3d")
os.makedirs(SAVE_DIR, exist_ok=True)
VIEW_KEYS = ("front", "back", "left", "right")


def _b64_image(data: str) -> Image.Image:
    return Image.open(BytesIO(base64.b64decode(data))).convert("RGBA")


class Worker:
    def __init__(self, model_path: str, subfolder: str, device: str, flashvdm: bool):
        log.info("모델 로드: %s / %s", model_path, subfolder)
        self.multiview = "mv" in subfolder
        self.rembg = BackgroundRemover()
        self.pipeline = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
            model_path, subfolder=subfolder, use_safetensors=True, device=device)
        if flashvdm:
            self.pipeline.enable_flashvdm(mc_algo="mc")
        self.device = device
        self.lock = threading.Lock()  # GPU 한 번에 하나

    def _prepare_images(self, params: dict):
        """요청에서 이미지를 꺼내 배경을 제거한다. 멀티뷰 모델이면 dict, 아니면 단일 이미지."""
        views = {k: _b64_image(params[k]) for k in VIEW_KEYS if params.get(k)}
        if not views and params.get("image"):
            views = {"front": _b64_image(params["image"])}
        if not views:
            raise ValueError("front/back/left/right 또는 image 중 하나는 있어야 한다")
        views = {k: self.rembg(v) for k, v in views.items()}
        if self.multiview:
            return views
        return views.get("front") or next(iter(views.values()))

    @torch.inference_mode()
    def generate(self, params: dict) -> str:
        image = self._prepare_images(params)
        seed = int(params.get("seed", 1234))
        kwargs = dict(
            image=image,
            generator=torch.Generator(self.device).manual_seed(seed),
            octree_resolution=int(params.get("octree_resolution", 256)),
            num_inference_steps=int(params.get("num_inference_steps", 30)),
            guidance_scale=float(params.get("guidance_scale", 5.0)),
            mc_algo="mc",
        )
        with self.lock:
            mesh = self.pipeline(**kwargs)[0]
            # 원본 서버는 texture=True일 때만 후처리했다 — 떠다니는 조각이 그대로 남는 원인
            mesh = FloaterRemover()(mesh)
            mesh = DegenerateFaceRemover()(mesh)
            face_count = int(params.get("face_count", 0) or 0)
            if face_count > 0:
                mesh = FaceReducer()(mesh, max_facenum=face_count)
            torch.cuda.empty_cache()
        path = os.path.join(SAVE_DIR, "%s.glb" % uuid.uuid4().hex)
        mesh.export(path)
        return path


app = FastAPI()
worker: Worker = None


@app.get("/status")
async def status():
    return {"ok": True, "multiview": worker.multiview if worker else None}


@app.post("/generate")
async def generate(request: Request):
    params = await request.json()
    try:
        # 생성은 수십 초 블로킹이다 — 이벤트 루프를 막으면 /status 가 응답하지 않아
        # 클라이언트가 서버를 "없음"으로 판단한다. 스레드풀에서 돌린다.
        import asyncio
        path = await asyncio.get_running_loop().run_in_executor(None, worker.generate, params)
    except Exception as e:  # 원인을 클라이언트가 읽을 수 있게 본문으로 돌려준다
        log.exception("생성 실패")
        return JSONResponse({"error": str(e)}, status_code=500)
    return FileResponse(path, media_type="model/gltf-binary", filename=os.path.basename(path))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--model_path", default="tencent/Hunyuan3D-2mv")
    ap.add_argument("--subfolder", default="hunyuan3d-dit-v2-mv-turbo")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--no_flashvdm", action="store_true")
    args = ap.parse_args()
    worker = Worker(args.model_path, args.subfolder, args.device, not args.no_flashvdm)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
