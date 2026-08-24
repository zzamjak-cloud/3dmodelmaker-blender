# 비동기 CLI 실행: 워커 스레드 + queue + bpy.app.timers 펌프
#
# 규칙: bpy API는 반드시 메인 스레드(타이머 콜백)에서만 호출한다.
# 워커 스레드는 subprocess만 다루고 결과를 큐에 넣는다.
import queue
import subprocess
import threading

import bpy

_result_queue = queue.Queue()
_state = {"proc": None, "cancelled": False, "keepalive": False, "pump_on": False}
_PUMP_INTERVAL = 0.25


def run_cli_async(cmd: list, cwd: str, timeout: int, on_done):
    """CLI를 워커 스레드로 실행하고 완료 시 메인 스레드에서 on_done(stdout, error)를 호출한다."""
    def worker():
        try:
            proc = subprocess.Popen(
                cmd, cwd=cwd, text=True,
                stdin=subprocess.DEVNULL,  # CLI가 stdin을 기다리지 않도록
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            _state["proc"] = proc
            try:
                out, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                _result_queue.put((on_done, None, f"CLI 시간 초과 ({timeout}초)"))
                return
            if _state["cancelled"]:
                _result_queue.put((on_done, None, "사용자 취소"))
            elif proc.returncode != 0:
                detail = (err or out or "")[-1500:]
                _result_queue.put((on_done, None, f"CLI 종료 코드 {proc.returncode}\n{detail}"))
            else:
                _result_queue.put((on_done, out, None))
        except FileNotFoundError:
            _result_queue.put((on_done, None, f"실행 파일 없음: {cmd[0]}"))
        except Exception as e:  # 워커 스레드는 절대 죽지 않고 오류를 큐로 전달
            _result_queue.put((on_done, None, f"실행 오류: {e}"))
        finally:
            _state["proc"] = None

    _state["cancelled"] = False
    threading.Thread(target=worker, daemon=True).start()
    _ensure_pump()


def cancel():
    """진행 중인 CLI 프로세스를 종료한다."""
    _state["cancelled"] = True
    proc = _state["proc"]
    if proc and proc.poll() is None:
        proc.terminate()


def set_keepalive(active: bool):
    """세션이 살아있는 동안 펌프를 유지한다 (session.py가 제어)."""
    _state["keepalive"] = active
    if active:
        _ensure_pump()


def _ensure_pump():
    if not _state["pump_on"]:
        _state["pump_on"] = True
        bpy.app.timers.register(_pump, first_interval=_PUMP_INTERVAL)


def _pump():
    # 큐에 쌓인 완료 콜백을 메인 스레드에서 소진
    while True:
        try:
            on_done, out, err = _result_queue.get_nowait()
        except queue.Empty:
            break
        try:
            on_done(out, err)
        except Exception:
            import traceback
            print("[LP3D] 콜백 오류:\n" + traceback.format_exc())
    if _state["keepalive"] or _state["proc"] is not None or not _result_queue.empty():
        return _PUMP_INTERVAL
    _state["pump_on"] = False
    return None  # 펌프 종료
