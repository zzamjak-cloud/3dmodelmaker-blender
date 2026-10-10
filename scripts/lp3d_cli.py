"""헤드리스 동기 생성 CLI — 패널의 [＋] → [전체 실행] → 익스포트를 명령 한 줄로 돌린다.

사용법 (개발 프로필에서 애드온이 켜진 상태로 실행된다):
  ./scripts/dev_run.sh --background --python scripts/lp3d_cli.py -- \\
      --prompt "낡은 나무 배럴, 금속 밴드 2개" --ref ref.png --out out/barrel.glb \\
      [--style LOWPOLY] [--mode OBJECT] [--review 1] [--no-multiview] \\
      [--game] [--height 0.85] [--footprint 0.96] [--preview out/barrel.png]

로직을 따로 구현하지 않는다. 큐 항목을 만들고 core.session.start_job으로 UI와 같은 세션 상태머신
(시스템 프롬프트 → 멀티뷰 → codex 생성 → 실행·오류 자기수정 → 시각 검토 턴 → 은면·동일평면·게임레디 정리)을
시작한 뒤, 헤드리스에서는 bpy.app.timers가 돌지 않으므로 core.runner.drive로 펌프를 직접 돌린다.
익스포트는 pipeline.export(--game이면 export_game_glb)를 그대로 쓴다.

환경설정 값(검토 횟수·멀티뷰)은 이 실행 동안만 메모리에서 바꾸고 설정 JSON에는 쓰지 않는다.
"""
import argparse
import importlib
import math
import os
import shutil
import sys
import tempfile
import time

import addon_utils
import bpy
from mathutils import Vector

MODULE = "bl_ext.user_default.lp3d_modelmaker"


def _say(text: str):
    print(f"[lp3d] {text}", flush=True)


def _parse(argv):
    parser = argparse.ArgumentParser(prog="lp3d_cli", description="AI LowPoly ModelMaker 헤드리스 생성")
    parser.add_argument("--prompt", required=True, help="만들 모델 설명")
    parser.add_argument("--ref", help="참조 이미지 (PNG/JPG/WEBP)")
    parser.add_argument("--out", required=True, help="출력 .glb 경로")
    parser.add_argument("--style", default="LOWPOLY", help="아트 스타일 id (core/styles.py)")
    parser.add_argument("--mode", default="OBJECT", choices=("OBJECT", "SCENE", "CHARACTER"))
    parser.add_argument("--review", type=int, default=1, help="시각 검토 턴 수 0~2 (UI 기본 0, CLI 기본 1)")
    parser.add_argument("--no-multiview", action="store_true", help="멀티뷰 참조 시트 생성을 건너뛴다")
    parser.add_argument("--game", action="store_true", help="팔레트 → 셀별 단색 머티리얼 게임용 glb")
    parser.add_argument("--height", type=float, help="--game: 목표 높이(m)")
    parser.add_argument("--footprint", type=float, help="--game: 최대 발판(m, 가로·세로 중 큰 쪽)")
    parser.add_argument("--preview", help="내보낸 glb를 Eevee로 렌더한 미리보기 PNG 경로")
    return parser.parse_args(argv)


def _addon():
    if MODULE not in bpy.context.preferences.addons:
        addon_utils.enable(MODULE, default_set=False, persistent=False)
    return {name: importlib.import_module(f"{MODULE}.{name}")
            for name in ("core.jobs", "core.persist", "core.runner", "core.session", "core.styles",
                         "lowpoly.cleanup", "pipeline.export", "preferences")}


# ---------- 미리보기 ----------

def _eevee(scene):
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = engine
            return
        except TypeError:
            continue


def setup_preview_scene(scene, resolution=512):
    """밝은 중립 조명 — 균일한 흰 월드(앰비언트) + 앞-왼쪽-위 태양광, Standard 뷰 변환(albedo 그대로)."""
    _eevee(scene)
    world = scene.world or bpy.data.worlds.new("LP3D_PreviewWorld")
    scene.world = world
    world.use_nodes = True
    background = next(n for n in world.node_tree.nodes if n.type == 'BACKGROUND')
    background.inputs["Color"].default_value = (0.42, 0.42, 0.44, 1.0)
    background.inputs["Strength"].default_value = 1.0
    sun = bpy.data.objects.get("LP3D_PreviewSun")
    if sun is None:
        sun = bpy.data.objects.new("LP3D_PreviewSun", bpy.data.lights.new("LP3D_PreviewSun", 'SUN'))
        scene.collection.objects.link(sun)
    sun.data.energy = 0.9
    sun.rotation_euler = (math.radians(50), 0.0, math.radians(-35))
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    scene.render.film_transparent = False
    scene.render.resolution_x = scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'


def bounds(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs if o.type == 'MESH' for c in o.bound_box]
    lo = Vector([min(p[i] for p in pts) for i in range(3)])
    hi = Vector([max(p[i] for p in pts) for i in range(3)])
    return lo, hi


def render_preview(objs, path, ortho_scale=None, target=None):
    """정면(-Y)·왼쪽(-X)·위가 보이는 쿼터뷰 정사영 렌더. ortho_scale·target을 고정하면 여러 모델을 같은 카메라로 찍는다."""
    scene = bpy.context.scene
    bpy.context.view_layer.update()
    lo, hi = bounds(objs)
    target = target if target is not None else (lo + hi) / 2
    direction = Vector((-1.0, -1.0, math.sqrt(2.0) * math.tan(math.radians(35)))).normalized()
    data = bpy.data.cameras.new("LP3D_PreviewCam")
    data.type = 'ORTHO'
    data.ortho_scale = ortho_scale or (hi - lo).length * 1.08
    cam = bpy.data.objects.new("LP3D_PreviewCam", data)
    scene.collection.objects.link(cam)
    dist = data.ortho_scale * 4.0 + 1.0
    cam.location = target + direction * dist
    cam.rotation_euler = (-direction).to_track_quat('-Z', 'Y').to_euler()
    data.clip_end = dist * 3.0
    scene.camera = cam
    scene.render.filepath = path
    try:
        bpy.ops.render.render(write_still=True)
    finally:
        bpy.data.objects.remove(cam)
        bpy.data.cameras.remove(data)


def import_glb(path, collection_name):
    """glb를 새 컬렉션으로 가져와 그 오브젝트 목록을 돌려준다."""
    coll = bpy.data.collections.new(collection_name)
    bpy.context.scene.collection.children.link(coll)
    layer = bpy.context.view_layer.layer_collection.children[coll.name]
    bpy.context.view_layer.active_layer_collection = layer
    bpy.ops.import_scene.gltf(filepath=path)
    return list(coll.all_objects)


def _preview(path_glb, path_png):
    """내보낸 glb를 그대로 다시 가져와 찍는다 — 엔진이 받는 것과 같은 결과를 본다.

    시작 씬의 기본 큐브·생성 결과 컬렉션 등 다른 메시는 렌더에서 숨긴다."""
    others = [o for o in bpy.context.scene.objects if o.type == 'MESH']
    for obj in others:
        obj.hide_render = True
    setup_preview_scene(bpy.context.scene)
    objs = import_glb(path_glb, "LP3D_Preview")
    render_preview(objs, path_png)


# ---------- 실행 ----------

def _run(args, mods, timings):
    session, runner, jobs = mods["core.session"], mods["core.runner"], mods["core.jobs"]
    scene = bpy.context.scene
    props = scene.lp3d
    job = jobs.add_job(props, args.prompt, mode=args.mode)
    job.style = args.style
    job.modeling_type = 'PALETTE'
    if args.ref:
        job.ref_image_path = os.path.abspath(args.ref)
    uid = job.uid
    error = session.start_job(scene.name, uid)
    if error:
        raise SystemExit(f"[lp3d] 시작 실패: {error}")

    last = {"status": None, "at": time.time()}

    def done():
        current = props.job_by_uid(uid)
        status = current.status if current else ""
        if status != last["status"]:
            now = time.time()
            if last["status"] is not None:
                timings.append((last["status"], now - last["at"]))
            _say(f"{status}")
            last.update(status=status, at=now)
        return not session.is_active(uid)

    runner.drive(done)
    job = props.job_by_uid(uid)
    if job.state != 'DONE':
        _say("세션 로그:\n" + job.log)
        raise SystemExit(f"[lp3d] 생성 실패: {job.status}")
    _say("세션 로그:\n" + job.log)
    return bpy.data.collections[job.collection_name]


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = _parse(argv)
    mods = _addon()
    styles = [s["id"] for s in mods["core.styles"].STYLES]
    if args.style not in styles:
        raise SystemExit(f"[lp3d] 알 수 없는 스타일 {args.style} — {', '.join(styles)}")
    if args.ref and not os.path.isfile(args.ref):
        raise SystemExit(f"[lp3d] 참조 이미지가 없습니다: {args.ref}")
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    persist = mods["core.persist"]
    prefs = mods["preferences"].get_prefs()
    overrides = {"object_review_turns": max(0, min(2, args.review))}
    if args.no_multiview:
        overrides["use_multiview"] = False
    saved = {key: getattr(prefs, key) for key in overrides}
    persist._suspended = True   # 이 실행의 임시 값이 사용자 설정 JSON에 저장되지 않게 한다
    started = time.time()
    phases = []
    try:
        for key, value in overrides.items():
            setattr(prefs, key, value)
        coll = _run(args, mods, phases)
    finally:
        for key, value in saved.items():
            setattr(prefs, key, value)
        persist._suspended = False
    generated = time.time()
    tris = mods["lowpoly.cleanup"].collection_tri_count(coll)

    export = mods["pipeline.export"]
    if args.game:
        stats = export.export_game_glb(coll, out, height=args.height, footprint=args.footprint)
        size = "x".join(f"{v:.3f}" for v in stats["size"])
        _say(f"게임용 glb → {out} (머티리얼 {stats['materials']}개, {stats['tris']} tris, "
             f"크기 {size}, 배율 {stats['scale']:.3f})")
    else:
        temp = tempfile.mkdtemp(prefix="lp3d_export_")
        shutil.move(export.export_collection(coll, temp, 'GLTF'), out)
        shutil.rmtree(temp, ignore_errors=True)
        _say(f"glb → {out}")
    exported = time.time()
    if args.preview:
        _preview(out, os.path.abspath(args.preview))
        _say(f"미리보기 → {args.preview}")
    finished = time.time()

    for status, seconds in phases:
        if seconds >= 0.5:
            _say(f"  {seconds:6.1f}s  {status}")
    _say(f"시간: 생성 {generated - started:.1f}s, 익스포트 {exported - generated:.1f}s, "
         f"미리보기 {finished - exported:.1f}s, 합계 {finished - started:.1f}s")
    _say(f"트라이: 생성 {tris} tris")


if __name__ == "__main__":
    main()
