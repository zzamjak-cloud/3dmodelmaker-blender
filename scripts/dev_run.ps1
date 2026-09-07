# 개발용: 포터블 Blender에 소스를 연결하고 개발 전용으로 실행 (Windows)
#
# 설치된 릴리스 버전(Microsoft Store 등)과 완전히 분리된다. 포터블 Blender 폴더에
# `portable` 디렉터리를 두면 Blender가 설정·확장을 그 안에서 읽고 쓰기 때문에,
# 기존 설치본의 설정이나 확장 목록을 전혀 건드리지 않는다.
#
# 소스 연결에는 심링크가 아니라 디렉터리 정션(junction)을 쓴다. Windows에서
# 심링크는 개발자 모드나 관리자 권한이 필요하지만 정션은 필요 없고, 볼륨이 달라도
# (예: D: 소스 -> C: Blender) 동작한다.
#
# 사용법:
#   .\scripts\dev_run.ps1                    # 기본 경로의 포터블 Blender 실행
#   .\scripts\dev_run.ps1 -BlenderDir "E:\Blender-5.2"
#   .\scripts\dev_run.ps1 -LinkOnly          # 연결만 하고 실행하지 않음
#   .\scripts\dev_run.ps1 -Background -PythonExpr "import bpy; print(bpy.app.version)"
#
# macOS는 scripts/dev_run.sh 를 쓴다.

[CmdletBinding()]
param(
    # 포터블 Blender를 푼 폴더 (blender.exe가 들어 있는 곳)
    [string]$BlenderDir = "D:\Tools\Blender-5.2",

    # Blender 버전 폴더명. 포터블 리소스 경로 <BlenderDir>\portable\<Version>\ 에 쓰인다
    [string]$Version = "5.2",

    # 연결만 하고 Blender를 띄우지 않는다
    [switch]$LinkOnly,

    # GUI 없이 백그라운드로 실행한다 (헤들리스 테스트용)
    [switch]$Background,

    # -Background와 함께 쓰는 파이썬 코드
    [string]$PythonExpr,

    # -Background와 함께 쓰는 파이썬 스크립트 파일
    [string]$PythonFile,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$BlenderArgs
)

$ErrorActionPreference = 'Stop'

$AddonId = "lp3d_modelmaker"
$SourceDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$manifest = Join-Path $SourceDir "blender_manifest.toml"
if (-not (Test-Path $manifest)) { Write-Error "매니페스트를 찾을 수 없습니다: $manifest" }
$manifestText = Get-Content $manifest -Raw -Encoding UTF8
if ($manifestText -notmatch '(?m)^id\s*=\s*"lp3d_modelmaker"\s*$') {
    Write-Error "예상하지 못한 Extension ID입니다"
}

# ---------- 포터블 Blender 확인 ----------

$exe = Join-Path $BlenderDir "blender.exe"
if (-not (Test-Path $exe)) {
    Write-Error @"
포터블 Blender를 찾을 수 없습니다: $exe

blender.org에서 포터블 ZIP을 받아 압축을 푸세요:
  https://download.blender.org/release/Blender$Version/
다른 경로에 풀었다면 -BlenderDir 로 지정하세요.
"@
}

# ---------- 포터블 리소스 폴더 ----------
# `portable` 폴더가 있으면 Blender가 설정/확장을 여기서 읽는다.
# 이것이 설치된 릴리스 버전과 격리되는 핵심이다.

# 확장 경로에 버전 폴더는 들어가지 않는다. Blender가 실제로 쓰는 경로는
# <BlenderDir>\portable\extensions\user_default 이다
# (bpy.utils.resource_path('USER') == <BlenderDir>\portable 로 확인).
$portableRoot = Join-Path $BlenderDir "portable"
$env:BLENDER_USER_RESOURCES = $portableRoot
$extDir = Join-Path $portableRoot "extensions\user_default"
New-Item -ItemType Directory -Path $extDir -Force | Out-Null

# ---------- 소스 정션 연결 ----------

$link = Join-Path $extDir $AddonId

if ($null -ne (Get-Item $link -Force -ErrorAction SilentlyContinue)) {
    $item = Get-Item $link -Force
    $isLink = $item.LinkType -in @('Junction', 'SymbolicLink')
    if ($isLink) {
        # 이미 링크가 있으면 대상이 맞는지 확인하고, 다르면 다시 건다
        $current = @($item.Target)[0]
        if ($current -and (Resolve-Path $current -ErrorAction SilentlyContinue).Path -eq $SourceDir) {
            Write-Host "링크 유지: $link -> $SourceDir"
        } else {
            Write-Host "링크 대상이 달라 재연결합니다 (기존: $current)"
            # 정션 제거는 대상 폴더 내용을 지우지 않는다
            [System.IO.Directory]::Delete($link, $false)
            New-Item -ItemType Junction -Path $link -Target $SourceDir | Out-Null
            Write-Host "링크 완료: $link -> $SourceDir"
        }
    } else {
        # 실제 폴더가 있으면 사용자가 수동 설치한 것일 수 있으므로 지우지 않는다
        Write-Error @"
링크 자리에 실제 폴더가 있습니다: $link

수동으로 설치한 확장으로 보입니다. 개발 링크를 걸려면 먼저 옮기거나 지우세요.
"@
    }
} else {
    New-Item -ItemType Junction -Path $link -Target $SourceDir | Out-Null
    Write-Host "링크 완료: $link -> $SourceDir"
}

if ($LinkOnly) {
    Write-Host ""
    Write-Host "연결만 수행했습니다. 실행하려면 -LinkOnly 없이 다시 호출하세요."
    exit 0
}

# ---------- 실행 ----------

$launchArgs = @('--python-exit-code', '1')
if ($Background) { $launchArgs += '--background' }
$launchArgs += @('--python', (Join-Path $PSScriptRoot 'dev_bootstrap.py'))
if ($PythonFile) {
    if (-not (Test-Path $PythonFile)) { Write-Error "스크립트를 찾을 수 없습니다: $PythonFile" }
    $launchArgs += @('--python', (Resolve-Path $PythonFile).Path)
}
if ($PythonExpr) { $launchArgs += @('--python-expr', $PythonExpr) }
if ($BlenderArgs) { $launchArgs += $BlenderArgs }
Write-Host "격리 프로필: $portableRoot"
Write-Host "개발 Blender 실행: $exe"
& $exe @launchArgs
exit $LASTEXITCODE
