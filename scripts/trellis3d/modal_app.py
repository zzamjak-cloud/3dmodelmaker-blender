# TRELLIS.2 셰이프 전용 서버 — 각 사용자가 자기 Modal 계정에 배포한다
#
#   modal deploy scripts/trellis3d/modal_app.py
#
# 저장소에는 이 코드만 있고 서버 주소·토큰은 없다. 배포 출력의 https://…modal.run 주소와
# Modal 대시보드(Settings → Proxy Auth Tokens)에서 만든 토큰을 애드온 환경설정에 넣는다.
# 웹 엔드포인트는 requires_proxy_auth 로 잠겨 있어 토큰 없는 요청은 GPU 컨테이너가 뜨기 전에 거절된다.
#
# 라이선스 메모: TRELLIS.2·CuMesh·FlexGEMM·o-voxel 은 MIT. 비상업 라이선스인 nvdiffrast/nvdiffrec(렌더·텍스처 전용)과
# 배경제거 모델 RMBG-2.0(CC BY-NC)은 설치·로드하지 않는다 — 텍스처는 애드온이, 배경 알파는 server_core 가 만든다.
import os
from pathlib import Path

import modal

APP_NAME = "lp3d-trellis2-shape"
VOLUME_NAME = "trellis2-weights"          # download_weights.py 가 채운다
WEIGHTS = "/weights"
GPU = os.environ.get("LP3D_SHAPE_GPU", "L40S")   # 48GB — 공식 요구 VRAM ≥24GB
TRELLIS_COMMIT = os.environ.get("LP3D_TRELLIS_COMMIT", "main")

app = modal.App(APP_NAME)
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
CORE = Path(__file__).with_name("server_core.py")
RASTER = Path(__file__).with_name("uv_raster.py")

# ---- GPU 이미지: CUDA 12.4 devel + torch 2.6.0 + TRELLIS.2 셰이프 의존성(커스텀 CUDA 확장 3종 소스 빌드) ----
gpu_image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.10")
    .apt_install("git", "build-essential", "ninja-build", "libgl1", "libglib2.0-0", "libegl1")
    .env({
        "CUDA_HOME": "/usr/local/cuda",
        "TORCH_CUDA_ARCH_LIST": "8.0;8.6;8.9;9.0",     # A100 / A10 / L40S / H100
        "MAX_JOBS": "8",
    })
    .pip_install("torch==2.6.0", "torchvision==0.21.0",
                 index_url="https://download.pytorch.org/whl/cu124")
    # PyPI 배포판이 torch 2.6.0 + CUDA 12.4 빌드 — flash-attn 소스 빌드(수십 분)를 피한다
    .pip_install("xformers==0.0.29.post3")
    .pip_install(
        # setup.sh --basic 중 추론에 필요한 것 (gradio/tensorboard/pandas/lpips 제외)
        "imageio", "imageio-ffmpeg", "tqdm", "easydict", "opencv-python-headless", "ninja", "trimesh",
        "transformers>=4.57", "kornia", "timm", "pillow", "safetensors", "huggingface_hub>=0.34",
        "fast-simplification", "numpy<2.3",
        "git+https://github.com/EasternJournalist/utils3d.git@9a4eb15e4021b67b12c460c7057d642626897ec8",
    )
    # --no-build-isolation 빌드는 빌드 도구가 환경에 미리 있어야 한다 (없으면 'invalid command bdist_wheel')
    .pip_install("wheel", "setuptools>=68", "packaging", "pybind11", "cmake", "scikit-build-core")
    # 베이스 이미지가 CXX=clang++ 를 물려주는데 torch 는 g++ 로 빌드됐다 — CUDA 확장은 반드시 gcc/g++ 로 컴파일
    .env({"CC": "gcc", "CXX": "g++", "CUDAHOSTCXX": "g++"})
    .run_commands(
        f"git clone --recursive https://github.com/microsoft/TRELLIS.2.git /opt/trellis2 && cd /opt/trellis2 && git checkout {TRELLIS_COMMIT}",
        "mkdir -p /tmp/extensions"
        " && git clone --recursive https://github.com/JeffreyXiang/CuMesh.git /tmp/extensions/CuMesh"
        " && git clone --recursive https://github.com/JeffreyXiang/FlexGEMM.git /tmp/extensions/FlexGEMM",
        "pip install --no-build-isolation /tmp/extensions/FlexGEMM",
        "pip install --no-build-isolation /tmp/extensions/CuMesh",
        "pip install --no-build-isolation /opt/trellis2/o-voxel",
        # nvdiffrast 대체 — o_voxel.postprocess 가 상단에서 import 하고 UV 굽기의 rasterize/interpolate 두 함수만 쓴다.
        # NVIDIA 코드(비상업 라이선스) 대신 우리 토치 구현(uv_raster.py)을 같은 이름으로 얹는다.
        "mkdir -p /opt/stubs/nvdiffrast && printf '%s\\n' "
        "'\"\"\"nvdiffrast 이름 자리 — 실제 구현은 uv_raster(토치).\"\"\"' "
        "> /opt/stubs/nvdiffrast/__init__.py && printf '%s\\n' "
        "'from uv_raster import *  # noqa: F401,F403' "
        "> /opt/stubs/nvdiffrast/torch.py",
    )
    # TRELLIS.2 의 DinoV3FeatureExtractor 는 transformers 4.5x 의 DINOv3ViTModel 내부 구조(`.layer`)를 전제한다 —
    # 5.x 에서는 `'DINOv3ViTModel' object has no attribute 'layer'` (실측). 확장 빌드 레이어 뒤에 별도 단계로 핀을 걸어 캐시를 지킨다.
    .pip_install("transformers>=4.56,<5")
    .env({
        "PYTHONPATH": "/opt/trellis2:/opt/stubs",
        "ATTN_BACKEND": "xformers",
        "SPARSE_ATTN_BACKEND": "xformers",
        "SPARSE_CONV_BACKEND": "flex_gemm",
        "HF_HOME": f"{WEIGHTS}/hf",
        "HF_HUB_OFFLINE": "1",              # 스테이징된 가중치만 사용 — 컨테이너에서 새 다운로드 금지
        "TOKENIZERS_PARALLELISM": "false",
    })
    .add_local_file(CORE, "/root/server_core.py")
    .add_local_file(RASTER, "/opt/stubs/uv_raster.py")
)

# 셰이프 경로에 필요한 모델만 — 텍스처 모델은 로드하지 않는다
SHAPE_MODELS = [
    "sparse_structure_flow_model", "sparse_structure_decoder",
    "shape_slat_flow_model_512", "shape_slat_flow_model_1024", "shape_slat_decoder",
]
# PBR 텍스처까지 굽는 공식 경로에 필요한 모델 (1024 캐스케이드 기준)
TEXTURE_MODELS = SHAPE_MODELS + ["tex_slat_flow_model_1024", "tex_slat_decoder"]
# 요청에 face_count 가 없을 때 서버가 적용하는 면수 상한 — 애드온은 이 뒤에 복셀 리메시(90분할)·QuadriFlow 로 12k 까지 내린다
SERVER_FACE_CAP = int(os.environ.get("LP3D_SHAPE_FACE_CAP", "400000"))
# PBR 경로 기본값 — to_glb 의 데시메이트 목표(정점 수)와 텍스처 한 변
DEFAULT_DECIMATION = int(os.environ.get("LP3D_DECIMATION", "60000"))
DEFAULT_TEXTURE_SIZE = int(os.environ.get("LP3D_TEXTURE_SIZE", "2048"))


@app.cls(image=gpu_image, gpu=GPU, volumes={WEIGHTS: volume}, timeout=1200,
         scaledown_window=600, max_containers=1)
class ShapeWorker:
    @modal.enter()
    def load(self):
        # 가중치가 없으면 모델을 올리기 전에 즉시 실패한다 — 기동 실패는 Modal 이 새 컨테이너로 재시도하므로
        # 느리게 실패하면 콜드스타트 비용이 반복된다 (실측: 평면 레이아웃으로 받아둔 볼륨에서 이 사고가 났다)
        hub = os.path.join(WEIGHTS, "hf", "hub")
        required = ["models--microsoft--TRELLIS.2-4B", "models--microsoft--TRELLIS-image-large",
                    "models--facebook--dinov3-vitl16-pretrain-lvd1689m"]
        missing = [r for r in required if not os.path.isdir(os.path.join(hub, r))]
        if missing:
            raise RuntimeError(f"볼륨 {VOLUME_NAME} 에 허브 캐시 가중치가 없습니다: {missing} — "
                               f"먼저 `modal run scripts/trellis3d/download_weights.py` 를 실행하세요")
        import torch
        from trellis2.pipelines import samplers
        from trellis2.pipelines.trellis2_image_to_3d import Trellis2ImageTo3DPipeline
        from trellis2.modules import image_feature_extractor

        class TrellisServerPipeline(Trellis2ImageTo3DPipeline):
            model_names_to_load = TEXTURE_MODELS

            @classmethod
            def from_pretrained(cls, path, config_file="pipeline.json"):
                # 공식 from_pretrained 와 같되 텍스처 샘플러·배경제거 모델(RMBG-2.0, CC BY-NC)을 만들지 않는다
                pipeline = super(Trellis2ImageTo3DPipeline, cls).from_pretrained(path, config_file)
                args = pipeline._pretrained_args
                for key in ("sparse_structure_sampler", "shape_slat_sampler", "tex_slat_sampler"):
                    if key not in args:
                        continue
                    setattr(pipeline, key, getattr(samplers, args[key]["name"])(**args[key]["args"]))
                    setattr(pipeline, key + "_params", args[key]["params"])
                pipeline.shape_slat_normalization = args["shape_slat_normalization"]
                pipeline.tex_slat_normalization = args.get("tex_slat_normalization")
                # 공식 파이프라인이 코드에 고정해 둔 PBR 채널 배치 (pipeline.json 에는 없다)
                pipeline.pbr_attr_layout = {"base_color": slice(0, 3), "metallic": slice(3, 4),
                                            "roughness": slice(4, 5), "alpha": slice(5, 6)}
                pipeline.image_cond_model = getattr(image_feature_extractor, args["image_cond_model"]["name"])(
                    **args["image_cond_model"]["args"])
                pipeline.rembg_model = None
                pipeline.low_vram = False          # 48GB — 모델을 GPU 에 상주시켜 재로드 비용을 없앤다
                pipeline.default_pipeline_type = args.get("default_pipeline_type", "1024_cascade")
                pipeline._device = "cpu"
                return pipeline

        self.pipe = TrellisServerPipeline.from_pretrained("microsoft/TRELLIS.2-4B")
        self.pipe.cuda()
        self.pipe.image_cond_model.to("cuda") if hasattr(self.pipe.image_cond_model, "to") else None
        self.torch = torch
        print("파이프라인 로드 완료(셰이프+PBR 텍스처):", GPU, flush=True)

    def _cond(self, images, resolution):
        """조건화. 이미지 1장이면 공식 get_cond 와 같다.

        실측(2026-09-19): TRELLIS.2 공식 파이프라인은 `run(image)` — 단일 이미지 전용이고 멀티뷰 API 가 없다.
        여러 뷰의 DINOv3 토큰을 한 시퀀스로 이어붙이는 것은 모델이 학습한 적 없는 입력이라, 뷰가 조금만
        어긋나도 서로 뭉개진 형상이 나온다(같은 시트로 비교: 4뷰 → 셸 4개·도끼 분리·깊이가 키만큼,
        정면 1장 → 셸 1개·정상 비율). 그래서 기본은 정면 1장이고, 멀티뷰는 실험용으로만 남긴다."""
        torch = self.torch
        self.pipe.image_cond_model.image_size = resolution
        feats = self.pipe.image_cond_model(images)             # (V, N, D)
        cond = feats.reshape(1, -1, feats.shape[-1])
        return {"cond": cond, "neg_cond": torch.zeros_like(cond)}

    @staticmethod
    def _mesh_stats(vertices, faces) -> str:
        """면수·(용접 후) 열린 엣지·부피비 — 껍데기는 부피비 0 근처, 찢김은 열린 엣지로 드러난다.

        UV 언랩이 섬마다 정점을 쪼개 두므로 같은 좌표를 먼저 합쳐야 경계 수가 실제와 맞는다."""
        import numpy as np
        v = np.asarray(vertices, dtype=np.float64)
        f = np.asarray(faces, dtype=np.int64)
        tri = v[f]
        volume = float(np.einsum('ij,ij->i', tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() / 6.0)
        box = float(np.prod(v.max(axis=0) - v.min(axis=0)))
        _uniq, inverse = np.unique(np.round(v, 6), axis=0, return_inverse=True)
        welded = inverse[f]
        edges = np.sort(np.concatenate([welded[:, [0, 1]], welded[:, [1, 2]], welded[:, [2, 0]]]), axis=1)
        _u, counts = np.unique(edges, axis=0, return_counts=True)
        return (f"면 {len(f):,} 정점 {len(_uniq):,} 열린엣지 {int((counts == 1).sum()):,} "
                f"부피비 {volume / box:+.4f}")

    def _textured_glb(self, mesh, cfg: dict) -> bytes:
        """공식 o_voxel.postprocess.to_glb 로 PBR 텍스처까지 구운 GLB 바이트.

        to_glb 는 정리·듀얼컨투어 리메시·데시메이트·UV 언랩·PBR 굽기를 한 번에 한다(cumesh·flex_gemm, MIT).
        내부에서 UV 래스터화에만 nvdiffrast 를 쓰는데, 이미지에 우리 토치 구현(uv_raster)을 그 이름으로 얹어 뒀다."""
        import cumesh
        import o_voxel
        target = cfg["face_count"] if cfg["face_count"] > 0 else DEFAULT_DECIMATION
        vertices, faces = mesh.vertices, mesh.faces
        if cfg.get("variants"):   # 700만 면 통계는 몇 초 걸린다 — 진단 요청에서만 낸다
            print("원본 등위면:", self._mesh_stats(vertices.cpu().numpy(), faces.cpu().numpy()), flush=True)
        glb = o_voxel.postprocess.to_glb(
            vertices=vertices,
            faces=faces,
            attr_volume=mesh.attrs,
            coords=mesh.coords,
            attr_layout=mesh.layout,
            voxel_size=mesh.voxel_size,
            aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
            decimation_target=int(target),
            texture_size=int(cfg.get("texture_size") or DEFAULT_TEXTURE_SIZE),
            # 공식 데모와 같은 설정으로 되돌렸다(2026-09-19). remesh=False 는 속이 찬 단일 표면을 주지만
            # 원본 등위면의 찢김(7.47M면에 열린 엣지 46,046)이 그대로 드러나고, 그걸 메우면 입·눈처럼
            # 뚫려 있어야 할 곳까지 막히고 메운 면의 방향이 어긋난다(사용자 실측 보고). 듀얼 컨투어링
            # 리메시는 그 찢김을 감싸 워터타이트하고 방향이 일관된 표면을 준다 — 대신 안쪽 면이 함께 남는다.
            remesh=True, remesh_band=1, remesh_project=0,
            verbose=False,   # xatlas 진행 막대가 로그를 덮어 진단 출력이 묻힌다
        )
        print("내보내기 결과:", self._mesh_stats(glb.vertices, glb.faces), flush=True)
        if cfg.get("variants"):
            import cumesh
            filled = {}
            for limit in (0.15, 10.0):
                clean = cumesh.CuMesh()
                clean.init(mesh.vertices, mesh.faces)
                clean.fill_holes(max_hole_perimeter=limit)
                clean.repair_non_manifold_edges()
                clean.fill_holes(max_hole_perimeter=limit)
                filled[limit] = clean.read()
                print(f"입력 구멍 메우기 {limit}:",
                      self._mesh_stats(filled[limit][0].cpu().numpy(), filled[limit][1].cpu().numpy()),
                      flush=True)
            for label, kw, src in (("E 메우기10 + 리메시", dict(remesh=True, remesh_band=1, remesh_project=0), filled[10.0]),
                                   ("F 메우기0.15 + 리메시", dict(remesh=True, remesh_band=1, remesh_project=0), filled[0.15]),
                                   ("G 메우기10 + 리메시 project0.9", dict(remesh=True, remesh_band=1, remesh_project=0.9), filled[10.0])):
                try:
                    other = o_voxel.postprocess.to_glb(
                        vertices=src[0], faces=src[1], attr_volume=mesh.attrs,
                        coords=mesh.coords, attr_layout=mesh.layout, voxel_size=mesh.voxel_size,
                        aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
                        decimation_target=int(target),
                        texture_size=int(cfg.get("texture_size") or DEFAULT_TEXTURE_SIZE),
                        **kw)
                    print(f"변형 {label}:", self._mesh_stats(other.vertices, other.faces), flush=True)
                except Exception as e:
                    print(f"변형 {label} 실패: {type(e).__name__} {e}", flush=True)
        # to_glb 는 trimesh.Trimesh 를 돌려주고 glTF 축 변환과 UV V 뒤집기까지 이미 끝내 둔다
        return glb.export(file_type="glb")

    @modal.method()
    def generate(self, params: dict) -> bytes:
        import sys
        sys.path.insert(0, "/root")
        import numpy as np
        import trimesh
        import server_core as sc

        torch = self.torch
        pipe = self.pipe
        views = sc.collect_views(params)                      # 라벨 제거·흰 여백 → RGB
        cfg = sc.map_params(params)
        # 기본은 정면 1장(공식 경로). multiview=true 를 명시한 요청만 여러 뷰를 이어붙인다.
        if params.get("multiview"):
            order = [k for k in ("front", "back", "left", "right") if k in views]
        else:
            order = ["front"] if "front" in views else list(views)[:1]
        # 공식 preprocess_image 는 알파가 있으면 배경제거 없이 알파 bbox 크롭·알파 곱을 한다
        images = [pipe.preprocess_image(sc.rgb_to_rgba_white(views[k])) for k in order]

        with torch.no_grad():
            torch.manual_seed(cfg["seed"])
            ss_params = {"steps": cfg["steps"], "guidance_strength": cfg["guidance_scale"]}
            slat_params = {"steps": cfg["steps"], "guidance_strength": cfg["guidance_scale"]}
            cond_512 = self._cond(images, 512)
            coords = pipe.sample_sparse_structure(cond_512, 32, 1, ss_params)
            if cfg["cascade"]:
                cond_1024 = self._cond(images, 1024)
                slat, res = pipe.sample_shape_slat_cascade(
                    cond_512, cond_1024,
                    pipe.models["shape_slat_flow_model_512"], pipe.models["shape_slat_flow_model_1024"],
                    512, 1024, coords, slat_params, 49152)
            else:
                slat = pipe.sample_shape_slat(cond_512, pipe.models["shape_slat_flow_model_512"], coords, slat_params)
                res = 512
            want_texture = bool(params.get("texture"))
            if want_texture and "tex_slat_flow_model_1024" in pipe.models:
                cond_tex = cond_1024 if cfg["cascade"] else self._cond(images, 1024)
                tex_slat = pipe.sample_tex_slat(cond_tex, pipe.models["tex_slat_flow_model_1024"], slat,
                                                {"steps": cfg["steps"], "guidance_strength": 1.0})
                out_mesh = pipe.decode_latent(slat, tex_slat, res)[0]
                torch.cuda.empty_cache()
                return self._textured_glb(out_mesh, cfg)
            meshes, _subs = pipe.decode_shape_slat(slat, res)
            torch.cuda.empty_cache()

        m = meshes[0]
        m.fill_holes()
        # 원시 듀얼그리드 메시는 수백만~천만 페이스다. 공식 Mesh.simplify(cumesh)로 GPU 에서 먼저 줄인다 —
        # 요청 face_count 가 있으면 그 값, 없으면 서버 상한(전송·리토폴로지에 충분한 밀도).
        target = cfg["face_count"] if cfg["face_count"] > 0 else SERVER_FACE_CAP
        try:
            if hasattr(m, "simplify") and len(m.faces) > target:
                m.simplify(int(target))
        except Exception as e:  # 단순화 실패는 치명적이지 않다 — 후처리의 FaceReducer 가 다시 시도한다
            print("Mesh.simplify 실패, 후처리로 넘김:", e, flush=True)
        v = m.vertices.detach().cpu().numpy() if hasattr(m.vertices, "detach") else np.asarray(m.vertices)
        f = m.faces.detach().cpu().numpy() if hasattr(m.faces, "detach") else np.asarray(m.faces)
        mesh = trimesh.Trimesh(vertices=v.astype(np.float32), faces=f.astype(np.int64), process=False)
        mesh = sc._postprocess_mesh(mesh, params)             # 파편 제거(부품 보존 시 생략) · 퇴화 면 · 목표 면수
        mesh = trimesh.Trimesh(vertices=sc.to_gltf_frame(mesh.vertices), faces=mesh.faces, process=False)
        return sc.export_glb(mesh)


# ---- 웹 계층: 가벼운 CPU 컨테이너. /status 는 생성 중에도 즉시 응답한다 ----
web_image = (modal.Image.debian_slim(python_version="3.11")
             .pip_install("fastapi[standard]>=0.115")
             .add_local_file(CORE, "/root/server_core.py")
             .add_local_file(RASTER, "/opt/stubs/uv_raster.py"))


@app.function(image=web_image, scaledown_window=300)
@modal.asgi_app(requires_proxy_auth=True)
def web():
    import sys
    sys.path.insert(0, "/root")
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response

    api = FastAPI()

    @api.get("/status")
    async def status():
        # server_core 는 numpy/PIL 을 import 하므로 여기서는 계약 페이로드만 직접 만든다
        return {"ok": True, "backend": "trellis2", "gpu": GPU, "multiview": True,
                "capabilities": {"preserve_parts": True, "async_jobs": True, "pbr_texture": True}}

    @api.post("/generate")
    async def generate(request: Request):
        params = await request.json()
        if not any(params.get(k) for k in ("front", "back", "left", "right", "image")):
            return JSONResponse({"error": "front/back/left/right 또는 image 중 하나는 있어야 한다"}, status_code=400)
        # 웹 요청 한계(150초) 때문에 GPU 작업은 분리해 띄우고 job_id 를 돌려준다 — 클라이언트가 /result 를 폴링
        call = await ShapeWorker().generate.spawn.aio(params)
        return JSONResponse({"job_id": call.object_id, "poll": f"/result/{call.object_id}"}, status_code=202)

    @api.get("/result/{job_id}")
    async def result(job_id: str):
        call = modal.FunctionCall.from_id(job_id)
        try:
            glb = await call.get.aio(timeout=0)     # 비동기 라우트에서 이벤트 루프를 막지 않게 .aio
        except TimeoutError:
            return JSONResponse({"job_id": job_id, "state": "running"}, status_code=202)
        except Exception as e:  # 생성 실패 원인을 본문으로 — 애드온이 읽어 패널에 표시한다
            return JSONResponse({"error": str(e)[:500]}, status_code=500)
        return Response(content=glb, media_type="model/gltf-binary",
                        headers={"Content-Disposition": "attachment; filename=shape.glb"})

    return api
