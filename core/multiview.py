# 멀티뷰 참조 시트 생성: codex CLI의 image_gen(gpt-image-2) 도구를 헤드리스로 호출
#
# 사용자가 정면/측면/상면 이미지를 직접 준비하기 어렵기 때문에, 생성 세션 시작 시
# 프롬프트(+사용자 참조 이미지)로 4분할 멀티뷰 시트를 먼저 만들어 모델링·비평의
# 기준으로 쓴다. codex CLI가 없거나 실패하면 조용히 스킵한다 (기존 흐름 유지).
#
# 주의: 세션 workdir에는 모델링용 AGENTS.md(STATUS+코드 출력 강제)가 있어서
# 그 안에서 codex exec를 돌리면 이미지 생성 지시와 충돌한다 — 반드시 별도
# 임시 디렉토리에서 실행하고, 완성된 시트만 세션 workdir로 옮긴다.
import logging
import os
import shutil
import tempfile

log = logging.getLogger(__name__)

MULTIVIEW_FILENAME = "multiview.png"


def is_available() -> bool:
    from .. import preferences
    return bool(preferences.resolve_cli_path('CODEX'))


def _slug(text: str, limit: int = 30) -> str:
    """파일명에 쓸 수 있게 정리한다 — 한글은 그대로 두고 경로 구분자만 제거."""
    keep = [c for c in (text or "").strip() if c.isalnum() or c in " _-가-힣"]
    return ("".join(keep).strip().replace(" ", "_")[:limit]) or "model"


def unique_path(directory: str, base: str) -> str:
    """directory/base.png — 이미 있으면 _001, _002로 비켜간다 (기존 참조를 덮지 않도록)."""
    path = os.path.join(directory, base + ".png")
    n = 1
    while os.path.exists(path):
        path = os.path.join(directory, f"{base}_{n:03d}.png")
        n += 1
    return path


def fallback_dir() -> str:
    """.blend를 저장하지 않았을 때 쓰는 보관 폴더 — OS 다운로드 폴더 아래 blender/.

    새 씬에서 바로 생성하는 경우가 흔한데 그때 시트를 잃지 않도록,
    어느 OS에서나 존재가 보장되는 경로에 남긴다."""
    home = os.path.expanduser("~")
    base = os.path.join(home, "Downloads")
    if not os.path.isdir(base):
        base = home  # 다운로드 폴더가 없는 환경(원격 세션 등)에서는 홈으로
    path = os.path.join(base, "blender")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        log.exception("보관 폴더 생성 실패")
        return base
    return path


def archive_dir() -> str:
    """멀티뷰 시트를 남길 폴더 — 열려 있는 .blend 옆, 저장 전이면 다운로드 폴더."""
    import bpy
    return os.path.dirname(bpy.data.filepath) if bpy.data.filepath else fallback_dir()


def archive(src: str, request: str) -> str:
    """생성한 시트를 보관 폴더에 남긴다. 저장 경로를 반환(불가하면 빈 문자열)."""
    directory = archive_dir()
    if not directory or not src or not os.path.isfile(src):
        return ""
    try:
        dest = unique_path(directory, f"LP3D_multiview_{_slug(request)}")
        shutil.copy(src, dest)
        return dest
    except OSError:
        log.exception("멀티뷰 시트 보관 실패")
        return ""


def build_command(exe: str, work_dir: str, ref_image: str = None) -> list:
    cmd = [
        exe, "exec",
        "-s", "workspace-write",   # 이미지 파일 저장이 필요하므로 쓰기 샌드박스
        "--skip-git-repo-check",
        "--cd", work_dir,
        "--json",
        "-o", os.path.join(work_dir, "last_message.txt"),
    ]
    if ref_image:
        cmd += ["-i", ref_image]
    cmd.append("-")  # 프롬프트는 stdin
    return cmd


def build_prompt(request: str, has_ref: bool = False) -> str:
    base = (
        "image_gen 도구를 사용해 3D 로우폴리 모델링용 멀티뷰 참조 시트 이미지 1장을 생성하고, "
        f"반드시 현재 디렉토리에 {MULTIVIEW_FILENAME} 파일로 저장하라.\n"
        f"대상: {request}\n"
        "구성: 2x2 그리드 — 좌상=정면(FRONT), 우상=측면(SIDE), 좌하=상면(TOP), 우하=3/4뷰. "
        "각 뷰에 라벨을 표기하고, 네 뷰 모두 동일한 대상을 일관된 비율로 그려라.\n"
        "스타일: 로우폴리 게임 에셋, 플랫 셰이딩, 단순한 색 팔레트, 흰 배경.\n"
    )
    if has_ref:
        base += "첨부한 참조 이미지와 동일한 대상·색·특징을 유지하라.\n"
    return base + "저장 완료 후 텍스트로는 SAVED 한 단어만 답하라."


def generate(request: str, session_workdir: str, timeout: int, on_done, ref_image: str = None):
    """멀티뷰 시트를 비동기로 생성한다. 완료 시 메인 스레드에서 on_done(경로 or None) 호출."""
    from .. import preferences
    from . import runner

    exe = preferences.resolve_cli_path('CODEX')
    if not exe:
        on_done(None)
        return
    work = tempfile.mkdtemp(prefix="lp3d_mv_")
    out_path = os.path.join(work, MULTIVIEW_FILENAME)
    final_path = os.path.join(session_workdir, MULTIVIEW_FILENAME)

    def _cb(stdout, error):
        path = None
        if os.path.isfile(out_path):
            try:
                shutil.move(out_path, final_path)
                path = final_path
            except OSError:
                log.exception("멀티뷰 시트 이동 실패")
        elif error:
            log.warning("멀티뷰 생성 실패: %s", error.splitlines()[0])
        shutil.rmtree(work, ignore_errors=True)
        on_done(path)

    cmd = build_command(exe, work, ref_image=ref_image)
    runner.run_cli_async(cmd, work, timeout, _cb,
                         stdin_text=build_prompt(request, has_ref=bool(ref_image)))
