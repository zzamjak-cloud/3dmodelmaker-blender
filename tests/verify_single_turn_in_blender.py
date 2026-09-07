"""격리 Blender에서 Astra 단일 생성과 Codex 폴백을 검증한다. CLI 과금 호출은 없다."""
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tomllib
import types
from unittest.mock import patch

import addon_utils
import bpy

from bl_ext.user_default.lp3d_modelmaker import preferences
from bl_ext.user_default.lp3d_modelmaker.core import jobs, models, scheduler, session


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    print("PASS " + name, flush=True)


source = Path(__file__).resolve().parents[1]
manifest = tomllib.loads((source / "blender_manifest.toml").read_text(encoding="utf-8"))
module = "bl_ext.user_default." + manifest["id"]
addon = importlib.import_module(module)
profile = Path(os.environ["BLENDER_USER_RESOURCES"]).resolve()
check("프로젝트 격리 프로필", Path(bpy.utils.resource_path("USER")).resolve() == profile)
check("로컬 소스 링크", (profile / "extensions/user_default" / manifest["id"]).resolve() == source)
check("Extension 활성화", addon_utils.check(module)[1])
registered = next(item for item in addon_utils.modules() if item.__name__ == module)
check("소스 버전 일치", addon_utils.module_bl_info(registered)["version"] == tuple(map(int, manifest["version"].split("."))))
check("개선 연산자 제거", "improve" not in dir(bpy.ops.lp3d))
check("단계 스냅샷 연산자 제거", "clear_snapshots" not in dir(bpy.ops.lp3d))

original_scene = bpy.context.window.scene
probe = bpy.data.scenes.new("LP3D_SingleTurnProbe")
workdirs = []
try:
    bpy.context.window.scene = probe
    props = probe.lp3d
    prefs = preferences.get_prefs()
    check("설정의 모델 선택 제거", not hasattr(prefs, "codex_model"))
    props["agent"] = "CLAUDE"
    props["auto_turns"] = 10
    code = "lp.box(name='SingleTurnProbe', size=(2, 1, 1))"

    def run_case(status, unavailable=False):
        job = jobs.add_job(props, "단일 턴 상자")
        job["agent"] = "CLAUDE"
        job["auto_turns"] = 10
        job["improve_feedback"] = "구버전 저장값"
        calls = []

        def fake_cli(cmd, workdir, timeout, callback, **kwargs):
            calls.append((list(cmd), kwargs.get("stdin_text")))
            workdirs.append(workdir)
            if unavailable and len(calls) == 1:
                callback("", "The 'gpt-6-astra' model is not supported with this account")
                return
            response = {"type": "item.completed", "item": {
                "type": "agent_message", "text": f"STATUS: {status}\n```python\n{code}\n```"}}
            callback(json.dumps(response), None)

        with patch.object(preferences, "resolve_cli_path", return_value="/mock/codex"), \
                patch.object(session.runner, "run_cli_async", side_effect=fake_cli), \
                patch.object(session.multiview, "is_available", return_value=False), \
                patch.object(session.library, "fewshot_examples", return_value=[]), \
                patch.object(session.library, "save_entry", return_value=""):
            error = session.start_job(probe.name, job.uid)
            check("생성 시작", error is None)
            for _ in range(20):
                scheduler.pump()
                if not session.is_active(job.uid):
                    break
            check(f"{status} 응답 첫 생성 완료", job.state == 'DONE')
            check("모델링 요청 횟수", len(calls) == (2 if unavailable else 1))
            check("Astra 우선 요청", calls[0][0][calls[0][0].index('-m') + 1] == models.ASTRA_ID)
            check("구버전 Claude 무시", all(call[0][0] == '/mock/codex' for call in calls))
            check("결과 코드 보존", job.code == code)
            coll = bpy.data.collections.get(job.collection_name)
            check("실제 메시 생성", coll is not None and any(obj.type == 'MESH' for obj in coll.objects))
            check("추가 턴 없음", "턴 2" not in job.log and not scheduler.has_work())
            check("폴백 상태 추적", job.model_fallback == unavailable)
            if unavailable:
                check("Codex 기본 모델 폴백", '-m' not in calls[1][0])
                check("폴백 요청 보존", calls[0][1] == calls[1][1])
                check("폴백 실제 모델 표시", job.effective_model == models.CODEX_DEFAULT_LABEL)
            for obj in list(coll.objects):
                bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.collections.remove(coll)
            jobs.remove_job(bpy.context, len(props.jobs) - 1)

    run_case("DONE")
    run_case("REVISE")
    run_case("DONE", unavailable=True)
    check("세션 정리", session.active_count() == 0)
finally:
    session.shutdown()
    bpy.context.window.scene = original_scene
    bpy.data.scenes.remove(probe)
    for workdir in set(workdirs):
        shutil.rmtree(workdir, ignore_errors=True)

removed_name = module + '.agents.claude_cli'
removed_module = types.ModuleType(removed_name)
removed_module.__file__ = str(source / 'agents/claude_cli.py')
sys.modules[removed_name] = removed_module
addon.dev_reload()
check("구버전 Claude 모듈 잔재 제거", removed_name not in sys.modules)
check("Dev Reload 재등록", hasattr(bpy.types.Scene, 'lp3d'))
check("리로드 후 개선 연산자 없음", 'improve' not in dir(bpy.ops.lp3d))
check("리로드 후 단계 스냅샷 연산자 없음", 'clear_snapshots' not in dir(bpy.ops.lp3d))
addon_utils.disable(module, default_set=False)
check("Extension 등록 해제", not addon_utils.check(module)[1] and not hasattr(bpy.types.Scene, 'lp3d'))
check("Extension 재등록", addon_utils.enable(module, default_set=False) is not None)
print("단일 턴 Blender 검증 모두 통과", flush=True)
