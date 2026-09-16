# 씬 컨셉 시트 생성: codex CLI의 image_gen 도구를 헤드리스로 호출 (배경 모드 전용)
#
# 배경 공간은 정면/측면/상면 3면도가 의미가 없다 — 필요한 것은 "어디에 무엇이 있는가"다.
# 그래서 멀티뷰 대신 한 장에 좌=아이소메트릭 조감도, 우=탑다운 레이아웃 맵을 그려
# 플랜 턴과 배치 턴의 공간 기준으로 쓴다. 실행 방식(임시 디렉토리, 명령 구성, 보관 폴더)은
# core/multiview.py와 동일하므로 그대로 재사용한다.
import logging
import os
import shutil
import tempfile

log = logging.getLogger(__name__)

SCENEVIEW_FILENAME = "sceneview.png"
ARCHIVE_PREFIX = "LP3D_sceneview_"  # 보관 폴더에 남는 컨셉 시트 파일명 접두어


def is_available() -> bool:
    from . import multiview
    return multiview.is_available()


def build_command(exe: str, work_dir: str, ref_image: str = None) -> list:
    """codex exec 명령 구성은 멀티뷰와 동일하다."""
    from . import multiview
    return multiview.build_command(exe, work_dir, ref_image=ref_image)


def _body(request: str, scene_size: str = "M", has_ref: bool = False,
          style_note: str = "") -> str:
    """백엔드 공용 지시 본문 — 시트 구성·규모·스타일."""
    from . import scene_plan
    size = str(scene_size or "M").strip().upper()
    meters = scene_plan.SCENE_SIZE_M.get(size, scene_plan.SCENE_SIZE_M["M"])
    base = (
        "대상: %s\n"
        "규모: 한 변 약 %dm의 정사각형 부지.\n"
        "구성: 좌우 2분할 — 좌 절반은 아이소메트릭 3/4 조감도(공간 전체의 분위기), "
        "우 절반은 탑다운 레이아웃 맵(구역 경계와 동선을 단순 도형으로 그리고 구역 이름을 라벨로 표기).\n"
        % (request, int(meters))
    )
    base += (style_note or
             "스타일: 로우폴리 캐주얼 게임 배경, 플랫 셰이딩, 단순한 색 팔레트, 흰 배경.\n")
    base += "랜드마크 1~2개를 크게 세우고 중앙에 빈 공간을 남겨라.\n"
    if has_ref:
        base += "첨부한 참조 이미지와 동일한 분위기·색·특징을 유지하라.\n"
    return base


def build_prompt(request: str, scene_size: str = "M", has_ref: bool = False,
                 style_note: str = "") -> str:
    return (
        "image_gen 도구를 사용해 3D 배경(레벨) 설계용 컨셉 시트 이미지 1장을 생성하고, "
        "반드시 현재 디렉토리에 %s 파일로 저장하라.\n" % SCENEVIEW_FILENAME
        + _body(request, scene_size, has_ref, style_note)
        + "저장 완료 후 텍스트로는 SAVED 한 단어만 답하라.")


def build_image_prompt(request: str, scene_size: str = "M", has_ref: bool = False,
                       style_note: str = "") -> str:
    """OpenRouter Image API용 — 파일 저장·도구 호출 지시가 필요 없다."""
    return ("3D 배경(레벨) 설계용 컨셉 시트 이미지 1장을 생성하라.\n"
            + _body(request, scene_size, has_ref, style_note))


def archive(src: str, request: str = "") -> str:
    """생성한 컨셉 시트를 보관 폴더(멀티뷰와 공유)에 남긴다. 저장 경로 또는 빈 문자열."""
    from . import multiview
    directory = multiview.archive_dir()
    if not directory or not src or not os.path.isfile(src):
        return ""
    try:
        dest = multiview.unique_path(directory, ARCHIVE_PREFIX + multiview._slug(request))
        shutil.copy(src, dest)
        return dest
    except OSError:
        log.exception("씬 컨셉 시트 보관 실패")
        return ""


def latest_archived() -> str:
    """가장 최근 컨셉 시트 경로 (없으면 빈 문자열) — 보관 폴더와 예전 .blend 옆까지 훑는다."""
    from . import multiview
    best, best_mtime = "", -1.0
    for directory in dict.fromkeys(d for d in (multiview.archive_dir(), multiview.blend_dir()) if d):
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


def generate(request: str, scene_size: str, session_workdir: str, timeout: int, on_done,
             ref_image: str = None, job_key=None, style_note: str = ""):
    """씬 컨셉 시트를 비동기로 생성한다.

    완료 시 메인 스레드에서 on_done(경로 or None, 오류 문자열 or None)을 호출한다.
    실패는 비치명이다 — 시트 없이 플랜 턴을 진행할 수 있어야 한다.
    job_key는 잡 단위 취소용 식별자다 (넘기지 않으면 세션 취소 후에도 codex가 살아남는다)."""
    from .. import preferences
    from . import imagegen, multiview, runner

    final_path = os.path.join(session_workdir, SCENEVIEW_FILENAME)
    if multiview.use_openrouter():
        # 좌우 2분할 시트라 가로 비율(3:2)이 필요하다 — 1:1로 뽑으면 두 패널이 다 눌린다
        imagegen.generate(
            build_image_prompt(request, scene_size, has_ref=bool(ref_image),
                               style_note=style_note),
            final_path, timeout, on_done,
            refs=[ref_image] if ref_image else None, aspect_ratio="3:2", job_key=job_key)
        return

    exe = preferences.resolve_cli_path('CODEX')
    if not exe:
        on_done(None, "codex CLI를 찾을 수 없습니다")
        return
    work = tempfile.mkdtemp(prefix="lp3d_sv_")
    out_path = os.path.join(work, SCENEVIEW_FILENAME)

    def _cb(stdout, error):
        path, failure = None, error
        if os.path.isfile(out_path):
            try:
                shutil.move(out_path, final_path)
                path, failure = final_path, None
            except OSError as e:
                log.exception("씬 컨셉 시트 이동 실패")
                failure = "시트 파일 이동 실패: %s" % e
        elif not error:
            # 종료 코드는 0인데 파일이 없다 — image_gen을 안 썼거나 다른 이름으로 저장
            failure = "codex가 %s를 저장하지 않았습니다" % SCENEVIEW_FILENAME
        if failure:
            log.warning("씬 컨셉 시트 생성 실패: %s", failure.splitlines()[0])
        shutil.rmtree(work, ignore_errors=True)
        on_done(path, failure)

    cmd = build_command(exe, work, ref_image=ref_image)
    runner.run_cli_async(cmd, work, timeout, _cb,
                         stdin_text=build_prompt(request, scene_size,
                                                 has_ref=bool(ref_image),
                                                 style_note=style_note),
                         job_key=job_key)
