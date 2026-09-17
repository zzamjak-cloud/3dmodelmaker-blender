@echo off
REM AI LowPoly ModelMaker용 Hunyuan3D 로컬 셰이프 서버 (멀티뷰, 포트 8081)
REM  - lp3d_h3d_server.py: 턴어라운드 시트에서 잘라낸 front/back/left/right 를 함께 받는 Hunyuan3D-2mv 서버
REM  - Blender MCP의 Hunyuan3D LOCAL_API 모드(단일 image)와도 호환된다
REM  - 텍스처는 켜지 않는다 (커스텀 래스터라이저 컴파일 필요). 텍스처는 애드온의 6면도 베이크가 담당
cd /d %~dp0
set HF_HUB_DISABLE_SYMLINKS_WARNING=1
.venv\Scripts\python.exe lp3d_h3d_server.py --host 127.0.0.1 --port 8081 --model_path tencent/Hunyuan3D-2mv --subfolder hunyuan3d-dit-v2-mv-turbo
