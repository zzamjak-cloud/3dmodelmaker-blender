# 텍스처 6면도 생성: 가이드 시트 위에 손맵 디테일을 입힌다 (OpenRouter 또는 codex image_gen)
#
# codex 경로는 multiview.py와 같은 방식 — 세션 workdir의 AGENTS.md(코드 출력 강제)와 충돌하므로
# 반드시 별도 임시 디렉토리에서 실행하고 결과 파일만 세션 workdir로 옮긴다.
import logging
import os
import shutil
import tempfile

from ..texturing import layout

log = logging.getLogger(__name__)

SHEET_FILENAME = "texture_sheet.png"
VIEW_FILENAME = "texture_view_%s.png"


def is_available() -> bool:
    from . import multiview
    return multiview.is_available()


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
             job_key=None, reference: str = None, per_view: bool = False,
             has_face: bool = True, strict_views: bool = False, is_active=None):
    """6면도 텍스처를 비동기로 생성한다.

    완료 시 on_done(결과, 오류). 결과는 시트 한 장의 경로(str) 또는 per_view 모드에서
    {시점: 경로} dict다 — 베이크(bake.rasterize_to_png)는 둘 다 받는다(session이 분기).

    reference: 턴어라운드/원화 경로. 함께 넘기면 색·무늬를 거기서 가져오게 한다 —
    회색 가이드만 주면 모델이 색을 지어내고 얼굴을 비워 둔다.
    per_view: 시점마다 1:1 이미지를 따로 요청한다(6회). 시트 한 장은 칸당 512px밖에
    안 돼 뿌옇다. OpenRouter 경로에서만 지원하고 codex 경로는 시트 한 장으로 돈다."""
    from .. import preferences
    from . import imagegen, multiview, runner

    if multiview.use_openrouter():
        if per_view:
            _generate_per_view(request, guide_image, session_workdir, timeout, on_done,
                               job_key=job_key, reference=reference, has_face=has_face,
                               strict_views=strict_views, is_active=is_active)
            return
        refs = [guide_image] + ([reference] if reference else [])
        imagegen.generate(
            layout.build_image_prompt(request, has_reference=bool(reference), has_face=has_face),
            os.path.join(session_workdir, SHEET_FILENAME), timeout, on_done,
            refs=refs, aspect_ratio=layout.ASPECT_RATIO, job_key=job_key)
        return

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

    command = build_command(exe, work, guide_copy)
    prompt = build_prompt(request)
    if reference:
        reference_copy = os.path.join(work, 'color_reference' + os.path.splitext(reference)[1])
        shutil.copy(reference, reference_copy)
        command[-1:-1] = ['-i', reference_copy]
        prompt += '\n' + layout.reference_contract(has_face=has_face)
    runner.run_cli_async(command, work, timeout, _cb,
                         stdin_text=prompt, job_key=job_key)


def _generate_per_view(request: str, guide_image: str, session_workdir: str, timeout: int,
                       on_done, job_key=None, reference: str = None,
                       has_face: bool = True, strict_views: bool = False, is_active=None):
    """시점별 1:1 요청 6개를 병렬로 보내고 전부 끝나면 {시점: 경로}로 콜백한다.

    하나라도 실패하면 실패한 시점만 빼고 넘긴다 — 베이크는 빠진 시점을 이웃 투영으로
    채우므로 전부 버리는 것보다 낫다. 전부 실패하면 오류로 콜백한다."""
    from ..texturing import capture as tex_capture
    from . import imagegen

    try:
        cells = tex_capture.split_sheet(guide_image)  # {VIEW: 셀 PNG}
    except Exception as e:
        on_done(None, f"가이드 시트 분할 실패: {e}")
        return
    pending = {"left": len(cells)}
    results, errors, retried = {}, {}, set()

    def _request(view):
        if is_active is not None and not is_active():
            return
        cell = cells[view]
        out = os.path.join(session_workdir, VIEW_FILENAME % view.lower())
        refs = [cell] + ([reference] if reference else [])
        imagegen.generate(layout.build_view_prompt(request, view, has_reference=bool(reference), has_face=has_face),
                          out, timeout, _make_cb(view), refs=refs, aspect_ratio="1:1",
                          job_key=job_key)

    def _make_cb(view):
        def _cb(path, error=None):
            if is_active is not None and not is_active():
                return
            if path:
                results[view] = path
            elif view not in retried:
                # 6회 요청 중 하나가 서버 혼잡 등으로 실패하는 일이 흔하다 — 한 번 다시 시도
                retried.add(view)
                log.warning("시점 %s 텍스처 생성 실패, 재시도: %s", view, error)
                _request(view)
                return
            else:
                errors[view] = error or "원인 불명"
                log.warning("시점 %s 텍스처 생성 재시도도 실패: %s", view, errors[view])
                # 베이크는 6시점을 모두 요구한다 — 빠진 시점은 회색 가이드 칸으로 채워
                # 텍스처 전체가 실패하는 것을 막는다 (그 시점만 밋밋하게 남는다)
                results[view] = cells[view]
            pending["left"] -= 1
            if pending["left"] > 0:
                return
            if len(errors) == len(cells) or (strict_views and errors):
                on_done(None, "시점별 텍스처 생성 실패: " + "; ".join(
                    f"{v}: {str(e).splitlines()[0]}" for v, e in errors.items()))
                return
            on_done(results, None)
        return _cb

    for view in cells:
        _request(view)
