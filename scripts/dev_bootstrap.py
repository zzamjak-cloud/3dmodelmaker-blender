"""격리 프로필의 개발 Extension을 활성화한다."""
import os
from pathlib import Path
import tomllib

import addon_utils
import bpy

source = Path(__file__).resolve().parents[1]
manifest = tomllib.loads((source / "blender_manifest.toml").read_text(encoding="utf-8"))
module = "bl_ext.user_default." + manifest["id"]
profile = Path(os.environ["BLENDER_USER_RESOURCES"]).resolve()
actual = Path(bpy.utils.resource_path("USER")).resolve()
print(f"개발 프로필: {actual}", flush=True)
if actual != profile:
    raise RuntimeError(f"격리 프로필이 일치하지 않습니다: {actual} != {profile}")
link = profile / "extensions" / "user_default" / manifest["id"]
if link.resolve() != source:
    raise RuntimeError("개발 Extension 링크가 현재 저장소를 가리키지 않습니다")
first_enable = module not in bpy.context.preferences.addons
addon = addon_utils.enable(module, default_set=True, persistent=True)
if addon is None or not addon_utils.check(module)[1]:
    raise RuntimeError(f"개발 Extension 활성화 실패: {module}")
if first_enable:
    bpy.ops.wm.save_userpref()
