# 텍스처 6면도 생성: codex CLI image_gen으로 가이드 시트 위에 손맵 디테일을 입힌다
#
# multiview.py와 같은 방식 — 세션 workdir의 AGENTS.md(코드 출력 강제)와 충돌하므로
# 반드시 별도 임시 디렉토리에서 실행하고 결과 파일만 세션 workdir로 옮긴다.
import logging
import os
import shutil
import tempfile

from ..texturing import layout

log = logging.getLogger(__name__)

SHEET_FILENAME = "texture_sheet.png"


def is_available() -> bool:
    from .. import preferences
    return bool(preferences.resolve_cli_path('CODEX'))


def build_command(exe: str, work_dir: str, guide_image: str) -> list:
    return [
        exe, "exec",
        "-s", "workspace-write",   # 이미지 파일 저장이 필요하므로 쓰기 샌드박스
        "--skip-git-repo-check",
        "--cd", work_dir,
        "--json",
        "-o", os.path.join(work_dir, "last_message.txt"),
        "-i", guide_image,
        "-",  # 프롬프트는 stdin
    ]


def build_prompt(request: str) -> str:
    return layout.build_prompt(request, SHEET_FILENAME)


def generate(request: str, guide_image: str, session_workdir: str, timeout: int, on_done,
             job_key=None):
    """6면도 텍스처 시트를 비동기로 생성한다. 완료 시 on_done(경로 or None, 오류 or None)."""
    from .. import preferences
    from . import runner

    exe = preferences.resolve_cli_path('CODEX')
    if not exe:
        on_done(None, "codex CLI를 찾을 수 없습니다")
        return
    work = tempfile.mkdtemp(prefix="lp3d_tex_")
    # 가이드는 임시 디렉토리로 복사 — 샌드박스 밖 경로 읽기 제약을 피한다
    guide_copy = os.path.join(work, "guide_sheet.png")
    shutil.copy(guide_image, guide_copy)
    out_path = os.path.join(work, SHEET_FILENAME)
    final_path = os.path.join(session_workdir, SHEET_FILENAME)

    def _cb(stdout, error):
        path, failure = None, error
        if os.path.isfile(out_path):
            try:
                shutil.move(out_path, final_path)
                path, failure = final_path, None
            except OSError as e:
                log.exception("텍스처 시트 이동 실패")
                failure = f"시트 파일 이동 실패: {e}"
        elif not error:
            failure = f"codex가 {SHEET_FILENAME}을 저장하지 않았습니다"
        if failure:
            log.warning("텍스처 시트 생성 실패: %s", failure.splitlines()[0])
        shutil.rmtree(work, ignore_errors=True)
        on_done(path, failure)

    runner.run_cli_async(build_command(exe, work, guide_copy), work, timeout, _cb,
                         stdin_text=build_prompt(request), job_key=job_key)
