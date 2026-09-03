# 개발용: 격리 빌드에서 패널 UI를 실제로 그려보고 스크린샷으로 남긴다
#
# 순수 파이썬 테스트(tests/)로는 draw() 경로 — 멀티뷰 썸네일, 실패 경고 표시 —
# 를 검증할 수 없다. 여기서는 Blender GUI를 격리 프로필로 띄워 사이드바를
# 캡처한다. 릴리스 설치본과 사용자 설정은 건드리지 않는다.
#
# 사용법 (Windows / PowerShell):
#   $env:LP3D_PKG   = "<소스 부모 폴더>"   # 그 아래 lp3d_modelmaker 폴더가 있어야 함
#   $env:LP3D_SHEET = "<멀티뷰 PNG 경로>"
#   $env:LP3D_OUT   = "<스크린샷 저장 폴더>"
#   $env:BLENDER_USER_RESOURCES = "<격리 프로필 폴더>"
#   blender.exe --factory-startup --python scripts/dev_ui_check.py
#
# HOME/USERPROFILE도 임시 폴더로 바꿔 실행하면 보관 폴더(~/Downloads/blender)까지
# 격리된다 — 실제 다운로드 폴더에 시트를 쓰지 않게 하려면 그렇게 하는 편이 안전하다.
import os
import sys
import traceback

sys.path.insert(0, os.environ["LP3D_PKG"])

import bpy
import numpy as np

SHEET = os.environ.get("LP3D_SHEET", "")
OUT = os.environ.get("LP3D_OUT", bpy.app.tempdir)

import lp3d_modelmaker as addon
# 사이드바 탭(Region.active_panel_category)은 읽기 전용이라 코드로 전환할 수 없다.
# draw() 코드는 그대로 두고 배치 탭만 기본 탭으로 옮겨 촬영한다.
from lp3d_modelmaker.ui import panel as _panel
_panel.LP3D_PT_main.bl_category = "Item"
addon.register()

from lp3d_modelmaker.core import errors, jobs, multiview  # noqa: E402
from lp3d_modelmaker.ui import previews  # noqa: E402

# 실제로 겪었던 codex 토큰 만료 실패를 그대로 재현한다
AUTH_FAILURE = (
    'CLI 종료 코드 1\n'
    '2026-09-02T01:31:04Z ERROR codex_login::auth::manager: '
    'Failed to refresh token: 401 Unauthorized: {\n'
    '  "message": "Your session has ended. Please log in again.",\n'
    '  "code": "refresh_token_invalidated"\n}\n'
)

_ui_rect = [0, 0, 0, 0]


def _prepare_sidebar():
    for area in bpy.context.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        area.spaces[0].show_region_ui = True
        for region in area.regions:
            if region.type == 'UI':
                _ui_rect[:] = [region.x, region.y, region.width, region.height]
        area.tag_redraw()


def _crop(src: str, dst: str):
    """전체 창 스크린샷에서 사이드바만 잘라낸다 — 축소되면 글자를 읽을 수 없다."""
    x, y, w, h = _ui_rect
    if w <= 0:
        return
    img = bpy.data.images.load(src)
    px = np.array(img.pixels[:]).reshape(img.size[1], img.size[0], 4)
    crop = px[y:y + h, x:x + w]
    out = bpy.data.images.new("lp3d_crop", width=crop.shape[1], height=crop.shape[0],
                              alpha=True)
    out.pixels = crop.ravel().tolist()
    out.filepath_raw = dst
    out.file_format = 'PNG'
    out.save()
    print("CROP_SAVED:", dst)


def _shot(tag: str):
    full = os.path.join(OUT, f"full_{tag}.png")
    bpy.ops.screen.screenshot(filepath=full)
    _crop(full, os.path.join(OUT, f"panel_{tag}.png"))


def step_setup():
    _prepare_sidebar()
    props = bpy.context.scene.lp3d
    # 상태는 이제 씬이 아니라 큐 항목(job) 하나에 담긴다 — 촬영용 항목을 만들어 선택해둔다
    job = jobs.add_job(props, "테스트 프롬프트")
    job.multiview_path = SHEET
    props.multiview_preview_open = True
    job.state = 'FAILED'
    job.status = "실패: " + errors.describe(AUTH_FAILURE, "codex")
    job.status_hint = errors.action(AUTH_FAILURE, "codex")
    job.log = "\n".join(["세션 종료: 실패"]
                        + [f"  · {l}" for l in errors.detail_lines(AUTH_FAILURE)])
    print("ICON_ID:", previews.icon_id(SHEET))   # 0이면 프리뷰 로드 실패
    print("STATUS:", job.status)
    print("HINT:", job.status_hint)
    print("ARCHIVE_DIR:", multiview.archive_dir())


def step_shot_with_sheet():
    _shot("with_sheet")
    # 다음 촬영 상태로 바꿔만 둔다 — 같은 콜백에서 찍으면 리드로우 전 프레임이 찍힌다
    job = bpy.context.scene.lp3d.active_job()
    job.multiview_path = ""
    job.status = "멀티뷰 실패: " + errors.describe(AUTH_FAILURE, "codex")
    job.status_hint = ""


def step_shot_multiview_fail():
    """멀티뷰만 실패하고 생성은 계속되는 상태 — 경고 표시가 붙어야 한다."""
    _shot("mv_failed")
    job = bpy.context.scene.lp3d.active_job()
    job.state = 'PENDING'
    job.status = "대기 중"


def step_shot_no_sheet():
    """시트가 없을 때 — 저장된 시트를 불러오는 버튼이 보여야 한다."""
    _shot("no_sheet")
    print("LOAD_OP:", bpy.ops.lp3d.load_last_multiview(),
          "->", repr(bpy.context.scene.lp3d.active_job().multiview_path))
    bpy.ops.wm.quit_blender()


def _guard(fn):
    def wrapped():
        try:
            fn()
        except Exception:
            traceback.print_exc()
            bpy.ops.wm.quit_blender()
    return wrapped


def _watchdog():
    # 어떤 단계가 실패해도 Blender 창이 떠 있는 채로 남지 않게 한다.
    # (등록 도중 예외가 나면 종료 타이머 자체가 안 걸려 프로세스가 매달린다)
    print("WATCHDOG: 시간 초과로 강제 종료")
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(_watchdog, first_interval=120.0)
bpy.app.timers.register(_guard(step_setup), first_interval=1.0)
bpy.app.timers.register(_guard(step_shot_with_sheet), first_interval=4.0)
bpy.app.timers.register(_guard(step_shot_multiview_fail), first_interval=6.0)
bpy.app.timers.register(_guard(step_shot_no_sheet), first_interval=8.0)
