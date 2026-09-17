# 셰이프 경로 가중치를 Modal Volume에 1회 내려받는다 (CPU 컨테이너, GPU 비용 없음)
#
# 사용:  modal run scripts/trellis3d/download_weights.py
# 전제:  modal secret create huggingface-secret HF_TOKEN=<자기 HF 토큰>  (DINOv3는 게이트 모델 — HF에서 접근 승인 필요)
# 텍스처 전용 체크포인트(imgshape2tex, tex_dec)는 받지 않는다 — 텍스처는 애드온의 6면도 베이크가 맡는다.
import modal

VOLUME_NAME = "trellis2-weights"
MOUNT = "/weights"

app = modal.App("lp3d-trellis2-weights")
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
image = modal.Image.debian_slim(python_version="3.11").pip_install("huggingface_hub[hf_transfer]>=0.34")

# 저장소별 받을 파일 패턴 — 셰이프 생성에 필요한 것만
DOWNLOADS = (
    ("microsoft/TRELLIS.2-4B", [
        "*.json",
        "ckpts/ss_flow_img_dit_1_3B_64_bf16.*",
        "ckpts/slat_flow_img2shape_dit_1_3B_512_bf16.*",
        "ckpts/slat_flow_img2shape_dit_1_3B_1024_bf16.*",
        "ckpts/shape_dec_next_dc_f16c32_fp16.*",
        "ckpts/shape_enc_next_dc_f16c32_fp16.*",
    ]),
    ("microsoft/TRELLIS-image-large", ["*.json", "ckpts/ss_dec_conv3d_16l8_fp16.*"]),
    ("facebook/dinov3-vitl16-pretrain-lvd1689m", None),   # 전체(1.1GB)
)


@app.function(image=image, volumes={MOUNT: volume},
              secrets=[modal.Secret.from_name("huggingface-secret")], timeout=3600)
def download():
    import os
    # 허브 캐시 레이아웃(models--org--repo/snapshots/…)으로 받는다 — 서버가 HF_HOME을 이 볼륨으로 잡으면
    # from_pretrained가 재다운로드 없이 그대로 읽는다
    os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
    os.environ["HF_HOME"] = os.path.join(MOUNT, "hf")
    from huggingface_hub import snapshot_download
    for repo, patterns in DOWNLOADS:
        print(f"→ {repo}", flush=True)
        snapshot_download(repo_id=repo, allow_patterns=patterns, token=os.environ["HF_TOKEN"])
    volume.commit()
    total = 0
    for root, _, files in os.walk(MOUNT):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    print(f"완료: 볼륨 총 {total / 2**30:.2f} GB", flush=True)
    return total


@app.local_entrypoint()
def main():
    size = download.remote()
    print(f"가중치 볼륨 '{VOLUME_NAME}' 준비됨 ({size / 2**30:.2f} GB)")
