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
ARCHIVE_PREFIX = "LP3D_multiview_"  # 보관 폴더에 남는 시트 파일명 접두어


def is_available() -> bool:
    from .. import preferences
    from . import imagegen
    return bool(imagegen.is_available() or preferences.resolve_cli_path('CODEX'))


def backend_label(enabled: bool = True) -> str:
    """지금 참조 시트를 무엇으로 만드는지 한 줄 — 패널·상태줄·로그가 같은 문구를 쓴다.

    예전에는 설정이 OpenRouter여도 키가 없으면 조용히 codex로 폴백해서, 사용자가
    실제로 어느 경로가 쓰이는지 알 길이 없었다."""
    from .. import preferences
    from . import imagegen
    if not enabled:
        return "미사용 (멀티뷰 참조 생성 꺼짐)"
    prefs = preferences.get_prefs()
    wants_or = getattr(prefs, "image_backend", 'OPENROUTER') == 'OPENROUTER'
    if wants_or and imagegen.is_available():
        model = imagegen.model_def(getattr(prefs, "image_model", imagegen.DEFAULT_MODEL))
        return "OpenRouter · %s" % model["label"]
    codex = preferences.resolve_cli_path('CODEX')
    if wants_or:
        return ("Codex image_gen (OpenRouter 키 없음 → 폴백)" if codex
                else "미사용 (OpenRouter 키 없음, codex도 없음)")
    return "Codex image_gen" if codex else "미사용 (codex CLI 없음)"


def use_openrouter() -> bool:
    """OpenRouter 경로를 쓸지 — 백엔드 설정이 OPENROUTER이고 키가 있을 때만.

    키가 없으면 조용히 codex로 폴백한다. 세 시트 모듈이 같은 판단을 공유해야
    한 세션 안에서 백엔드가 섞이지 않는다."""
    from .. import preferences
    from . import imagegen
    prefs = preferences.get_prefs()
    return (getattr(prefs, "image_backend", 'OPENROUTER') == 'OPENROUTER'
            and imagegen.is_available())


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


def archive_dir() -> str:
    """멀티뷰 시트와 클립보드 참조 이미지를 남길 폴더 — 항상 다운로드 폴더 아래 blender/.

    예전에는 .blend를 저장했으면 그 옆, 아니면 다운로드 폴더로 갈렸다. 그래서 파일이
    매번 다른 곳에 생겨 찾기 불편했다. 어느 OS에서나 존재가 보장되는 한 곳으로 고정한다."""
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


def blend_dir() -> str:
    """열려 있는 .blend 파일이 있는 폴더 (저장 전이면 빈 문자열).

    보관 위치를 고정하기 전(v0.6.x 이하)에는 여기에 시트를 남겼다 —
    조회할 때만 함께 훑어 예전 파일을 잃지 않게 한다."""
    import bpy
    return os.path.dirname(bpy.data.filepath) if bpy.data.filepath else ""


def archive(src: str, request: str) -> str:
    """생성한 시트를 보관 폴더에 남긴다. 저장 경로를 반환(불가하면 빈 문자열)."""
    directory = archive_dir()
    if not directory or not src or not os.path.isfile(src):
        return ""
    try:
        dest = unique_path(directory, f"{ARCHIVE_PREFIX}{_slug(request)}")
        shutil.copy(src, dest)
        return dest
    except OSError:
        log.exception("멀티뷰 시트 보관 실패")
        return ""


def latest_in(directories) -> str:
    """주어진 폴더들에서 가장 최근에 저장된 멀티뷰 시트 경로 (없으면 빈 문자열)."""
    best, best_mtime = "", -1.0
    for directory in dict.fromkeys(d for d in directories if d):
        if not os.path.isdir(directory):
            continue
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if not name.startswith(ARCHIVE_PREFIX) or not name.lower().endswith(".png"):
                continue
            path = os.path.join(directory, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime > best_mtime:
                best, best_mtime = path, mtime
    return best


def latest_archived() -> str:
    """가장 최근 시트를 찾는다 — 고정 보관 폴더와, 예전에 쓰던 .blend 옆까지 훑는다.

    세션 상태(multiview_path)는 Blender를 다시 켜면 사라지지만 파일은 남는다 —
    지난 세션에서 만든 시트를 다시 열어볼 수 있도록 파일 쪽에서 되찾는다."""
    return latest_in([archive_dir(), blend_dir()])


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


# 시트 종류별 구성 — 프랍은 2x2 멀티뷰, 캐릭터는 3x2 턴어라운드(6면도)
SHEET_LAYOUT = {
    "MULTIVIEW": {
        "aspect": "1:1",
        "title": "3D 모델링용 멀티뷰 참조 시트",
        "grid": ("구성: 2x2 그리드 — 좌상=정면(FRONT), 우상=측면(SIDE), 좌하=상면(TOP), 우하=3/4뷰. "
                 "각 뷰에 라벨을 표기하고, 네 뷰 모두 동일한 대상을 일관된 비율로 그려라.\n"),
        "ref": "첨부한 참조 이미지와 동일한 대상·색·특징을 유지하라.\n",
    },
    "TURNAROUND": {
        "aspect": "3:2",
        "title": "3D 캐릭터 모델링용 턴어라운드(6면도) 시트",
        "grid": ("구성: 3x2 그리드 — 윗줄 왼쪽부터 정면(FRONT) | 뒷면(BACK) | 좌측면(LEFT), "
                 "아랫줄 왼쪽부터 우측면(RIGHT) | 상면(TOP) | 3/4뷰. 각 칸에 라벨을 표기한다.\n"
                 "자세: 리깅용 중립 자세 — 인간형은 A-포즈(팔을 몸에서 30~40도 내림, 다리 살짝 벌림), "
                 "네발 동물·크리처는 네 발로 자연스럽게 선 자세, 날개가 있으면 절반 펼침. "
                 "표정은 중립, 모든 칸이 같은 자세·같은 축척·같은 발 높이여야 한다.\n"
                 "얼굴·손·발·장비의 디테일이 정면과 측면에서 모두 읽히게 그려라. 그림자·바닥·배경 소품 금지.\n"),
        "ref": ("첨부한 원화의 캐릭터를 **그대로** 6면도로 전개하라 — 얼굴 생김새·머리 모양·의상·장비·"
                "색 배치·비율을 바꾸지 마라. 원화에 없는 요소를 추가하지 마라. 원화가 동적인 포즈라면 "
                "위의 중립 자세로 펴되 디자인은 유지한다.\n"),
    },
}


def sheet_aspect(sheet: str = "MULTIVIEW") -> str:
    return SHEET_LAYOUT.get(sheet, SHEET_LAYOUT["MULTIVIEW"])["aspect"]


def _body(request: str, has_ref: bool = False, style_note: str = "",
          sheet: str = "MULTIVIEW") -> str:
    """백엔드 공용 지시 본문 — 시트 구성·스타일."""
    layout = SHEET_LAYOUT.get(sheet, SHEET_LAYOUT["MULTIVIEW"])
    base = (
        f"대상: {request}\n"
        + layout["grid"]
        + (style_note or "스타일: 로우폴리 게임 에셋, 플랫 셰이딩, 단순한 색 팔레트, 흰 배경.\n")
    )
    if has_ref:
        base += layout["ref"]
    return base


def build_prompt(request: str, has_ref: bool = False, style_note: str = "",
                 sheet: str = "MULTIVIEW") -> str:
    title = SHEET_LAYOUT.get(sheet, SHEET_LAYOUT["MULTIVIEW"])["title"]
    return (
        f"image_gen 도구를 사용해 {title} 이미지 1장을 생성하고, "
        f"반드시 현재 디렉토리에 {MULTIVIEW_FILENAME} 파일로 저장하라.\n"
        + _body(request, has_ref, style_note, sheet)
        + "저장 완료 후 텍스트로는 SAVED 한 단어만 답하라.")


def build_image_prompt(request: str, has_ref: bool = False, style_note: str = "",
                       sheet: str = "MULTIVIEW") -> str:
    """OpenRouter Image API용 — 파일 저장·도구 호출 지시가 필요 없다."""
    title = SHEET_LAYOUT.get(sheet, SHEET_LAYOUT["MULTIVIEW"])["title"]
    return (f"{title} 이미지 1장을 생성하라.\n"
            + _body(request, has_ref, style_note, sheet))


def generate(request: str, session_workdir: str, timeout: int, on_done, ref_image: str = None,
             job_key=None, style_note: str = "", sheet: str = "MULTIVIEW",
             prompt_override: str = None):
    """멀티뷰 시트를 비동기로 생성한다.

    완료 시 메인 스레드에서 on_done(경로 or None, 오류 문자열 or None)을 호출한다.
    실패 원인을 함께 넘기는 이유: 예전에는 실패를 '스킵'으로만 알려서
    codex 로그인 만료 같은 조치 가능한 원인이 그대로 묻혔다.

    job_key는 잡 단위 취소용 식별자다 — 이걸 넘기지 않으면 세션을 취소해도
    codex 프로세스가 살아남아 AI 동시 실행 한도를 초과한 채로 돈다."""
    from .. import preferences
    from . import imagegen, runner

    final_path = os.path.join(session_workdir, MULTIVIEW_FILENAME)
    if use_openrouter():
        # OpenRouter는 결과를 바로 최종 경로에 쓴다 — 임시 디렉토리도, AGENTS.md 충돌도 없다
        imagegen.generate(
            prompt_override or build_image_prompt(request, has_ref=bool(ref_image), style_note=style_note,
                               sheet=sheet),
            final_path, timeout, on_done,
            refs=[ref_image] if ref_image else None, aspect_ratio=sheet_aspect(sheet),
            job_key=job_key)
        return

    exe = preferences.resolve_cli_path('CODEX')
    if not exe:
        on_done(None, "codex CLI를 찾을 수 없습니다")
        return
    work = tempfile.mkdtemp(prefix="lp3d_mv_")
    out_path = os.path.join(work, MULTIVIEW_FILENAME)

    def _cb(stdout, error):
        path, failure = None, error
        if os.path.isfile(out_path):
            try:
                shutil.move(out_path, final_path)
                path, failure = final_path, None
            except OSError as e:
                log.exception("멀티뷰 시트 이동 실패")
                failure = f"시트 파일 이동 실패: {e}"
        elif not error:
            # 종료 코드는 0인데 파일이 없다 — image_gen 도구를 안 썼거나 다른 이름으로 저장
            failure = f"codex가 {MULTIVIEW_FILENAME}을 저장하지 않았습니다"
        if failure:
            log.warning("멀티뷰 생성 실패: %s", failure.splitlines()[0])
        shutil.rmtree(work, ignore_errors=True)
        on_done(path, failure)

    cmd = build_command(exe, work, ref_image=ref_image)
    custom_cli_prompt = (f'image_gen 도구로 이미지 1장을 생성하고 현재 디렉토리에 '
                         f'{MULTIVIEW_FILENAME} 파일로 저장하라.\n{prompt_override}\n'
                         '완료 후 SAVED 한 단어만 답하라.') if prompt_override else None
    runner.run_cli_async(cmd, work, timeout, _cb,
                         stdin_text=custom_cli_prompt or build_prompt(request, has_ref=bool(ref_image),
                                                 style_note=style_note, sheet=sheet),
                         job_key=job_key)
