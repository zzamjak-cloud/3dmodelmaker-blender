# OS 네이티브 텍스트 입력 팝업 — Blender 텍스트 위젯의 한글 IME 문제(글자 소실,
# 스페이스 2회 입력 등)를 우회한다. Blender 내부 팝업(invoke_props_dialog)은
# 같은 위젯을 쓰므로 해결되지 않고, OS 다이얼로그를 subprocess로 띄워야 한다.
#   - macOS: osascript(display dialog)
#   - Windows: PowerShell + WinForms 폼 (InputBox는 취소/빈 입력 구분 불가)
#
# 값 전달은 UTF-8 파일로 한다: 초기값 파일 → 다이얼로그 프리필, 결과 파일은
# [입력완료]일 때만 생성된다 (파일 없음 = 취소). stdout을 쓰지 않는 이유는
# Windows 콘솔 코드페이지와 따옴표 이스케이프 문제를 피하기 위함이다.
#
# 스레드 금지(릴리스 검증기 제약) — Popen 논블로킹 + bpy.app.timers 폴링.
# bpy는 함수 안에서 임포트한다 (순수 파이썬 단위 테스트에서 모듈 로드 가능하도록).
import logging
import os
import shutil
import subprocess
import sys
import tempfile

log = logging.getLogger(__name__)

_POLL_INTERVAL = 0.2
_state = {"proc": None, "on_done": None, "dir": None, "result": None}

# 취소 버튼은 cancel button 선언으로 에러(-128) 종료시켜 결과 파일을 만들지 않는다.
# osascript 프로세스는 백그라운드라 자기 다이얼로그가 Blender 뒤에 깔린다 —
# System Events를 activate해 그쪽에서 띄워야 전면에 온다 (최초 1회 macOS가
# "Blender이(가) System Events를 제어하려고 합니다" 자동화 권한을 묻는다 → 허용).
_APPLESCRIPT = '''on run argv
    set dialogTitle to item 1 of argv
    set initPath to item 2 of argv
    set resultPath to item 3 of argv
    set initText to ""
    try
        set initText to (read POSIX file initPath as «class utf8»)
    end try
    tell application "System Events"
        activate
        set d to display dialog dialogTitle default answer initText buttons {"취소", "입력완료"} default button "입력완료" cancel button "취소" with title dialogTitle
    end tell
    set out to open for access POSIX file resultPath with write permission
    set eof of out to 0
    write (text returned of d) to out as «class utf8»
    close access out
end run'''

# PS 5.1이 한글을 올바르게 읽도록 .ps1은 BOM 포함 UTF-8로 저장해야 한다
_POWERSHELL = '''Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$title = $args[0]
$initPath = $args[1]
$resultPath = $args[2]
$utf8 = New-Object System.Text.UTF8Encoding($false)
$init = ''
if (Test-Path -LiteralPath $initPath) {
    $init = [System.IO.File]::ReadAllText($initPath, $utf8)
}
$form = New-Object System.Windows.Forms.Form
$form.Text = $title
$form.Width = 560
$form.Height = 230
$form.StartPosition = 'CenterScreen'
$form.TopMost = $true
$box = New-Object System.Windows.Forms.TextBox
$box.Multiline = $true
$box.AcceptsReturn = $false
$box.ScrollBars = 'Vertical'
$box.Text = $init
$box.SetBounds(12, 12, 520, 110)
$box.Anchor = 'Top,Left,Right,Bottom'
$ok = New-Object System.Windows.Forms.Button
$ok.Text = '입력완료'
$ok.DialogResult = 'OK'
$ok.SetBounds(330, 140, 98, 32)
$ok.Anchor = 'Bottom,Right'
$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = '취소'
$cancel.DialogResult = 'Cancel'
$cancel.SetBounds(434, 140, 98, 32)
$cancel.Anchor = 'Bottom,Right'
$form.Controls.Add($box)
$form.Controls.Add($ok)
$form.Controls.Add($cancel)
$form.AcceptButton = $ok
$form.CancelButton = $cancel
$form.Add_Shown({ $form.Activate(); $box.Focus(); $box.SelectAll() })
if ($form.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [System.IO.File]::WriteAllText($resultPath, $box.Text, $utf8)
}'''


def is_open() -> bool:
    return _state["proc"] is not None


def to_single_line(text: str) -> str:
    """StringProperty는 단일행이므로 개행을 공백으로 정규화한다."""
    return " ".join(text.split())


def build_command(platform: str, work_dir: str, title: str, init_path: str, result_path: str):
    """플랫폼별 다이얼로그 실행 명령을 만든다. (cmd 리스트, creationflags) 반환.

    Windows는 인라인 -Command 대신 .ps1 파일을 쓴다 — 따옴표 이스케이프와
    코드페이지 문제를 피하고, CREATE_NO_WINDOW로 콘솔 창을 숨긴다."""
    if platform == "darwin":
        return (["osascript", "-e", _APPLESCRIPT, title, init_path, result_path], 0)
    if platform == "win32":
        script_path = os.path.join(work_dir, "dialog.ps1")
        with open(script_path, "w", encoding="utf-8-sig") as f:
            f.write(_POWERSHELL)
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", script_path, title, init_path, result_path]
        return (cmd, getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return (None, 0)


def open_dialog(title: str, initial_text: str, on_done):
    """네이티브 입력 다이얼로그를 연다. 완료 시 메인 스레드에서 on_done(text)를 호출한다.

    text가 None이면 취소. 반환값은 에러 메시지(시작 실패 시) 또는 None(정상 시작)."""
    import bpy

    if is_open():
        return "이미 입력 창이 열려 있습니다"
    work = tempfile.mkdtemp(prefix="lp3d_input_")
    init_path = os.path.join(work, "initial.txt")
    result_path = os.path.join(work, "result.txt")
    try:
        with open(init_path, "w", encoding="utf-8") as f:
            f.write(initial_text or "")
        cmd, flags = build_command(sys.platform, work, title, init_path, result_path)
        if cmd is None:
            shutil.rmtree(work, ignore_errors=True)
            return "이 플랫폼에서는 네이티브 입력 창을 지원하지 않습니다 — 외부에서 작성 후 붙여넣어 주세요"
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except Exception as e:
        shutil.rmtree(work, ignore_errors=True)
        return f"입력 창 실행 실패: {e}"
    _state.update(proc=proc, on_done=on_done, dir=work, result=result_path)
    bpy.app.timers.register(_poll, first_interval=_POLL_INTERVAL)
    return None


def _poll():
    proc = _state["proc"]
    if proc is None:
        return None
    if proc.poll() is None:
        return _POLL_INTERVAL
    on_done = _state["on_done"]
    result_path = _state["result"]
    text = None
    try:
        with open(result_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = None  # 결과 파일 없음 = 취소 (macOS는 취소 시 rc=1로 끝나도 동일 처리)
    shutil.rmtree(_state["dir"], ignore_errors=True)
    _state.update(proc=None, on_done=None, dir=None, result=None)
    try:
        on_done(text)
    except Exception:
        log.exception("LP3D 입력 콜백 오류")
    _redraw_view3d()
    return None


def _redraw_view3d():
    import bpy

    wm = bpy.context.window_manager
    if not wm:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
