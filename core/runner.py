# 비동기 CLI 실행: subprocess.Popen(논블로킹) + bpy.app.timers 폴링
#
# 스레드를 쓰지 않는다 — Popen은 논블로킹이고, 완료 여부는 타이머에서 poll()로
# 확인한다. stdout/stderr는 파이프 버퍼 블로킹을 피하기 위해 파일로 리다이렉트한다.
# 모든 bpy 호출과 콜백은 메인 스레드(타이머 콜백)에서 실행된다.
import logging
import os
import subprocess
import time

import bpy

from . import errors

log = logging.getLogger(__name__)

_jobs = []  # 진행 중인 작업 목록 (동시 1개가 일반적이지만 리스트로 안전하게)
_state = {"keepalive": False, "pump_on": False, "last_redraw": 0.0}
_PUMP_INTERVAL = 0.25
_REDRAW_INTERVAL = 1.0  # 경과 시간 표시 갱신 주기
_job_counter = 0


def run_cli_async(cmd: list, cwd: str, timeout: int, on_done, stdin_text: str = None):
    """CLI를 논블로킹으로 실행하고 완료 시 메인 스레드에서 on_done(stdout, error)를 호출한다.

    stdin_text가 주어지면 파일로 저장해 stdin으로 넘긴다. 프롬프트를 명령줄 인자로
    넘기면 Windows의 .cmd 셸림(npm 설치본)이 첫 줄에서 잘라버리기 때문이다."""
    global _job_counter
    _job_counter += 1
    out_path = os.path.join(cwd, f"cli_stdout_{_job_counter}.log")
    err_path = os.path.join(cwd, f"cli_stderr_{_job_counter}.log")
    job = {
        "cmd": cmd, "on_done": on_done, "cancelled": False,
        "out_path": out_path, "err_path": err_path,
        "deadline": time.monotonic() + timeout, "timeout": timeout,
    }
    try:
        job["out_file"] = open(out_path, "w", encoding="utf-8")
        job["err_file"] = open(err_path, "w", encoding="utf-8")
        if stdin_text is None:
            stdin = subprocess.DEVNULL  # CLI가 stdin을 기다리지 않도록
        else:
            in_path = os.path.join(cwd, f"cli_stdin_{_job_counter}.txt")
            with open(in_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(stdin_text)
            job["in_file"] = stdin = open(in_path, "rb")
        job["proc"] = subprocess.Popen(
            cmd, cwd=cwd,
            stdin=stdin,
            stdout=job["out_file"], stderr=job["err_file"],
        )
    except FileNotFoundError:
        _close_job_files(job)
        _finish_now(on_done, None, f"실행 파일 없음: {cmd[0]}")
        return
    except Exception as e:
        _close_job_files(job)
        _finish_now(on_done, None, f"실행 오류: {e}")
        return
    _jobs.append(job)
    _ensure_pump()


def cancel():
    """진행 중인 CLI 프로세스를 모두 종료한다."""
    for job in _jobs:
        job["cancelled"] = True
        if job["proc"].poll() is None:
            job["proc"].terminate()


def set_keepalive(active: bool):
    """세션이 살아있는 동안 펌프를 유지한다 (session.py가 제어)."""
    _state["keepalive"] = active
    if active:
        _ensure_pump()


def _close_job_files(job):
    for key in ("out_file", "err_file", "in_file"):
        f = job.get(key)
        if f and not f.closed:
            f.close()


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _finish_now(on_done, out, err):
    # Popen 생성 자체가 실패한 경우에도 콜백 계약은 동일하게 유지
    try:
        on_done(out, err)
    except Exception:
        log.exception("LP3D 콜백 오류")


def _ensure_pump():
    if not _state["pump_on"]:
        _state["pump_on"] = True
        bpy.app.timers.register(_pump, first_interval=_PUMP_INTERVAL)


def _pump():
    finished = []
    for job in _jobs:
        rc = job["proc"].poll()
        if rc is None:
            if time.monotonic() > job["deadline"]:
                job["proc"].kill()
                job["timed_out"] = True
            continue
        finished.append(job)
    for job in finished:
        _jobs.remove(job)
        _close_job_files(job)
        stdout = _read(job["out_path"])
        stderr = _read(job["err_path"])
        rc = job["proc"].returncode
        if job.get("timed_out"):
            result, error = None, f"CLI 시간 초과 ({job['timeout']}초)"
        elif job["cancelled"]:
            result, error = None, "사용자 취소"
        elif rc != 0:
            # stdout(JSON 이벤트)에도 원인이 담기므로 둘 다 넘긴다 —
            # codex는 인증 실패를 stdout의 error 이벤트로도 알려준다
            detail = errors.tail(stderr) + "\n" + errors.tail(stdout, 800)
            result, error = None, f"CLI 종료 코드 {rc}\n{detail}"
        else:
            result, error = stdout, None
        try:
            job["on_done"](result, error)
        except Exception:
            log.exception("LP3D 콜백 오류")
    if _state["keepalive"] or _jobs:
        # 세션 진행 중에는 주기적으로 패널을 갱신 (경과 시간 실시간 표시)
        now = time.monotonic()
        if now - _state["last_redraw"] >= _REDRAW_INTERVAL:
            _state["last_redraw"] = now
            _redraw_view3d()
        return _PUMP_INTERVAL
    _state["pump_on"] = False
    return None  # 펌프 종료


def _redraw_view3d():
    wm = bpy.context.window_manager
    if not wm:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
