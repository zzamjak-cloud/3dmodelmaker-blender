# Hunyuan3D-2 로컬 셰이프 서버 설치 (Windows, NVIDIA GPU 8GB+ 권장)
#
# 하는 일:
#   1) Tencent/Hunyuan3D-2 리포를 $InstallDir 에 클론
#   2) uv 로 Python 3.11 가상환경을 만들고 PyTorch(CUDA 12.6) + 셰이프 생성 의존성 설치
#      (텍스처 파이프라인의 C++/CUDA 확장은 빌드하지 않는다 — 텍스처는 애드온이 6면도 베이크로 담당)
#   3) 멀티뷰 서버(lp3d_h3d_server.py)와 런처(run_server.bat)를 복사
#
# 사용: .\scripts\hunyuan3d\setup.ps1            # 기본 D:\Tools\Hunyuan3D-2
#       .\scripts\hunyuan3d\setup.ps1 -InstallDir E:\Hunyuan3D-2
# 설치 후: <InstallDir>\run_server.bat  (첫 실행 때 모델 ~3GB 다운로드)
param(
    [string]$InstallDir = "D:\Tools\Hunyuan3D-2"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv 가 없습니다. https://docs.astral.sh/uv/ 에서 설치한 뒤 다시 실행하세요."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git 이 없습니다."
}

if (-not (Test-Path (Join-Path $InstallDir "api_server.py"))) {
    Write-Host "[1/3] Hunyuan3D-2 클론 -> $InstallDir"
    git clone --depth 1 https://github.com/Tencent/Hunyuan3D-2.git $InstallDir
} else {
    Write-Host "[1/3] 리포가 이미 있습니다: $InstallDir"
}

$py = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[2/3] Python 3.11 가상환경 + 의존성 설치 (수 분 소요)"
    uv venv --python 3.11 (Join-Path $InstallDir ".venv")
    uv pip install --python $py torch torchvision --index-url https://download.pytorch.org/whl/cu126
    uv pip install --python $py diffusers einops opencv-python numpy transformers omegaconf tqdm `
        trimesh pymeshlab pygltflib xatlas accelerate fastapi uvicorn rembg onnxruntime ninja pybind11 huggingface_hub
} else {
    Write-Host "[2/3] 가상환경이 이미 있습니다"
}

Write-Host "[3/3] 서버 스크립트 복사"
Copy-Item (Join-Path $here "lp3d_h3d_server.py") $InstallDir -Force
Copy-Item (Join-Path $here "run_server.bat") $InstallDir -Force

& $py -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
Write-Host ""
Write-Host "완료. 서버 실행: $InstallDir\run_server.bat  (포트 8081, 첫 실행 시 tencent/Hunyuan3D-2mv 모델 다운로드)"
Write-Host "애드온 환경설정 > 캐릭터 > 셰이프 서버 주소 가 http://127.0.0.1:8081 인지 확인하세요."
