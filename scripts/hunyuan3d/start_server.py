"""설치된 Hunyuan 환경에서 저장소의 최신 서버를 실행한다."""
import argparse
import os
from pathlib import Path
import runpy
import sys

parser = argparse.ArgumentParser(description=__doc__, add_help=False)
parser.add_argument('--hunyuan-root', required=True)
options, server_args = parser.parse_known_args()
source = Path(options.hunyuan_root).resolve()
if not (source / 'hy3dgen').is_dir():
    raise SystemExit('Hunyuan 소스의 hy3dgen 폴더를 찾을 수 없습니다: ' + str(source))
sys.path.insert(0, str(source))
# 설치된 의존성 폴더에 캐시를 쓰지 않아 읽기 전용 설치 환경에서도 시작할 수 있다.
cache = Path(__file__).resolve().parents[2] / 'Generate' / 'hunyuan-cache'
os.environ.setdefault('NUMBA_CACHE_DIR', str(cache))
Path(os.environ['NUMBA_CACHE_DIR']).mkdir(parents=True, exist_ok=True)
server = Path(__file__).with_name('lp3d_h3d_server.py')
sys.argv = [str(server)] + server_args
runpy.run_path(str(server), run_name='__main__')
