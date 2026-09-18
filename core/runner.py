# 비동기 CLI 실행: subprocess.Popen(논블로킹) + bpy.app.timers 폴링
#
# 스레드를 쓰지 않는다 — Popen은 논블로킹이고, 완료 여부는 타이머에서 poll()로
# 확인한다. stdout/stderr는 파이프 버퍼 블로킹을 피하기 위해 파일로 리다이렉트한다.
# 모든 bpy 호출과 콜백은 메인 스레드(타이머 콜백)에서 실행된다.
import json
import logging
import os
import subprocess
import sys
import time

import bpy

from . import errors, scheduler

log = logging.getLogger(__name__)

_jobs = []  # 진행 중인 CLI 작업 목록
_http_jobs = []  # 진행 중인 HTTP 작업 목록 (OpenRouter 이미지 생성)
_state = {"pump_on": False, "last_redraw": 0.0}
_keepalive = set()  # 펌프를 살려둬야 하는 job_key 집합
_PUMP_INTERVAL = 0.25
_REDRAW_INTERVAL = 1.0  # 경과 시간 표시 갱신 주기
_job_counter = 0


def run_cli_async(cmd: list, cwd: str, timeout: int, on_done, stdin_text: str = None,
                  job_key=None):
    """CLI를 논블로킹으로 실행하고 완료 시 메인 스레드에서 on_done(stdout, error)를 호출한다.

    job_key는 잡 단위 취소용 식별자다 (없으면 전체 취소에만 걸린다).

    stdin_text가 주어지면 파일로 저장해 stdin으로 넘긴다. 프롬프트를 명령줄 인자로
    넘기면 Windows의 .cmd 셸림(npm 설치본)이 첫 줄에서 잘라버리기 때문이다."""
    global _job_counter
    _job_counter += 1
    out_path = os.path.join(cwd, f"cli_stdout_{_job_counter}.log")
    err_path = os.path.join(cwd, f"cli_stderr_{_job_counter}.log")
    job = {
        "cmd": cmd, "on_done": on_done, "cancelled": False, "job_key": job_key,
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


# 자식 파이썬이 워커 파일을 실행하는 부트 코드 — 워커 모듈에 __main__ 블록을 두지 않기 위해 main 을 직접 부른다
_WORKER_BOOT = "import sys, runpy; sys.exit(runpy.run_path(sys.argv[1])['main'](sys.argv[2:]))"


def _worker_command(spec_path: str) -> list:
    """HTTP 워커 실행 명령. Blender 의 sys.executable 은 번들 파이썬이다(2.92+, 확장 플랫폼 4.2+ 전부)."""
    from . import http_worker
    return [sys.executable, "-c", _WORKER_BOOT, http_worker.__spec__.origin, spec_path]


def run_http_async(spec: dict, on_done, job_key=None):
    """HTTP 요청 spec(http_worker 참고)을 자식 파이썬 프로세스로 수행하고, 완료 시 메인 스레드에서
    on_done(result_dict|None, error|None)을 호출한다. result_dict 는 http_worker.perform 의 반환값이다.

    스레드를 쓰지 않는다 — urllib 은 논블로킹 폴링이 안 되므로 요청을 자식 프로세스에 맡기고, 종료는 CLI 잡과
    같은 타이머 펌프에서 poll() 로 확인한다. 취소는 프로세스를 죽여서 즉시 끊는다."""
    global _job_counter
    _job_counter += 1
    work_dir = spec.get("work_dir") or os.path.dirname(spec.get("out_file") or "") or None
    if not work_dir or not os.path.isdir(work_dir):
        import tempfile
        work_dir = tempfile.mkdtemp(prefix="lp3d_http_")
    spec = dict(spec)
    spec.setdefault("result_file", os.path.join(work_dir, f"http_result_{_job_counter}.json"))
    spec_path = os.path.join(work_dir, f"http_spec_{_job_counter}.json")
    job = {"on_done": on_done, "job_key": job_key, "cancelled": False,
           "result_file": spec["result_file"], "out_path": os.path.join(work_dir, f"http_stdout_{_job_counter}.log"),
           "err_path": os.path.join(work_dir, f"http_stderr_{_job_counter}.log"),
           "deadline": time.monotonic() + float(spec.get("timeout", 300)) + float((spec.get("poll") or {}).get("timeout", 0)) + 30,
           "timeout": spec.get("timeout", 300)}
    try:
        with open(spec_path, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
        job["out_file"] = open(job["out_path"], "w", encoding="utf-8")
        job["err_file"] = open(job["err_path"], "w", encoding="utf-8")
        cmd = _worker_command(spec_path)
        job["proc"] = subprocess.Popen(cmd, cwd=work_dir, stdin=subprocess.DEVNULL,
                                       stdout=job["out_file"], stderr=job["err_file"])
    except Exception as e:
        _close_job_files(job)
        _finish_now(on_done, None, f"HTTP 워커 실행 오류: {e}")
        return
    _http_jobs.append(job)
    _ensure_pump()


def cancel(job_key=None):
    """진행 중인 CLI 프로세스를 종료한다.

    job_key가 주어지면 그 잡의 프로세스만, 없으면 전부 종료한다.
    여러 세션이 동시에 도는 구조에서 한 세션의 취소가 남의 프로세스를
    죽이면 안 되므로 기본은 잡 단위 호출이다."""
    for job in _jobs:
        if job_key is not None and job.get("job_key") != job_key:
            continue
        job["cancelled"] = True
        if job["proc"].poll() is None:
            job["proc"].terminate()
    for job in _http_jobs:
        if job_key is not None and job.get("job_key") != job_key:
            continue
        job["cancelled"] = True
        if job["proc"].poll() is None:
            job["proc"].terminate()


def add_keepalive(job_key):
    """세션이 살아있는 동안 펌프를 유지한다 (session.py가 제어).

    전역 불리언이었을 때는 한 세션이 끝나면 다른 세션의 펌프까지 꺼져
    진행 중인 잡이 영영 멈췄다. 그래서 잡 단위 집합으로 관리한다."""
    _keepalive.add(job_key)
    _ensure_pump()


def remove_keepalive(job_key):
    _keepalive.discard(job_key)


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
    _pump_http()
    scheduler.pump()

    if _keepalive or _jobs or _http_jobs or scheduler.has_work():
        # 세션 진행 중에는 주기적으로 패널을 갱신 (경과 시간 실시간 표시)
        now = time.monotonic()
        if now - _state["last_redraw"] >= _REDRAW_INTERVAL:
            _state["last_redraw"] = now
            _redraw_view3d()
        return _PUMP_INTERVAL
    _state["pump_on"] = False
    return None  # 펌프 종료


def _pump_http():
    """끝난 HTTP 워커 프로세스의 결과를 읽어 콜백을 메인 스레드에서 실행한다."""
    finished = []
    for job in _http_jobs:
        if job["proc"].poll() is None:
            if time.monotonic() > job["deadline"]:
                job["proc"].kill()
                job["timed_out"] = True
            continue
        finished.append(job)
    for job in finished:
        _http_jobs.remove(job)
        _close_job_files(job)
        if job["cancelled"]:
            continue  # 취소된 세션에 결과를 되돌려주지 않는다
        result, error = None, None
        if job.get("timed_out"):
            error = f"HTTP 요청 시간 초과 ({job['timeout']}초)"
        else:
            try:
                with open(job["result_file"], encoding="utf-8") as f:
                    result = json.load(f)
            except (OSError, ValueError) as exc:
                # 자식이 결과 파일을 못 남긴 경우 — 종료 코드·표준 출력·오류 출력을 모두 남겨 원인을 추적한다
                error = (f"HTTP 워커가 결과를 남기지 않았습니다 (종료 코드 {job['proc'].returncode}, {type(exc).__name__}: {exc})\n"
                         + errors.tail(_read(job["err_path"])) + "\n" + errors.tail(_read(job["out_path"]), 800))
        try:
            job["on_done"](result, error)
        except Exception:
            log.exception("LP3D 콜백 오류")


def _redraw_view3d():
    wm = bpy.context.window_manager
    if not wm:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
