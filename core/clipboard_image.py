# 클립보드 이미지 붙여넣기: 브라우저(핀터레스트 등)에서 복사한 이미지를 참조로 바로 쓴다.
#
# Blender는 클립보드 이미지를 직접 읽지 못하므로 OS 도구로 꺼내 PNG 파일로 저장한다.
#   - macOS: osascript (클립보드를 PNG로, 없으면 TIFF를 받아 sips로 변환)
#   - Windows: PowerShell WinForms Clipboard.GetImage (STA 필요)
# native_input.py와 같은 서브프로세스 패턴이지만, 클립보드 저장은 순식간에 끝나므로
# 타이머 없이 동기로 실행한다 (UI가 체감상 멈추지 않는다).
import logging
import os
import subprocess
import sys
import time

log = logging.getLogger(__name__)

_TIMEOUT = 15

# PNG를 먼저 시도하고, 실패하면 TIFF로 받아 파일에 쓴다 (변환은 호출부에서 sips로).
# 결과 파일이 생기지 않으면 클립보드에 이미지가 없다는 뜻이다.
_APPLESCRIPT = '''on run argv
    set outPath to item 1 of argv
    set tiffPath to item 2 of argv
    try
        set imgData to the clipboard as «class PNGf»
        set fp to open for access POSIX file outPath with write permission
        set eof of fp to 0
        write imgData to fp
        close access fp
        return "PNG"
    end try
    try
        set imgData to the clipboard as TIFF picture
        set fp to open for access POSIX file tiffPath with write permission
        set eof of fp to 0
        write imgData to fp
        close access fp
        return "TIFF"
    end try
    return "NOIMAGE"
end run'''

# Clipboard.GetImage는 STA 스레드에서만 동작하므로 -STA로 실행해야 한다
_POWERSHELL = '''Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$outPath = $args[0]
$img = [System.Windows.Forms.Clipboard]::GetImage()
if ($img -ne $null) {
    $img.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
    Write-Output "PNG"
} else {
    Write-Output "NOIMAGE"
}'''


def is_supported() -> bool:
    return sys.platform in ("darwin", "win32")


ARCHIVE_PREFIX = "LP3D_ref_clipboard_"  # 붙여넣기 결과 파일명 접두어 — 결과 폴더로 옮길 대상 판별에도 쓴다


def target_path(directory: str) -> str:
    """붙여넣은 이미지를 저장할 경로 — 겹치지 않게 시각을 붙인다."""
    return os.path.join(directory, f"{ARCHIVE_PREFIX}{time.strftime('%Y%m%d-%H%M%S')}.png")


def _run(cmd, cwd=None, flags=0) -> str:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              timeout=_TIMEOUT, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        log.exception("클립보드 명령 실행 실패")
        return ""
    return (proc.stdout or "").strip()


def paste_to(directory: str):
    """클립보드 이미지를 directory에 PNG로 저장한다. (경로, 오류메시지)를 반환."""
    if not is_supported():
        return None, "이 플랫폼에서는 클립보드 붙여넣기를 지원하지 않습니다"
    if not os.path.isdir(directory):
        return None, f"저장할 폴더가 없습니다: {directory}"
    out_path = target_path(directory)
    if sys.platform == "darwin":
        tiff_path = out_path[:-4] + ".tiff"
        result = _run(["osascript", "-e", _APPLESCRIPT, out_path, tiff_path])
        if result == "TIFF" and os.path.isfile(tiff_path):
            # sips는 macOS 기본 제공 — TIFF만 있는 클립보드(일부 앱)를 PNG로 바꾼다
            _run(["sips", "-s", "format", "png", tiff_path, "--out", out_path])
            try:
                os.remove(tiff_path)
            except OSError:
                pass
    else:
        script_path = os.path.join(directory, "lp3d_clipboard.ps1")
        try:
            with open(script_path, "w", encoding="utf-8-sig") as f:
                f.write(_POWERSHELL)
        except OSError:
            return None, "임시 스크립트를 만들지 못했습니다"
        _run(["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
              "-File", script_path, out_path],
             flags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            os.remove(script_path)
        except OSError:
            pass
    if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
        return out_path, None
    return None, "클립보드에 이미지가 없습니다 — 브라우저에서 '이미지 복사'로 복사했는지 확인하세요"
