# 쿼드 리토폴로지 — 셰이프 서버가 구운 PBR 메시 위에 새 와이어를 깔고 텍스처를 베이크로 옮긴다
#
# 입력은 retopo.import_textured() 가 정리해 둔 단일 셸(용접·안쪽 껍질 제거·키 정규화 완료)과
# PBR 텍스처다. 순서: 원본 보존 → 작업본 → 복셀 리메시(촘촘히) → 파편 제거 → 데시메이트 → 매니폴드 수리
# → (링 가이드 위치에 절단 띠) → QuadriFlow(좌우 대칭이면 양의 반쪽만, 실패 시 데시메이트 폴백)
# → (절단 링 접합) → 구멍 쿼드 메움 → 투영 슈링크랩+릴랙스 → 대칭면 미러 용접 → UV 언랩 → Cycles 베이크
# → 새 머티리얼.
#
# 링 가이드: 팔·다리·목 같은 원통은 가우스 곡률이 0 이라 QuadriFlow 방향장이 임의 각도로 굳어 나선 와이어가
# 생긴다. 가이드 위치의 얇은 띠를 지워 경계를 만들고 경계 보존으로 깔면 그 경계가 앵커가 되어 링 루프가 축을
# 따라 전파된다. QuadriFlow 가 막은 캡을 지우고 양쪽 링을 브리지로 다시 잇는다(ring_cut.py).
#
# 텍스처를 AI 로 다시 만들지 않고 원본에서 굽는 것이 예전 리토폴로지 경로와의 결정적인 차이다.
# 어느 단계가 실패해도 원본 오브젝트는 손대지 않는다 — 작업본과 보존본만 지우고 예외를 올린다.
import math
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager

import bmesh
import bpy
from mathutils import Vector

from . import ring_cut
from .names import safe_id_name

SOURCE_SUFFIX = '_원본'
SOURCE_KEY = 'lp3d_source_mesh'   # 이 표식이 있는 오브젝트는 리토폴로지 대상이 아닌 보존본이다

_WORKER = os.path.join(os.path.dirname(__file__), "quadriflow_worker.py")

MERGE_DIST = 2e-4              # 이보다 짧은 엣지는 이 길이까지 늘린다. Blender 의 QuadriFlow 사전 검사는 길이가
                               # 1e-4 미만인 엣지를 '길이 0'으로 보고 거절한다(실측 2026-09-20: 27개 때문에
                               # 완전 매니폴드 메시가 계속 거절당했다)
FRAGMENT_RATIO = 0.005         # 전체 면수의 이 비율 미만인 떨어진 셸은 복셀 리메시 거품으로 보고 지운다
REPAIR_ROUNDS = 4              # 수리 반복 상한 — 실측상 1회면 끝난다
# QuadriFlow 입력 사다리 — (복셀 리메시 면수, 데시메이트 뒤 삼각형 수). 실패하면 다음 단으로 내려간다.
# 복셀은 촘촘히 굽고(겨드랑이·다리 사이 같은 좁은 틈이 살아남게) 데시메이트로 QuadriFlow 가 받는 크기까지
# 낮춘다. 실측(2026-09-21, 죄수): 복셀 1.4만면(2.2cm)은 3.8cm 겨드랑이 틈을 메워 팔이 몸통에 붙었고,
# 3.2만면(1.5cm)→1.6만 삼각형은 틈을 보존(광선 교차 일치 0.86~0.90). 5.6만면→1.6만 삼각형은 데시메이트
# 비율이 커져 틈이 다시 메워졌다(0.14) — 복셀 밀도만 올리면 되는 게 아니라 데시메이트 비율도 2배 안쪽이어야 한다.
# 삼각형 2.8만은 세 시드 전패, 1.1만~1.6만은 성공.
QF_INPUT_LADDER = ((32000, 16000), (32000, 11000), (20000, 8000))
QF_INPUT_FACES = 14000         # 복셀 한 변을 표면적에서 역산할 때의 기준 면수(_voxel_size 초기값)
VOXEL_TARGET_RATIO = 2.5       # 복셀 리메시가 노릴 면수 = 목표 x 이 비율
VOXEL_MAX_RATIO = 6.0          # 이 배수를 넘으면 데시메이트로 낮춘다 — 실측(2026-09-18): 입력/목표 28배에서
                               # QuadriFlow 가 메모리 폭주로 SIGKILL, 6~12배는 'Remeshing failed'
VOXEL_SIZE_MIN_DIV = 400.0     # 복셀 한 변의 하한 = 모델 크기 / 이 값
VOXEL_SIZE_MAX_DIV = 8.0       # 복셀 한 변의 상한 = 모델 크기 / 이 값
QF_MIN_RATIO = 0.3             # QuadriFlow 결과가 목표의 이 비율 미만이면 실패로 본다
QF_MAX_RATIO = 3.0             # 초과해도 실패
QF_EXTRA_SHELLS = 2            # QuadriFlow 출력 셸 수가 입력보다 이만큼 넘게 많으면 조각난 출력으로 본다
QF_HALF_MAX_SHELLS = 24        # 반쪽 출력은 절단면을 따라 패치가 나뉘어 셸이 늘어난다(실측 2026-09-22: 18개).
                               # 미러 뒤 remove_fragments 가 부스러기를 걷어내므로 이 정도는 받아들인다
QF_MAX_BOUNDARY = 0.1          # 대칭면 밖의 구멍 엣지가 전체 엣지의 이 비율을 넘으면 깨진 출력으로 본다
                               # (정상 출력은 0~3%, 실측 2026-09-21 비대칭 시드 1: 702/28k)
QF_MAX_EXTENT_RATIO = 1.5      # 출력 경계 상자가 입력 크기의 이 배수를 넘으면 좌표가 폭주한 출력이다
QF_MAX_MEDIAN_EDGE_RATIO = 0.1 # 중앙 엣지 길이가 모델 크기의 이 비율을 넘어도 폭주다 (실측 2026-09-23, 고블린
                               # 절단 링 반쪽: NaN 없이 중앙 엣지 0.83 / 모델 1.8 인 출력이 검사를 통과했다)
QF_MAX_EDGE_RATIO = 10.0       # 중앙 엣지의 이 배수를 넘는 엣지가 있으면 튄 정점이 있는 출력이다 (갱스터: 옷깃에서 뻗은 가시)
HOLE_MAX_EDGES = 64            # 이보다 큰 구멍이 있으면 표면을 덮지 못한 것으로 보고 출력을 버린다.
                               # 셰이프 서버 결과는 셸이 수천 개라(실측 2026-09-22: 1,968개) 40~50엣지짜리
                               # 구멍이 정상 출력에도 남는다 — 16으로 조이면 쓸 만한 결과까지 전부 버린다
PLANE_MARGIN_BANDS = 4.0       # 대칭면을 가로지르지 않는 링은 대칭면에서 띠 반폭의 이 배수 넘게 떨어져 있어야 반쪽에서 자른다
BAND_STRAY_RATIO = 0.05        # 띠 경계 루프 정점 중 이 비율(최소 BAND_STRAY_MIN 개)까지는 띠 밖으로 떠도 띠 경계로 본다
BAND_STRAY_MIN = 2
BAND_LOOP_MAX_RATIO = 3.0      # 절단 띠 안의 경계 루프가 예상 링 둘레(엣지 수)의 이 배수를 넘으면 구멍으로 본다
MIN_QUAD_RATIO = 0.95          # 출력의 쿼드 비율 하한 — 나머지는 _repair_output 이 삼각화·병합으로 되돌린다
# 대칭(반쪽) 경로 전용 사다리 — 반쪽은 형상의 절반만 담으므로 전체와 같은 삼각형 수를 넣으면 QuadriFlow 가
# 두 배로 촘촘한 와이어를 깔게 되고 그만큼 정지가 잦다. 실측(2026-09-22, 캐릭터 GLB): 반쪽 7,146면은 세 시드
# 모두 45초를 넘겨 정지, 3,827면·2,237면은 3초에 성공했다. 복셀 밀도는 그대로 두고(좁은 틈 보존) 데시메이트만
# 낮춘다 — 자르기 전에 한 번만 줄여야 절단면이 상하지 않는다(두 번 줄이면 비매니폴드 정점이 수십 개 생긴다).
QF_HALF_LADDER = ((32000, 8000), (32000, 5500), (20000, 4000))
QF_REQUEST_SCALE = 1.15        # QuadriFlow 에 요청할 면수 배수 — 실측(2026-09-20, 17회): 결과가 요청의
                               # 0.67~0.96배(평균 0.81)로 늘 모자라게 나와 목표를 그대로 넣으면 하한을 깬다
QF_HALF_REQUEST_SCALE = 1.0    # 경계를 보존한 반쪽 입력은 요청의 1.05~1.1배로 나와 보정이 필요 없다
PLANE_TOLERANCE_RATIO = 1e-3   # 출력 경계 루프가 모델 크기의 이 비율 안에 있으면 대칭 절단면 루프로 본다
SNAP_TOLERANCE_RATIO = 1e-5    # 절단 직후 이 비율 안의 정점만 평면에 붙인다 — 넓게 붙이면 미세 엣지가
                               # 생겨 QuadriFlow 사전 검사가 거절한다
WELD_RATIO = 1e-4              # 이 비율(모델 크기 기준)보다 짧은 엣지는 양 끝점을 합친다. 1.7m 모델에서 0.17mm
                               # — 형상에는 무의미하고, 남겨 두면 종횡비 수백짜리 퇴화 면이 된다
QF_TIMEOUT = 25.0              # 시도 하나의 기본 제한 시간(초) — 성공은 8~12초라 이보다 길면 정지로 본다
QF_TIMEOUT_PER_QUAD = 1.0 / 400  # 목표 쿼드 하나당 늘려 주는 시간(초)
SCORE_OUTPUTS = 2              # 링 접합 점수를 매겨 볼 QuadriFlow 출력 수 — 3개를 다 보면 실패 가이드가 있는 오거가 47초 → 198초
QF_ATTEMPTS = 3                # 한 밀도에서 시드를 바꿔 볼 횟수. 실측(2026-09-20): 같은 입력에서 시드 1·2 는 정지,
                               # 시드 3 은 9초 성공 — 비결정적이라 여러 시드를 차례로 본다
SHRINK_LIMIT = 3.0             # 노멀 투영 한계 = 복셀 한 변 x 이 배수 — 이보다 먼 표면으로는 끌려가지 않는다
RELAX_ROUNDS = 2               # 스무딩 → 재투영 반복 횟수
RELAX_FACTOR = 0.5
SLIVER_ASPECT = 8.0            # 이 종횡비를 넘는 면 주변만 골라 다시 편다 — 전체 스무딩은 형상을 뭉개면서도
                               # 짧은 엣지를 늘리지 못한다(실측 2026-09-21: 최대 종횡비 22.2 → 7.6)
SLIVER_ROUNDS = 20             # 슬리버 완화 반복 상한. 남는 몇 개는 형상이 실제로 접힌 곳이다
SYMMETRY_CENTER_RATIO = 0.05   # 메시가 X=0 에서 이 비율(폭 기준)보다 치우쳐 있으면 대칭을 끈다
UNWRAP_ANGLE = math.radians(66)
ISLAND_MARGIN = 0.003
CAGE_RATIO = 0.01              # 케이지 돌출 = 모델 크기 x 이 비율
RAY_RATIO = 0.02               # 최대 광선 거리 = 모델 크기 x 이 비율
BAKE_MARGIN = 8

# (키, 이미지 이름 꼬리, Non-Color 여부, 초기 색, Principled 입력 이름)
_MAP_PLAN = (
    ("basecolor", "베이스컬러", False, (0.8, 0.8, 0.8, 1.0), "Base Color"),
    ("metallic", "메탈릭", True, (0.0, 0.0, 0.0, 1.0), "Metallic"),
    ("roughness", "러프니스", True, (0.5, 0.5, 0.5, 1.0), "Roughness"),
    ("normal", "노멀", True, (0.5, 0.5, 1.0, 1.0), "Normal"),
)


def retopologize(source_obj, collection, target_faces=8000, symmetry=True,
                 texture_size=2048, normal_map=True, progress=None, stash=None,
                 ring_guides=()) -> dict:
    """source_obj 를 쿼드 메시로 다시 깔고 텍스처를 베이크로 옮긴다.

    원본은 `<이름>_원본` 으로 같은 컬렉션에 숨겨 남기고, 결과가 원래 이름을 이어받는다.
    progress 는 `progress("단계 설명")` 으로 불리는 선택적 콜백이다.

    stash 를 주면 **다시 리토폴로지**다 — 이미 보존된 원본(stash)에서 새 작업본을 만들고, source_obj
    (지난 결과)를 지우고 그 이름을 이어받는다. 보존본은 새로 만들지 않으며 실패해도 손대지 않는다.

    ring_guides 는 (이름, 월드 좌표 닫힌 점 열) 목록이다. 각 위치를 절단 링으로 써서 링 루프를 깐다.
    쓸 수 없거나 접합에 실패한 가이드는 빼고 계속하며 사유는 결과의 notes 에 남긴다."""
    started = time.perf_counter()
    say = progress or (lambda _text: None)
    base_name = source_obj.name
    own_stash = stash is None
    work = snapshot = None
    try:
        # 사용자가 편집 모드에 있으면 아래 오퍼레이터들이 전부 어긋난다 — 먼저 오브젝트 모드로 내린다
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if own_stash:
            say("원본 보존")
            stash = _stash_source(source_obj, collection)
        else:
            say("보존된 원본에서 다시 시작")

        say("작업본 생성")
        work = _duplicate(stash, base_name + "_리토폴로지", collection)
        work[SOURCE_KEY] = False
        del work[SOURCE_KEY]
        work.hide_render = False
        _set_hidden(work, False)

        # QuadriFlow 가 실패하면 여기(리메시 이전)로 되돌려 데시메이트한다
        snapshot = work.data.copy()

        # 치우친 모델에서 반쪽을 잘라 미러하면 형상이 통째로 어긋난다 — 그럴 때는 대칭을 포기한다
        if symmetry and not _centered_on_axis(work, 'X'):
            say("모델이 X=0 에 정렬돼 있지 않아 대칭을 끕니다")
            symmetry = False

        notes = []
        # 대칭 반쪽은 +X 로 미러·중복 제거한 링을, 닫힌 전체 폴백은 사용자가 둔 그대로의 링을 자른다
        requested, full_requested = _requested_cuts(work, ring_guides, target_faces, symmetry, notes)
        max_retries = len(full_requested)   # 실패한 가이드를 하나씩은 빼므로 가이드 수만큼이면 충분하다
        for first, second, gap in ring_cut.crowded_pairs(requested):
            notes.append(f"링 가이드 '{first}' 와 '{second}' 의 간격({gap:.3g})이 좁아 접합이 서로 간섭할 수 있습니다 "
                         "— 더 벌리거나 목표 면수를 올려 주세요")
        outcome = None
        seams = ()
        ladder = QF_HALF_LADDER if symmetry else QF_INPUT_LADDER
        attempt, retries, fresh = 0, 0, True
        while attempt < len(ladder):
            density, triangles = ladder[attempt]
            attempt += 1
            if not fresh:
                stale, work.data = work.data, snapshot.copy()
                bpy.data.meshes.remove(stale)
            fresh = False
            say(f"복셀 리메시 ({density:,}면 목표)")
            _voxel_remesh(work, target_faces, wanted=density)
            remove_fragments(work)
            say(f"데시메이트 ({triangles:,} 삼각형)")
            _decimate(work, triangles)
            say("메시 정리")
            if not make_manifold(work):
                say(f"정리 실패 {quadriflow_ready(work)}")
                continue
            say(f"쿼드 리토폴로지 시도 {attempt}/{len(ladder)}")
            # 같은 형상도 입력이 거칠수록 QuadriFlow 성공률이 크게 오른다(실측 2026-09-20:
            # 3.6만면 전패, 1.3만면 5시드 중 1승, 5천면 전승) — 실패하면 한 단계 낮춰 다시 굽는다
            outcome, cuts = _quadriflow(work, target_faces, symmetry, say,
                                        allow_full=attempt == len(ladder),
                                        requested_cuts=requested, full_cuts=full_requested, notes=notes)
            if outcome is None or not cuts:
                seams = ()
                if outcome is not None:
                    break
                continue
            say(f"절단 링 {len(cuts)}개 접합")
            plane = ('X',) if outcome == "HALF" else ()
            seams = ring_cut.stitch_bands(work.data, cuts, plane, _stitch_plane_tolerance(work) if plane else 0.0)
            # 반쪽 출력은 대칭면을 가로지르는 링(목)이 열린 호로 남는 것이 정상이다 — 미러가 닫는다
            failed = [seam for seam in seams
                      if not seam.bridged or (not seam.closed and outcome != "HALF")]
            if not failed:
                break
            if retries >= max_retries:
                raise RuntimeError(f"링 가이드 '{failed[0].name}' 의 절단 링을 접합하지 못했습니다 "
                                   f"({failed[0].note or '링이 닫히지 않음'})")
            # 접합 못 한 가이드는 빼고 같은 밀도를 다시 돈다 — 나머지 가이드의 링은 살린다
            for seam in failed:
                notes.append(f"링 가이드 '{seam.name}' 접합 실패({seam.note or '링이 닫히지 않음'}, "
                             f"양쪽 {seam.ring_sizes[0]}·{seam.ring_sizes[1]}정점) — 빼고 다시 깔았습니다. "
                             "단면이 일정한 위치로 옮겨 주세요")
            names = {seam.name for seam in failed}
            requested = tuple(cut for cut in requested if cut.name not in names)
            full_requested = tuple(cut for cut in full_requested if cut.name not in names)
            retries += 1
            attempt -= 1
            outcome, seams = None, ()
        for seam in seams:
            notes.append(f"링 '{seam.name}': 절단 링 {seam.ring_sizes[0]}·{seam.ring_sizes[1]}정점 접합"
                         + (f" (전이 삼각형 {seam.triangles}개)" if seam.triangles else ""))
        plane_axes = ()
        if outcome is not None:
            method = "QUADRIFLOW"
            # 반쪽을 깐 결과는 대칭면이 열려 있다 — 투영까지 끝낸 뒤 미러로 닫는다
            plane_axes = ('X',) if outcome == "HALF" else ()
            bpy.data.meshes.remove(snapshot)
            say("출력 정리")
            _repair_output(work, plane_axes)
        else:
            say("QuadriFlow 실패 — 데시메이트로 전환")
            stale, work.data = work.data, snapshot
            bpy.data.meshes.remove(stale)
            work.data.name = work.name
            _decimate(work, target_faces)
            method = "DECIMATE"

        with _visible(stash):
            say("표면 맞춤")
            _shrinkwrap(work, stash, plane_axes)
            if plane_axes:
                say("대칭면 미러 용접")
                # 구멍 메우기가 대칭면 옆에 남긴 웹의 내부 정점이 평면 위에 있으면 미러가 거울상과 용접해
                # 면 4개짜리 엣지가 된다(3DRemesher 실측: 목 링 둘에서 3개) — 내부 정점만 살짝 띄운다
                _lift_interior_plane_vertices(work, plane_axes)
                _mirror(work, plane_axes)
                remove_fragments(work)   # 반쪽 출력에서 떨어져 나온 부스러기 패치를 걷어낸다
                make_manifold(work)      # 미러 뒤 남은 구멍·겹친 면을 닫는다 (결과는 삼각형)
                _repair_output(work)     # 그 삼각형을 다시 쿼드로 합친다
            say("UV 언랩")
            _unwrap(work)
            say("머티리얼 준비")
            images, nodes = _prepare_material(work, base_name, texture_size, normal_map)
            _bake_maps(stash, work, nodes, normal_map, say)
            _connect_material(nodes)

        say("마무리")
        _replace_source(source_obj, work, base_name)
        work.data.calc_loop_triangles()
        polygons = work.data.polygons
        return {
            "obj": work,
            "faces": len(polygons),
            "quads": sum(1 for p in polygons if len(p.vertices) == 4),
            "tris": len(work.data.loop_triangles),
            "method": method,
            "symmetry_error": round(symmetry_error(work), 6) if symmetry else None,
            "images": sorted(image.name for image in images.values()),
            "source_name": stash.name,
            "seconds": round(time.perf_counter() - started, 1),
            "rings": sum(1 for seam in seams if seam.bridged),
            "notes": notes,
        }
    except Exception:
        # 실패해도 원본은 그대로 둔다 — 중간 산물만 걷어낸다 (다시 리토폴로지면 보존본은 남긴다)
        for leftover in (work, stash if own_stash else None):
            if leftover is not None:
                _discard(leftover)
        if snapshot is not None and snapshot.name in bpy.data.meshes and snapshot.users == 0:
            bpy.data.meshes.remove(snapshot)
        raise


# --- 오브젝트 준비 ---------------------------------------------------------

def _duplicate(obj, name: str, collection):
    """메시까지 복제해 컬렉션에 링크한 새 오브젝트."""
    copy = obj.copy()
    copy.data = obj.data.copy()
    copy.name = safe_id_name(name)
    copy.data.name = copy.name
    copy.modifiers.clear()
    collection.objects.link(copy)
    return copy


def _stash_source(obj, collection):
    """리토폴로지 전 메시를 `<이름>_원본` 으로 숨겨 남긴다 — 베이크 소스이자 되돌릴 기준이다."""
    stash = _duplicate(obj, obj.name + SOURCE_SUFFIX, collection)
    stash[SOURCE_KEY] = True
    stash.hide_render = True
    _set_hidden(stash, True)
    return stash


def _discard(obj) -> None:
    mesh = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _replace_source(source_obj, work, name: str) -> None:
    """원본 오브젝트를 지우고 작업본이 그 이름을 이어받게 한다."""
    _discard(source_obj)
    work.name = safe_id_name(name)
    work.data.name = work.name


def _set_hidden(obj, hidden: bool) -> None:
    # hide_set 은 뷰 레이어가 필요하다 — 백그라운드에서 오브젝트가 아직 링크되기 전이면 조용히 넘어간다
    try:
        obj.hide_set(bool(hidden))
    except RuntimeError:
        pass
    obj.hide_viewport = bool(hidden)


@contextmanager
def _visible(obj):
    """보존본을 잠시 드러낸다 — 슈링크랩 타깃과 베이크 소스는 숨겨져 있으면 쓰이지 않는다."""
    render = obj.hide_render
    obj.hide_render = False
    _set_hidden(obj, False)
    try:
        yield obj
    finally:
        _set_hidden(obj, True)
        obj.hide_render = render


@contextmanager
def _override(obj):
    """오퍼레이터가 이 오브젝트에만 걸리도록 뷰 레이어의 활성·선택까지 실제로 바꾼다.

    temp_override(active_object=...) 만으로는 부족하다 — 실측(2026-09-20): mode_set 은 뷰 레이어의
    활성 오브젝트를 보므로, 씬에 다른 오브젝트(기본 큐브 등)가 활성인 상태에서는 그쪽이 편집 모드로
    들어가 UV 언랩이 조용히 빗나갔고 베이크가 '활성 UV 레이어 없음'으로 터졌다.
    백그라운드에는 창이 없으므로 window 는 있을 때만 넣는다."""
    view_layer = bpy.context.view_layer
    previous = view_layer.objects.active
    restore = [o for o in view_layer.objects if _select(o, False)]
    _select(obj, True)
    view_layer.objects.active = obj
    kwargs = dict(object=obj, active_object=obj, selected_objects=[obj],
                  selected_editable_objects=[obj])
    windows = bpy.context.window_manager.windows if bpy.context.window_manager else None
    if windows:
        kwargs["window"] = windows[0]
    try:
        with bpy.context.temp_override(**kwargs):
            yield
    finally:
        _select(obj, False)
        for other in restore:
            _select(other, True)
        try:
            view_layer.objects.active = previous
        except (ReferenceError, RuntimeError):
            pass   # 그 사이 지워진 오브젝트 — 활성 지정은 포기해도 된다


def _select(obj, state: bool) -> bool:
    """선택 상태를 바꾸고 바뀌기 전 값을 돌려준다. 숨겨진 오브젝트는 선택할 수 없으므로 무시한다."""
    try:
        was = obj.select_get()
        obj.select_set(bool(state))
        return was
    except RuntimeError:
        return False


def _model_size(obj) -> float:
    """월드 기준 경계 상자의 가장 긴 변.

    bound_box 는 캐시라 메시를 직접 변환한 직후에는 옛 값이다 — 읽기 전에 갱신한다
    (임포트의 키 정규화가 좌표를 메시에 굽는다)."""
    bpy.context.view_layer.update()
    points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return max(max(p[a] for p in points) - min(p[a] for p in points) for a in range(3))


# --- 토폴로지 --------------------------------------------------------------

def quadriflow_ready(obj, plane_axes: tuple = (), cuts: tuple = ()) -> dict:
    """QuadriFlow 사전 검사와 같은 기준으로 메시 상태를 센다. 모두 0 이어야 통과한다.

    Blender 는 ① 면이 2개가 아닌 엣지, ② 이웃 면의 winding 불일치, ③ **길이 1e-4 미만 엣지**를 모두
    거절 사유로 본다(source/blender/editors/object/object_remesh.cc). 셋째 조건 때문에 눈으로도
    bmesh 로도 멀쩡한 메시가 계속 거절당했다.

    plane_axes 를 주면 그 대칭 절단면의 경계는 세지 않는다 — 반쪽 메시는 거기가 열려 있는 것이 정상이고,
    QuadriFlow 도 use_preserve_boundary 로 그 테두리를 받아들인다. cuts 를 주면 절단 띠 경계도 같이 뺀다."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    plane_edges, plane_verts = _plane_boundary(bm, plane_axes, _local_size(obj), cuts)
    report = {
        "tiny": sum(1 for e in bm.edges
                    if all(abs(e.verts[0].co[i] - e.verts[1].co[i]) < 1e-4 for i in range(3))),
        "open": sum(1 for e in bm.edges if len(e.link_faces) == 1 and e not in plane_edges),
        "edge": sum(1 for e in bm.edges if not e.is_manifold and e not in plane_edges),
        "vert": sum(1 for v in bm.verts if not v.is_manifold and v not in plane_verts),
        "wind": sum(1 for e in bm.edges if not e.is_contiguous and e not in plane_edges),
    }
    bm.free()
    return report


def _plane_boundary(bm, plane_axes: tuple, scale: float, cuts: tuple = ()):
    """대칭 절단면(과 절단 띠) 위에 있는 경계 엣지·정점 집합. 둘 다 없으면 빈 집합이다."""
    if not plane_axes and not cuts:
        return set(), set()
    edges = set()
    for loop in _boundary_loops(bm):
        if _loop_on_plane(loop, plane_axes, scale) or _loop_in_bands(loop, plane_axes, scale, cuts, False):
            edges.update(loop)
    return edges, {vertex for edge in edges for vertex in edge.verts}


def make_manifold(obj, rounds: int = REPAIR_ROUNDS, plane_axes: tuple = (), cuts: tuple = ()) -> bool:
    """QuadriFlow 가 받아들이는 상태로 만든다. 성공 여부를 돌려준다.

    한 번에 하나씩 고치면 서로를 되살린다 — 미세 엣지를 늘리고, 엣지에 셋 이상 붙은 면 중 **초과분만**
    지우고, 그때 생긴 구멍을 메우고, 뜬 정점을 걷어내고 노멀을 다시 맞추는 것을 한 묶음으로 돌린다.
    (초기 구현처럼 비매니폴드 엣지 주변 면을 통째로 지우면 경계가 폭증해 되레 악화된다).

    미세 엣지는 **녹이지 않고 정점을 밀어 늘린다** — dissolve 로 정점을 합치면 나비 매듭 정점(면 부채
    두 개가 한 점을 공유)이 생기고, 그 정점을 지우거나 떼는 어떤 수리도 새 비매니폴드를 낳아 라운드가
    수렴하지 않았다(실측 2026-09-21). 0.2mm 이동은 형상에 무의미하고 QuadriFlow 는 짧은 엣지를 잘 다룬다."""
    for _ in range(max(rounds, 1)):
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        _stretch_tiny_edges(bm)
        extra = []
        for edge in bm.edges:
            if len(edge.link_faces) > 2:
                keep = sorted(edge.link_faces, key=lambda f: -f.calc_area())[:2]
                extra += [f for f in edge.link_faces if f not in keep]
        if extra:
            bmesh.ops.delete(bm, geom=list(set(extra)), context='FACES')
        # 대칭 절단면의 테두리는 메우지 않는다 — 여기를 닫으면 반쪽을 미러할 수 없다
        scale = _local_size(obj)
        border = _open_border(bm, plane_axes, scale, cuts)
        if border:
            bmesh.ops.holes_fill(bm, edges=border, sides=64)
            border = _open_border(bm, plane_axes, scale, cuts)
            if border:
                bmesh.ops.triangle_fill(bm, edges=border, use_beauty=True, use_dissolve=False)
        loose = [v for v in bm.verts if not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context='VERTS')
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        if not any(quadriflow_ready(obj, plane_axes, cuts).values()):
            return True
    return False


def _open_border(bm, plane_axes: tuple, scale: float, cuts: tuple = ()) -> list:
    """메워야 할 경계 엣지 — 대칭 절단면과 절단 띠의 테두리는 뺀다."""
    plane_edges, _ = _plane_boundary(bm, plane_axes, scale, cuts)
    return [e for e in bm.edges if len(e.link_faces) == 1 and e not in plane_edges]


def _stretch_tiny_edges(bm) -> int:
    """QuadriFlow 사전 검사가 '길이 0'으로 보는 엣지(축별 차이 1e-4 미만)를 MERGE_DIST 까지 늘린다.

    한쪽 정점을 엣지 방향으로 민다. 두 정점이 완전히 겹쳐 방향이 없으면 정점 노멀 방향으로 띄운다."""
    moved = 0
    for edge in bm.edges:
        a, b = edge.verts
        if not all(abs(a.co[i] - b.co[i]) < 1e-4 for i in range(3)):
            continue
        direction = b.co - a.co
        if direction.length_squared < 1e-16:
            direction = b.normal if b.normal.length_squared > 0 else Vector((0.0, 0.0, 1.0))
        direction.normalize()
        b.co = a.co + direction * MERGE_DIST
        moved += 1
    return moved


def remove_fragments(obj, min_ratio: float = FRAGMENT_RATIO) -> int:
    """전체 면수의 min_ratio 미만인 떨어진 셸을 지운다. 지운 셸 수를 돌려준다.

    촘촘한 복셀 리메시는 좁은 틈(손가락 사이·옷 주름)에 작은 거품 셸을 수십 개 남긴다(실측 2026-09-21:
    5.6만면에서 108개). 데시메이트가 이를 뭉개면 비매니폴드가 되고 QuadriFlow 입력에도 쓸모가 없다."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    seen = set()
    shells = []
    for face in bm.faces:
        if face.index in seen:
            continue
        stack, shell = [face], []
        while stack:
            cur = stack.pop()
            if cur.index in seen:
                continue
            seen.add(cur.index)
            shell.append(cur)
            for edge in cur.edges:
                stack.extend(f for f in edge.link_faces if f.index not in seen)
        shells.append(shell)
    if len(shells) <= 1:
        bm.free()
        return 0
    threshold = max(int(len(bm.faces) * min_ratio), 1)
    small = [shell for shell in shells if len(shell) < threshold]
    if not small or len(small) == len(shells):
        bm.free()
        return 0
    bmesh.ops.delete(bm, geom=[f for shell in small for f in shell], context='FACES')
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return len(small)


def _voxel_size(obj, target_faces: int) -> float:
    """복셀 리메시 결과가 QF_INPUT_FACES 근처가 되도록 표면적에서 역산한 복셀 한 변.

    QuadriFlow 는 입력이 클수록 실패·정지 확률이 급격히 오른다(실측 2026-09-20: 3.6만면 3전 3패,
    1.5만면 3전 2승, 5천면 전승). 최종 목표 면수와 무관하게 입력 밀도를 이 범위로 맞춘다."""
    area = sum(p.area for p in obj.data.polygons)
    wanted = max(float(QF_INPUT_FACES), float(target_faces))
    size = _model_size(obj)
    voxel = math.sqrt(max(area, 1e-9) / wanted)
    return min(max(voxel, size / VOXEL_SIZE_MIN_DIV), size / VOXEL_SIZE_MAX_DIV)


def _voxel_remesh(obj, target_faces: int, wanted: float = 0.0) -> None:
    """복셀 리메시로 열린 셸·겹친 면을 하나의 닫힌 표면으로 녹인다.

    컬링된 셸은 테두리가 열려 있지만 복셀 리메시가 그대로 닫아 준다 — 예전처럼 Solidify 로 두께를
    먼저 주면 그 얇은 벽이 복셀보다 얇아 오히려 구멍이 남았다(실측 2026-09-20).

    면수는 복셀 한 변으로 정확히 예측되지 않아(같은 공식으로 1.4만을 노렸는데 3.3만이 나왔다)
    **재어 보고 복셀을 조정한다.** QuadriFlow 는 입력이 2만면을 넘으면 그대로 실패한다."""
    wanted = wanted or max(float(QF_INPUT_FACES), float(target_faces))
    voxel = _voxel_size(obj, target_faces)
    size = _model_size(obj)
    original = obj.data                      # 매번 원본에서 다시 리메시한다 — 리메시 결과를 또 리메시하면
    obj.data = original.copy()               # 표면이 뭉개져 QuadriFlow 가 실패한다(실측 2026-09-20)
    for _ in range(3):
        obj.data.remesh_voxel_size = voxel
        obj.data.remesh_voxel_adaptivity = 0.0
        obj.data.use_remesh_fix_poles = False   # 극점 정리 단계가 비매니폴드를 남긴다
        with _override(obj):
            bpy.ops.object.voxel_remesh()
        faces = len(obj.data.polygons)
        if wanted * 0.6 <= faces <= wanted * 1.4 or faces == 0:
            break
        # 면수는 복셀 한 변의 제곱에 반비례한다 — 그 비율로 보정해 원본에서 다시 굽는다
        voxel = min(max(voxel * math.sqrt(faces / wanted), size / VOXEL_SIZE_MIN_DIV),
                    size / VOXEL_SIZE_MAX_DIV)
        stale, obj.data = obj.data, original.copy()
        bpy.data.meshes.remove(stale)
    if original.users == 0:
        bpy.data.meshes.remove(original)


def _quadriflow(obj, target_faces: int, symmetry: bool, say=lambda _text: None, allow_full: bool = True,
                requested_cuts: tuple = (), full_cuts=None, notes=None):
    """자식 Blender 프로세스에서 QuadriFlow 를 돌려 결과 메시로 바꾼다.

    대칭이면 **양의 반쪽(X>=0)만 잘라** 경계를 보존한 채 깔고 "HALF" 를 돌려준다 — 호출자가 미러로 용접한다.
    QuadriFlow 자체의 대칭 모드는 쓰지 않는다: 어떤 시드·옵션에서도 대칭면을 따라 수십 엣지짜리 구멍을
    남기거나 정점이 NaN 인 메시를 내놓았다. 반쪽을 깔고 미러하면 구멍 없이 **정확히** 대칭인 결과가 나온다.
    반쪽이 모든 시드에서 실패하면 닫힌 전체로 한 번 더 보고 "FULL" 을 돌려준다. 전부 실패하면 None.

    같은 프로세스에서 돌리지 않는 이유: QuadriFlow 는 내부 멀티스레딩이 비결정적이라 같은 입력·같은
    시드에도 결과가 매번 다르고, 드물게 FixValence() 안에서 영영 끝나지 않는다. bpy.ops 는 중간에
    끊을 수 없어 Blender 전체가 얼어붙으므로, 자식 프로세스여야 시간 초과에 죽일 수 있다.

    실패는 **시드를 바꿔 계속 재시도한다.** 실측(2026-09-20, 같은 입력 1.5만면): 시드 1·2 는 8초 성공,
    시드 3 은 60초 초과. 빨리 끝나는 실패(내부 오류)도 다른 시드에서는 성공하므로 종료 코드를 보고
    포기하면 안 된다.

    requested_cuts 가 있으면 QuadriFlow 에 보내기 직전(매니폴드 수리 뒤) 그 위치에 띠를 지우고 경계 보존으로
    깐다. full_cuts 는 닫힌 전체 경로가 쓸 미러 전 절단 링이다(없으면 requested_cuts).
    (결과 종류, 실제로 자른 절단 링) 을 돌려준다 — 쓸 수 없는 가이드의 사유는 notes 에 쌓는다."""
    scale = _local_size(obj)
    notes = notes if notes is not None else []
    with tempfile.TemporaryDirectory(prefix="lp3d_qf_") as work_dir:
        if symmetry:
            # 마지막 단에서는 링을 넣은 반쪽이 실패하면 링 없이 반쪽을 한 번 더 본다 — 닫힌 전체로 떨어지면
            # 대칭이 깨지므로 사용자가 요청한 대칭을 링보다 우선한다
            variants = (requested_cuts, ()) if requested_cuts and allow_full else (requested_cuts,)
            for variant in variants:
                if variant is not requested_cuts:
                    say("링을 넣은 대칭 반쪽 실패 — 링 없이 대칭 반쪽 재시도")
                outcome = _half_attempt(obj, target_faces, scale, work_dir, variant, say, notes)
                if outcome is False:
                    break   # 반쪽 자체를 만들 수 없다
                if outcome is not None:
                    if variant is not requested_cuts:
                        notes.append("링을 넣은 대칭 반쪽이 모든 시드에서 실패해 링 없이 대칭으로 깔았습니다")
                    return "HALF", outcome
            else:
                if not allow_full:
                    say("대칭 반쪽 실패 — 다음 밀도로")
                    return None, ()  # 전체 메시 시도는 마지막 단에서만 — 정지하면 시드마다 수십 초를 버린다
                say("대칭 반쪽 실패 — 닫힌 전체로 다시 시도")
        if symmetry and not allow_full:
            return None, ()
        # 띠는 복사본에 자른다 — 실패해도 작업본은 다음 밀도가 스냅샷에서 다시 굽는다
        full = obj.data.copy()
        holder = bpy.data.objects.new(full.name, full)
        try:
            cuts = _cut_bands(holder, requested_cuts if full_cuts is None else full_cuts, scale, say, notes, ())
            max_shells = _shell_count(full) + QF_EXTRA_SHELLS
            request = max(int(target_faces * QF_REQUEST_SCALE), 4)
            src = os.path.join(work_dir, "in.blend")
            bpy.data.libraries.write(src, {full}, fake_user=True)
        finally:
            bpy.data.objects.remove(holder)
            bpy.data.meshes.remove(full)
        mesh = _race_quadriflow(src, work_dir, request, expected=target_faces,
                                preserve_boundary=bool(cuts), plane_axes=(), scale=scale,
                                max_shells=max_shells, cuts=cuts,
                                attempts=2 if symmetry else QF_ATTEMPTS,
                                score=(lambda m: _stitch_score(m, cuts, (), 0.0)) if cuts else None, perfect=len(cuts))
        if mesh is None:
            return None, ()
        _swap_mesh(obj, mesh)
        return "FULL", cuts


def _half_attempt(obj, target_faces: int, scale: float, work_dir: str, requested_cuts: tuple, say, notes: list):
    """양의 반쪽(X>=0)을 잘라 링 띠를 내고 경계 보존 QuadriFlow 로 깐다. 성공하면 작업본 메시를 바꾸고 실제로 자른
    절단 링(없으면 빈 튜플)을, 시드가 모두 실패하면 None 을, 반쪽을 만들 수 없으면 False 를 돌려준다."""
    half = _clip_positive(obj.data, ('X',), scale)
    if half is None:
        say("양의 반쪽에 면이 없어 대칭을 건너뜁니다")
        return False
    mesh, cuts = None, ()
    # 절단은 원본이 멀쩡해도 교차점에 비매니폴드 엣지·정점을 남긴다 — 그대로 보내면
    # QuadriFlow 가 "manifold 가 아니다"라며 아무것도 하지 않는다(실측 2026-09-22).
    holder = bpy.data.objects.new(half.name, half)
    try:
        if not make_manifold(holder, plane_axes=('X',)):
            say(f"반쪽 정리 실패 {quadriflow_ready(holder, ('X',))}")
        else:
            cuts = _cut_bands(holder, requested_cuts, scale, say, notes, ('X',))
            # 띠 하나가 팔·다리를 끊을 때마다 셸이 하나씩 는다
            max_shells = max(_shell_count(holder.data) + QF_EXTRA_SHELLS, QF_HALF_MAX_SHELLS + len(cuts))
            request = max(int(target_faces / 2 * QF_HALF_REQUEST_SCALE), 4)
            src = os.path.join(work_dir, "half.blend")
            bpy.data.libraries.write(src, {holder.data}, fake_user=True)
            mesh = _race_quadriflow(src, work_dir, request, expected=request,
                                    preserve_boundary=True, plane_axes=('X',), scale=scale,
                                    max_shells=max_shells, cuts=cuts, perfect=len(cuts),
                                    score=(lambda m: _stitch_score(m, cuts, ('X',), _plane_tolerance_of(m, scale))) if cuts else None)
    finally:
        bpy.data.objects.remove(holder)
        bpy.data.meshes.remove(half)
    if mesh is None:
        return None
    _swap_mesh(obj, mesh)
    return cuts


def _stitch_plane_tolerance(obj) -> float:
    """접합 때 대칭면 위 경계로 볼 거리 — QuadriFlow 경계 정점은 엣지 길이 절반 가까이 평면에서 뜬다."""
    return _plane_tolerance_of(obj.data, _local_size(obj))


def _plane_tolerance_of(mesh, scale: float) -> float:
    vertices = mesh.vertices
    lengths = sorted((vertices[e.vertices[0]].co - vertices[e.vertices[1]].co).length for e in mesh.edges)
    median = lengths[len(lengths) // 2] if lengths else 0.0
    return max(PLANE_TOLERANCE_RATIO * scale, 0.6 * median)


def _cut_bands(holder, requested_cuts: tuple, scale: float, say, notes: list, plane_axes: tuple) -> tuple:
    """링 가이드 위치에 절단 띠를 지운다. 실제로 자른 절단 링을 돌려주고 건너뛴 사유는 notes 에 쌓는다.

    셰이프 서버 메시는 구멍 메우기로 이어 붙인 표면이라 띠를 자르면 면 3장짜리 엣지·와인딩 불일치가 남아
    QuadriFlow 가 아무것도 하지 않는다(실측 2026-09-23, 고블린: 42개, 종료 코드 3). 띠 경계는 열어 둔 채
    다시 수리하고, 그래도 안 되면 띠 없이 돌려 기존 결과라도 낸다."""
    if not requested_cuts:
        return ()
    say(f"링 가이드 {len(requested_cuts)}개 위치에 절단 띠 생성")
    original = holder.data.copy()
    # 미세 엣지는 뒤의 make_manifold 가 늘려 준다 — 병합·삼각화는 구멍 메우기 면과 엉켜 비매니폴드를 만든다
    cuts, skipped = ring_cut.cut_bands(holder.data, requested_cuts, scale, 0.0, triangulate=False)
    for reason in skipped:
        if reason not in notes:
            notes.append(reason)
    if cuts and not make_manifold(holder, plane_axes=plane_axes, cuts=cuts):
        note = f"절단 띠를 낸 메시를 QuadriFlow 입력으로 수리하지 못해 링 없이 깝니다 {quadriflow_ready(holder, plane_axes, cuts)}"
        say(note)
        if note not in notes:
            notes.append(note)
        # 호출자가 이 메시 데이터블록을 계속 쥐고 있으므로 갈아 끼우지 않고 기하만 되돌려 쓴다
        bm = bmesh.new()
        bm.from_mesh(original)
        bm.to_mesh(holder.data)
        bm.free()
        holder.data.update()
        cuts = ()
    bpy.data.meshes.remove(original)
    return cuts


def _requested_cuts(work, ring_guides, target_faces: int, symmetry: bool, notes=None) -> tuple:
    """월드 좌표 링 가이드를 작업본 로컬 절단 링으로 바꾼다. (반쪽용, 전체용) 을 돌려준다 — 대칭이면 반쪽용은
    음의 쪽 가이드를 양의 쪽으로 미러하고 중복을 합친다. 띠 반폭은 출력 엣지 길이(표면적 / 목표 면수)의 절반이다."""
    if not ring_guides:
        return (), ()
    to_local = work.matrix_world.inverted()
    guides = [(name, [tuple(to_local @ Vector(point)) for point in points]) for name, points in ring_guides]
    area = sum(polygon.area for polygon in work.data.polygons)
    edge = math.sqrt(max(area, 1e-12) / max(int(target_faces), 1))
    # 띠 폭 = 출력 엣지 하나. 엣지 두 개로 넓히면 오거(014)에서 접합이 5개 중 1개로 줄었다(실측 2026-09-23)
    cuts = ring_cut.ring_cuts(guides, edge)
    if not symmetry:
        return cuts, cuts
    half = []
    for cut in ring_cut.mirror_cuts(cuts, ('X',)):
        # 대칭면을 가로지르지도, 떨어져 있지도 않은 링(가랑이에 닿은 허벅지)은 반쪽 출력에서 띠 경계가 대칭면 경계와
        # 불규칙하게 뭉쳐 모든 시드가 큰 구멍으로 버려졌다(실측 2026-09-23, 갱스터) — 반쪽에서는 자르지 않는다
        straddles = abs(cut.center[0]) < cut.radius * ring_cut.STRADDLE_RATIO
        if not straddles and min(abs(p[0]) for p in cut.points) < cut.half_width * PLANE_MARGIN_BANDS:
            if notes is not None:
                notes.append(f"링 가이드 '{cut.name}' 이 대칭면(X=0)에 닿아 대칭 리토폴로지에서는 쓰지 않았습니다 "
                             "— 축 오프셋으로 대칭면에서 조금 떨어뜨려 주세요")
            continue
        half.append(cut)
    return tuple(half), cuts


def _swap_mesh(obj, mesh) -> None:
    """오브젝트의 메시를 새 메시로 갈아 끼우고 옛 메시를 지운다."""
    mesh.use_fake_user = False
    stale, obj.data = obj.data, mesh
    bpy.data.meshes.remove(stale)
    obj.data.name = obj.name


def _local_size(obj) -> float:
    """로컬 좌표 기준 경계 상자의 가장 긴 변 — 평면 허용치·용접 거리는 전부 이 값의 비율로 잡는다.

    _model_size 와 달리 월드 행렬을 거치지 않는다: 절단·스냅·미러는 전부 로컬 좌표에서 돈다."""
    coords = [v.co for v in obj.data.vertices]
    if not coords:
        return 0.0
    return max(max(c[a] for c in coords) - min(c[a] for c in coords) for a in range(3))


def _centered_on_axis(obj, axis: str = 'X', ratio: float = SYMMETRY_CENTER_RATIO) -> bool:
    """메시가 그 축의 0 평면을 기준으로 놓여 있는지.

    치우친 모델에서 반쪽을 잘라 미러하면 형상이 통째로 어긋난다 — 그럴 때는 대칭을 포기하는 편이 낫다."""
    component = "XYZ".index(axis)
    values = [v.co[component] for v in obj.data.vertices]
    if not values:
        return False
    low, high = min(values), max(values)
    width = high - low
    return width > 0.0 and abs(low + high) <= width * ratio


def _clip_positive(mesh, axes: tuple, scale: float):
    """대칭 축마다 양의 반쪽만 남긴 새 메시. 절단면 정점은 정확히 평면에 놓는다. 남는 면이 없으면 None."""
    tolerance = SNAP_TOLERANCE_RATIO * scale
    bm = bmesh.new()
    bm.from_mesh(mesh)
    for axis in axes:
        component = "XYZ".index(axis)
        normal = [0.0, 0.0, 0.0]
        normal[component] = 1.0
        bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:],
                               plane_co=(0.0, 0.0, 0.0), plane_no=tuple(normal),
                               clear_inner=True, clear_outer=False, dist=1e-6)
    # 절단이 남긴 미세 엣지는 QuadriFlow 사전 검사에 걸린다(실측: 17개 때문에 시드 전패).
    # 아주 작은 모델에서 형상을 무너뜨리지 않도록 모델 크기 기준으로도 자른다.
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=min(MERGE_DIST, 1e-3 * scale))
    for vertex in bm.verts:
        for axis in axes:
            component = "XYZ".index(axis)
            if abs(vertex.co[component]) < tolerance:
                vertex.co[component] = 0.0
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context='VERTS')
    if not bm.faces:
        bm.free()
        return None
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    half = bpy.data.meshes.new(mesh.name + "_half")
    bm.to_mesh(half)
    bm.free()
    half.update()
    return half


def _qf_output_ok(mesh, expected: int, max_shells: int, plane_axes: tuple, scale: float,
                  cuts: tuple = ()) -> bool:
    """QuadriFlow 출력이 쓸 수 있는 메시인지 — NaN 정점·비쿼드·큰 구멍·조각남·면수 이탈을 거른다.

    QuadriFlow 는 종료 코드 0 으로 끝나면서도 정점 대부분이 NaN 인 메시나, 표면을 덮지 못하고 닫힌 조각
    수십 개로 흩어진 메시(실측 2026-09-21 갱스터.001 비대칭: 입력 2셸 → 출력 20셸)를 내놓을 수 있다.
    그대로 쓰면 슈링크랩·베이크가 조용히 망가져 구멍 뚫린 형상이 된다.
    대칭 절단면 루프와 절단 띠 경계는 구멍이 아니다 — 미러와 링 접합이 닫아 준다."""
    faces = len(mesh.polygons)
    if not (expected * QF_MIN_RATIO <= faces <= expected * QF_MAX_RATIO):
        return False   # 면수가 목표와 크게 어긋난 결과는 쓰지 않는다
    if any(not all(math.isfinite(c) for c in v.co) for v in mesh.vertices):
        return False
    quads = sum(1 for polygon in mesh.polygons if len(polygon.vertices) == 4)
    if quads < faces * MIN_QUAD_RATIO:
        return False
    coords = [v.co for v in mesh.vertices]
    extent = max(max(c[a] for c in coords) - min(c[a] for c in coords) for a in range(3)) if coords else 0.0
    if extent > scale * QF_MAX_EXTENT_RATIO:
        return False   # 좌표가 폭주한 출력 — NaN 은 아니지만 형상 밖으로 튄다
    bm = bmesh.new()
    bm.from_mesh(mesh)
    lengths = sorted(edge.calc_length() for edge in bm.edges)
    ok = (bool(lengths) and lengths[len(lengths) // 2] <= scale * QF_MAX_MEDIAN_EDGE_RATIO
          and lengths[-1] <= lengths[len(lengths) // 2] * QF_MAX_EDGE_RATIO)
    # 3DRemesher 의 미세 엣지 비율 검사(0.5%)는 두지 않는다 — 고블린 절단 링 출력은 0.8~0.9% 로 전부 걸렸고,
    # 이 경로는 뒤에서 퇴화 엣지를 용접하고 슬리버 주변을 다시 편다
    hole_edges = 0
    for loop in _boundary_loops(bm) if ok else ():
        if _loop_on_plane(loop, plane_axes, scale) or _loop_in_bands(loop, plane_axes, scale, cuts):
            continue
        if len(loop) > HOLE_MAX_EDGES:
            ok = False
            break
        hole_edges += len(loop)
    if ok and hole_edges > len(bm.edges) * QF_MAX_BOUNDARY:
        ok = False
    if ok and _shell_count_bm(bm) > max_shells:
        ok = False
    bm.free()
    return ok


def _shell_count(mesh) -> int:
    bm = bmesh.new()
    bm.from_mesh(mesh)
    count = _shell_count_bm(bm)
    bm.free()
    return count


def _shell_count_bm(bm) -> int:
    """연결 요소(셸) 수."""
    bm.faces.ensure_lookup_table()
    seen = set()
    count = 0
    for face in bm.faces:
        if face.index in seen:
            continue
        count += 1
        stack = [face]
        while stack:
            cur = stack.pop()
            if cur.index in seen:
                continue
            seen.add(cur.index)
            for edge in cur.edges:
                stack.extend(f for f in edge.link_faces if f.index not in seen)
    return count



def _race_quadriflow(src: str, work_dir: str, request: int, *, expected: int,
                     preserve_boundary: bool, plane_axes: tuple, scale: float,
                     max_shells: int, attempts: int = QF_ATTEMPTS, cuts: tuple = (),
                     score=None, perfect: int = 0):
    """시드를 바꿔 가며 차례로 돌리고, 시간을 넘긴 시도는 죽이고 다음 시드로 넘어간다.

    QuadriFlow 의 정지는 완전히 비결정적이다 — 실측(2026-09-20, 고블린 1.3만면): 시드 1·2 는 60초를
    넘기고 시드 3 은 9초에 끝난다. 성공은 10초 안쪽이므로 짧게 끊고 다음 시드로 가는 편이 빠르다.
    동시에 띄우면 서로 코어를 뺏어 9초짜리도 45초를 넘긴다(실측) — 그래서 순차로 돌린다.

    score 를 주면(절단 링 접합 수) 쓸 수 있는 출력마다 점수를 매겨 perfect 면 바로 쓰고, 아니면 시드를 더 돌려
    가장 높은 출력을 고른다 — 링 접합 성패는 시드에 좌우되고(실측 2026-09-23, 갱스터 허리: 같은 입력에서 66·61 접합
    ↔ 43+21 로 끊김), 실패할 때마다 다시 깔면 이미 접합된 링까지 다시 복권을 긁는다."""
    timeout = max(1.0, QF_TIMEOUT + request * QF_TIMEOUT_PER_QUAD)
    tag = 'h' if preserve_boundary else 'c'
    best = None
    scored = 0
    for seed in range(1, attempts + 1):
        dst = os.path.join(work_dir, f"out{tag}{seed}.blend")
        command = [bpy.app.binary_path, "--background", "--factory-startup",
                   "--python", _WORKER, "--",
                   src, dst, str(request), "1" if preserve_boundary else "0", str(seed)]
        try:
            done = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            continue      # 멈춘 시도 — 다른 시드로
        except OSError:
            return None
        if done.returncode != 0 or not os.path.isfile(dst):
            continue      # 빨리 끝난 실패도 시드를 바꾸면 성공한다
        with bpy.data.libraries.load(dst) as (data_from, data_to):
            data_to.meshes = data_from.meshes[:1]
        if not data_to.meshes:
            continue
        mesh = data_to.meshes[0]
        if not _qf_output_ok(mesh, expected, max_shells, plane_axes, scale, cuts):
            bpy.data.meshes.remove(mesh)   # NaN·구멍투성이 출력 — 다른 시드로
            continue
        if score is None:
            return mesh
        value = score(mesh)
        scored += 1
        if value >= perfect or scored >= SCORE_OUTPUTS:
            if best is not None and value <= best[0]:
                bpy.data.meshes.remove(mesh)
                return best[1]
            if best is not None:
                bpy.data.meshes.remove(best[1])
            return mesh
        if best is None or value > best[0]:
            if best is not None:
                bpy.data.meshes.remove(best[1])
            best = (value, mesh)
        else:
            bpy.data.meshes.remove(mesh)
    return best[1] if best is not None else None


def _stitch_score(mesh, cuts: tuple, plane_axes: tuple, plane_tolerance: float) -> int:
    """복사본에 절단 링을 접합해 보고 성공한 링 수를 돌려준다 — 시드 고르기용."""
    trial = mesh.copy()
    try:
        seams = ring_cut.stitch_bands(trial, cuts, plane_axes, plane_tolerance)
    finally:
        bpy.data.meshes.remove(trial)
    return sum(1 for seam in seams if seam.bridged and (seam.closed or plane_axes))


# --- QuadriFlow 출력 정리 ---------------------------------------------------

def _boundary_loops(bm) -> list:
    """경계 엣지(면이 하나뿐인 엣지)를 연결된 루프 단위로 묶는다."""
    border_list = [e for e in bm.edges if len(e.link_faces) == 1]
    border = set(border_list)
    seen = set()
    loops = []
    for edge in border_list:
        if edge in seen:
            continue
        loop = [edge]
        seen.add(edge)
        stack = [edge]
        while stack:
            current = stack.pop()
            for vertex in current.verts:
                for other in vertex.link_edges:
                    if other in border and other not in seen:
                        seen.add(other)
                        loop.append(other)
                        stack.append(other)
        loops.append(loop)
    return loops


def _plane_snap_tolerance(loop, scale: float) -> float:
    """대칭면 루프 정점을 평면으로 되돌릴 때 허용하는 거리.

    QuadriFlow 의 경계 보존은 근사라 밀도가 낮으면 경계 정점이 엣지 길이의 절반 가까이 평면에서 떠 있다
    (실측 2026-09-21, 옥탄트 128면: 0.049). 루프 엣지 길이 기준으로 허용치를 잡는다."""
    lengths = sorted(edge.calc_length() for edge in loop)
    median = lengths[len(lengths) // 2] if lengths else 0.0
    return max(PLANE_TOLERANCE_RATIO * scale, 1.5 * median)


def _loop_on_plane(loop, plane_axes: tuple, scale: float) -> bool:
    """경계 루프가 대칭 절단면 루프인지 — 정점 과반이 평면 위에 있고 나머지도 엣지 길이 안에서 떠 있어야 한다."""
    if not plane_axes:
        return False
    tight = PLANE_TOLERANCE_RATIO * scale
    loose = _plane_snap_tolerance(loop, scale)
    vertices = {vertex for edge in loop for vertex in edge.verts}
    distances = [min(abs(v.co["XYZ".index(axis)]) for axis in plane_axes) for v in vertices]
    if any(distance >= loose for distance in distances):
        return False
    return sum(1 for distance in distances if distance < tight) >= 0.5 * len(distances)


def _loop_in_bands(loop, plane_axes: tuple, scale: float, cuts: tuple, limit_length: bool = True) -> bool:
    """경계 루프의 정점이 모두 절단 띠 안(또는 대칭면 위)에 있으면 절단이 만든 경계다.
    QuadriFlow 출력에서는 띠 안이라도 예상 링 둘레의 몇 배를 넘는 루프는 경계 주변을 덮지 못한 것이다.
    입력(절단 직후)은 원래 해상도라 엣지가 훨씬 많으므로 limit_length=False 로 길이를 보지 않는다.

    대칭면을 가로지르는 링(목)은 반쪽 출력에서 띠 경계가 대칭면 경계와 한 루프로 이어진다(실측 2026-09-23,
    갱스터: 181엣지). 길이는 대칭면 위가 아닌 엣지만 센다 — 전체 길이로 재면 모든 시드가 큰 구멍으로 버려져
    비대칭 전체 경로로 떨어졌다."""
    if not cuts:
        return False
    loose = _plane_snap_tolerance(loop, scale) if plane_axes else 0.0

    def on_plane(vertex) -> bool:
        return bool(plane_axes) and min(abs(vertex.co["XYZ".index(axis)]) for axis in plane_axes) < loose

    band_edges = [edge for edge in loop if not all(on_plane(v) for v in edge.verts)]
    if not band_edges:
        return False   # 순수 대칭면 루프 — _loop_on_plane 이 다룬다
    longest_ring = max(2.0 * math.pi * cut.radius / max(cut.half_width * 2.0, 1e-12) for cut in cuts)
    if limit_length and len(band_edges) > longest_ring * BAND_LOOP_MAX_RATIO:
        return False
    # QuadriFlow 경계 정점은 띠 밖으로 조금 뜬다 — 한 점만 벗어나도 루프 전체를 구멍으로 보면 허리 링이 든 반쪽이
    # 모든 시드에서 버려졌다(실측 2026-09-23, 갱스터: 81정점 중 1~2개)
    vertices = {vertex for edge in band_edges for vertex in edge.verts}
    strays = sum(1 for vertex in vertices if not (ring_cut.in_band(tuple(vertex.co), cuts) or on_plane(vertex)))
    return strays <= max(BAND_STRAY_MIN, len(vertices) * BAND_STRAY_RATIO)


def _ordered_loop_vertices(loop):
    """루프가 단순 폐곡선(정점마다 루프 엣지 2개)이면 순서대로 정점을 돌려준다. 아니면 None."""
    adjacency = {}
    for edge in loop:
        a, b = edge.verts
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
    if any(len(neighbours) != 2 for neighbours in adjacency.values()):
        return None
    start = next(iter(adjacency))
    ordered = [start]
    previous, current = None, start
    while True:
        first, second = adjacency[current]
        following = second if first is previous else first
        if following is start:
            break
        ordered.append(following)
        previous, current = current, following
        if len(ordered) > len(loop):
            return None
    return ordered if len(ordered) == len(loop) else None


def _repair_output(obj, plane_axes: tuple = ()) -> None:
    """QuadriFlow 가 남긴 작은 구멍을 **쿼드로** 메우고 대칭면 정점을 평면에 맞춘다. 대칭면 루프는 열어 둔다.

    make_manifold 로 메우면 삼각형이 남아 쿼드 비율이 깎이고, 대칭면 루프까지 닫혀 미러를 할 수 없다.
    출력은 구멍 가장자리에 겹친 정점(크랙)을 남기기도 해서 먼저 경계 정점을 용접한다. 짝수 구멍은 중심
    정점을 세워 쿼드 부채로, 그 외는 면을 바로 만들어 삼각화한 뒤 다시 합친다. 한 번에 안 닫히는 구멍이
    있어 여러 번 돈다. 중심 정점 위치는 뒤따르는 슈링크랩이 원본 표면으로 끌어온다."""
    scale = _local_size(obj)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    border_vertices = list({v for e in bm.edges if len(e.link_faces) == 1 for v in e.verts})
    if border_vertices:
        bmesh.ops.remove_doubles(bm, verts=border_vertices, dist=SNAP_TOLERANCE_RATIO * scale)
    _weld_degenerate_bm(bm, scale)
    plane_vertices = {}
    for _ in range(REPAIR_ROUNDS):
        leftovers = []
        open_holes = False
        for loop in _boundary_loops(bm):
            if _loop_on_plane(loop, plane_axes, scale):
                tolerance = _plane_snap_tolerance(loop, scale)
                for edge in loop:
                    for vertex in edge.verts:
                        plane_vertices[vertex] = max(tolerance, plane_vertices.get(vertex, 0.0))
                continue
            open_holes = True
            if plane_axes:
                # 대칭면 루프에 붙은 작은 구멍은 한 경계 성분으로 합쳐진다. 평면 위 엣지는 메우지 않고(평면을
                # 가로지르는 웹이 생긴다 — 3DRemesher 실측: 목 안쪽 웹), 평면 밖 체인만 '만(灣)' 으로 닫는다
                plane_edges, bays = _split_plane_loop(loop, plane_axes, scale)
                if plane_edges:
                    tolerance = _plane_snap_tolerance(loop, scale)
                    for edge in plane_edges:
                        for vertex in edge.verts:
                            plane_vertices[vertex] = max(tolerance, plane_vertices.get(vertex, 0.0))
                    for chain in bays:
                        if 3 <= len(chain) <= HOLE_MAX_EDGES:
                            # 만은 평면을 따라 얇게 누운 띠라 중심 정점 부채는 슬리버를 남긴다 — n각형 하나로
                            # 닫고 뒤의 삼각화·병합에 맡긴다
                            try:
                                bm.faces.new(chain)
                            except ValueError:
                                pass
                    continue
            ordered = _ordered_loop_vertices(loop)
            if ordered is not None and 3 <= len(ordered) <= HOLE_MAX_EDGES and _fill_loop(bm, ordered):
                continue
            leftovers.extend(loop)
        if not open_holes:
            break
        if leftovers:
            bmesh.ops.holes_fill(bm, edges=sorted(set(leftovers), key=lambda edge: edge.index), sides=0)
    odd = [face for face in bm.faces if len(face.verts) != 4]
    if odd:
        result = bmesh.ops.triangulate(bm, faces=odd)
        triangles = [f for f in result["faces"] if f.is_valid and len(f.verts) == 3]
        if triangles:
            bmesh.ops.join_triangles(bm, faces=triangles, cmp_seam=False, cmp_sharp=False,
                                     cmp_uvs=False, cmp_vcols=False, cmp_materials=False,
                                     angle_face_threshold=math.radians(40.0),
                                     angle_shape_threshold=math.radians(40.0))
    _snap_plane_vertices({v: t for v, t in plane_vertices.items() if v.is_valid}, plane_axes)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def _split_plane_loop(loop, plane_axes: tuple, scale: float):
    """경계 루프를 대칭면 위 엣지와, 평면 밖으로 튀어나온 열린 체인(정점 열)들로 나눈다.
    평면 위 엣지가 없으면 ([], []) — 보통의 구멍이다."""
    tolerance = _plane_snap_tolerance(loop, scale)

    def on_plane(vertex) -> bool:
        return any(abs(vertex.co["XYZ".index(axis)]) < tolerance for axis in plane_axes)

    plane_edges = [edge for edge in loop if all(on_plane(v) for v in edge.verts)]
    if not plane_edges:
        return [], []
    plane_set = set(plane_edges)
    adjacency = {}
    for edge in loop:
        if edge in plane_set:
            continue
        a, b = edge.verts
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)
    chains = []
    seen = set()
    for start in adjacency:
        if start in seen or len(adjacency[start]) != 1:
            continue
        chain = [start]
        seen.add(start)
        previous, current = None, start
        while True:
            following = [v for v in adjacency[current] if v is not previous]
            if not following:
                break
            previous, current = current, following[0]
            if current in seen:
                break
            chain.append(current)
            seen.add(current)
        # 양 끝이 평면 위에 있어야 평면 쪽 변 하나로 닫히는 만(灣)이다
        if len(chain) >= 3 and on_plane(chain[0]) and on_plane(chain[-1]):
            chains.append(chain)
    return plane_edges, chains


def _weld_degenerate_bm(bm, scale: float) -> int:
    """길이가 사실상 0 인 엣지의 양 끝점을 합친다. 합친 정점 수를 돌려준다.

    QuadriFlow 출력과 투영 슈링크랩은 둘 다 이런 엣지를 남긴다(실측 2026-09-22, 캐릭터: 출력 26개,
    최대 종횡비 1.1e11). 퇴화한 면은 UV 언랩·베이크를 망가뜨리고 종횡비 지표를 의미 없게 만든다.
    합쳐진 쿼드는 삼각형이 되지만 뒤따르는 삼각화·병합이 다시 쿼드로 돌린다."""
    weld = max(WELD_RATIO * scale, 1e-6)
    degenerate = list({v for e in bm.edges if e.calc_length() < weld for v in e.verts})
    if not degenerate:
        return 0
    bmesh.ops.remove_doubles(bm, verts=degenerate, dist=weld)
    return len(degenerate)


def _weld_degenerate(obj) -> int:
    """오브젝트 메시에 _weld_degenerate_bm 을 적용한다."""
    scale = _local_size(obj)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    merged = _weld_degenerate_bm(bm, scale)
    if merged:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        bm.to_mesh(obj.data)
        obj.data.update()
    bm.free()
    return merged


def _fill_loop(bm, ordered) -> bool:
    """단순 폐루프 하나를 면으로 닫는다. 짝수 6각 이상은 중심 정점 쿼드 부채, 나머지는 면 하나."""
    count = len(ordered)
    try:
        if count >= 6 and count % 2 == 0:
            centre_co = ordered[0].co.copy()
            for vertex in ordered[1:]:
                centre_co += vertex.co
            centre = bm.verts.new(centre_co / count)
            for index in range(0, count, 2):
                bm.faces.new((centre, ordered[index], ordered[(index + 1) % count],
                              ordered[(index + 2) % count]))
        else:
            bm.faces.new(ordered)
    except ValueError:
        return False   # 이미 있는 면과 겹치는 루프 — 일반 구멍 메우기에 맡긴다
    return True


def _snap_plane_vertices(plane_vertices: dict, plane_axes: tuple) -> None:
    """대칭면 루프 정점을 평면에 붙인다.

    정점마다 가장 가까운 평면 하나에만 붙이고, 두 평면이 만나는 모서리는 평면 쌍마다 교선에 가장 가까운
    정점 하나만 두 평면에 붙인다 — 이웃한 두 정점을 모두 두 평면에 붙이면 교선 위에 엣지가 놓이고,
    두 번 미러된 뒤 면 네 개가 그 엣지를 공유해 비매니폴드가 된다."""
    components = ["XYZ".index(axis) for axis in plane_axes]
    corners = {}
    for first in range(len(components)):
        for second in range(first + 1, len(components)):
            a, b = components[first], components[second]
            candidates = sorted(
                ((abs(v.co[a]) ** 2 + abs(v.co[b]) ** 2, v, tolerance)
                 for v, tolerance in plane_vertices.items()
                 if abs(v.co[a]) < tolerance and abs(v.co[b]) < tolerance),
                key=lambda item: item[0])
            chosen = []
            for _, vertex, tolerance in candidates:
                if any((vertex.co - other.co).length < 2.0 * tolerance for other in chosen):
                    continue
                chosen.append(vertex)
                corners.setdefault(vertex, set()).update((a, b))
    for vertex, tolerance in plane_vertices.items():
        if vertex in corners:
            for component in corners[vertex]:
                vertex.co[component] = 0.0
            continue
        nearest = min(components, key=lambda component: abs(vertex.co[component]))
        if abs(vertex.co[nearest]) < tolerance:
            vertex.co[nearest] = 0.0


def _lift_interior_plane_vertices(obj, plane_axes: tuple) -> int:
    """대칭면 위에 놓인 내부 정점(경계가 아닌 정점)을 미러 용접 임계값의 두 배만큼 안쪽으로 민다.
    경계 정점은 용접돼야 하므로 둔다. 민 정점 수를 돌려준다."""
    threshold = max(SNAP_TOLERANCE_RATIO * _local_size(obj), 1e-6)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    lifted = 0
    for vertex in bm.verts:
        if vertex.is_boundary:
            continue
        for axis in plane_axes:
            component = "XYZ".index(axis)
            if abs(vertex.co[component]) < threshold * 1.5:
                vertex.co[component] = threshold * 2.0
                lifted += 1
    if lifted:
        bm.to_mesh(obj.data)
        obj.data.update()
    bm.free()
    return lifted


def _mirror(obj, plane_axes: tuple) -> None:
    """양의 반쪽을 대칭면 기준으로 복제·용접해 닫힌 대칭 메시로 만든다."""
    mod = obj.modifiers.new("LP3D_Mirror", 'MIRROR')
    mod.use_axis = tuple(axis in plane_axes for axis in "XYZ")
    mod.use_bisect_axis = (False, False, False)   # 이미 잘라 놨으므로 다시 자르지 않는다
    mod.use_mirror_merge = True
    mod.merge_threshold = max(SNAP_TOLERANCE_RATIO * _local_size(obj), 1e-6)
    with _override(obj):
        bpy.ops.object.modifier_apply(modifier=mod.name)


def _face_aspect(face) -> float:
    """면의 가장 긴 엣지 / 가장 짧은 엣지."""
    lengths = [edge.calc_length() for edge in face.edges]
    return max(lengths) / max(min(lengths), 1e-12)


def symmetry_error(obj, axis: str = 'X') -> float:
    """정점을 그 축으로 뒤집었을 때 가장 가까운 정점까지의 최대 거리 — 0 에 가까울수록 대칭이다."""
    from mathutils.kdtree import KDTree
    vertices = obj.data.vertices
    tree = KDTree(len(vertices))
    for index, vertex in enumerate(vertices):
        tree.insert(vertex.co, index)
    tree.balance()
    component = "XYZ".index(axis)
    worst = 0.0
    for vertex in vertices:
        mirrored = vertex.co.copy()
        mirrored[component] = -mirrored[component]
        worst = max(worst, tree.find(mirrored)[2])
    return worst


def _decimate(obj, target_faces: int) -> None:
    """Decimate(COLLAPSE)로 목표 면수까지 줄인다 — 결과는 전부 삼각형이라 면수=삼각형 수다."""
    obj.data.calc_loop_triangles()
    tris = len(obj.data.loop_triangles)
    if tris <= target_faces:
        return
    mod = obj.modifiers.new("LP3D_Decimate", 'DECIMATE')
    mod.decimate_type = 'COLLAPSE'
    mod.ratio = min(1.0, float(target_faces) / float(tris))
    with _override(obj):
        bpy.ops.object.modifier_apply(modifier=mod.name)


def _shrinkwrap(obj, target, plane_axes: tuple = ()) -> None:
    """리토폴로지 결과를 원본 표면에 붙여 복셀·QuadriFlow 가 뭉갠 디테일을 되찾는다.

    최근접점 방식(NEAREST_SURFACEPOINT)만 쓰면 접히는 공간(겨드랑이·소매 안쪽)에서 이웃 정점이 서로 다른
    표면으로 끌려가 면이 교차하고 어둡게 찢어진다(실측 2026-09-21: 원본에서 1% 넘게 벗어난 면 39개).
    그래서 **노멀 방향 투영**(양방향, 복셀 SHRINK_LIMIT 배 안)을 먼저 하고, 노멀 선상에 표면이 없어 빗나간
    정점(옷단 립 등)만 최근접점으로 붙인다. 그 뒤 스무딩 → 다시 투영을 RELAX_ROUNDS 번 반복해 접힌 부분의
    와이어를 편다(같은 실측에서 7개로 감소).

    마지막으로 종횡비가 큰 슬리버 면 **주변 한 겹만** 골라 다시 편다 — QuadriFlow 는 구멍 주변에 아주 짧은
    엣지를 남기고 그것이 면 분포를 망친다(실측 2026-09-21: 최대 종횡비 22.2 → 7.6). 전체 스무딩을 더 돌리면
    형상만 뭉개지고 짧은 엣지는 늘어나지 않는다.

    plane_axes 가 있으면 그 평면 위 정점은 매 단계 뒤 평면으로 되돌린다 — 평면을 넘어간 정점을 두면
    미러 복제와 겹쳐 대칭이 깨진다(실측: 대칭 오차 0.07)."""
    import mathutils
    limit = _voxel_size(target, QF_INPUT_FACES) * SHRINK_LIMIT
    tree = mathutils.bvhtree.BVHTree.FromObject(target, bpy.context.evaluated_depsgraph_get())
    to_target = target.matrix_world.inverted() @ obj.matrix_world
    to_local = obj.matrix_world.inverted() @ target.matrix_world
    components = ["XYZ".index(axis) for axis in plane_axes]
    # _repair_output 이 대칭면 정점만 정확히 0.0 으로 스냅해 두므로 그 정점을 고정 대상으로 삼는다.
    # 투영·스무딩은 디폼만 하므로 인덱스가 유지되고, 퇴화 엣지를 용접한 뒤에는 목록을 다시 만든다.
    pinned = []

    def repin():
        pinned[:] = [(index, component)
                     for index, vertex in enumerate(obj.data.vertices)
                     for component in components if vertex.co[component] == 0.0]

    repin()

    def nearest_local(co):
        hit = tree.find_nearest(to_target @ co)
        return to_local @ hit[0] if hit[0] is not None else None

    def pin():
        """대칭면 위 정점은 평면으로 되돌리고, 평면을 넘어간 정점은 평면에서 멈춘다."""
        if not components:
            return
        vertices = obj.data.vertices
        for index, component in pinned:
            vertices[index].co[component] = 0.0
        for vertex in vertices:
            for component in components:
                if vertex.co[component] < 0.0:
                    vertex.co[component] = 0.0
        obj.data.update()

    def project():
        before = [v.co.copy() for v in obj.data.vertices]
        mod = obj.modifiers.new("LP3D_Shrinkwrap", 'SHRINKWRAP')
        mod.target = target
        mod.wrap_method = 'PROJECT'
        mod.use_negative_direction = True
        mod.use_positive_direction = True
        mod.project_limit = limit
        mod.offset = 0.0
        with _override(obj):
            bpy.ops.object.modifier_apply(modifier=mod.name)
        for index, vert in enumerate(obj.data.vertices):
            if (vert.co - before[index]).length_squared < 1e-14:
                nearest = nearest_local(vert.co)
                if nearest is not None:
                    vert.co = nearest
        obj.data.update()
        pin()

    def relax():
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.smooth_vert(bm, verts=bm.verts[:], factor=RELAX_FACTOR,
                              use_axis_x=True, use_axis_y=True, use_axis_z=True)
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        pin()

    def relax_slivers():
        for _ in range(SLIVER_ROUNDS):
            bm = bmesh.new()
            bm.from_mesh(obj.data)
            slivers = [face for face in bm.faces if _face_aspect(face) > SLIVER_ASPECT]
            if not slivers:
                bm.free()
                return
            # 슬리버 정점과 그 이웃 한 겹을 함께 펴야 짧은 엣지가 실제로 늘어난다
            ring = list({neighbour for face in slivers for vertex in face.verts
                         for edge in vertex.link_edges for neighbour in edge.verts})
            bmesh.ops.smooth_vert(bm, verts=ring, factor=RELAX_FACTOR,
                                  use_axis_x=True, use_axis_y=True, use_axis_z=True)
            for vertex in ring:
                nearest = nearest_local(vertex.co)
                if nearest is not None:
                    vertex.co = nearest
            bm.to_mesh(obj.data)
            bm.free()
            obj.data.update()
            pin()

    project()
    for _ in range(RELAX_ROUNDS):
        relax()
        project()
    # 투영이 이웃 정점을 같은 자리로 끌어와 퇴화 엣지를 만든다 — 슬리버를 펴기 전에 먼저 합친다
    if _weld_degenerate(obj):
        repin()
        pin()
    relax_slivers()
    if _weld_degenerate(obj):
        repin()
        pin()


def _unwrap(obj) -> None:
    """스마트 UV 투영 + 아일랜드 팩.

    팩은 반드시 shape_method='AABB' 로 한다 — 기본값 CONCAVE 는 아일랜드가 수천 개인 언랩에서
    단일 스레드로 10분을 넘겨도 끝나지 않는다(실측 2026-09-20: 8천 면·2만6천 UV 루프에서 11분 경과 후
    중단, AABB 는 0.2초)."""
    with _override(obj):
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.uv.smart_project(angle_limit=UNWRAP_ANGLE, island_margin=ISLAND_MARGIN)
            bpy.ops.uv.pack_islands(margin=ISLAND_MARGIN, shape_method='AABB')
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')
    if not obj.data.uv_layers:
        # 조용히 빗나간 언랩을 여기서 잡는다 — 그냥 두면 베이크가 알 수 없는 오류로 터진다
        raise RuntimeError(f"UV 언랩이 레이어를 만들지 못했습니다: {obj.name}")


# --- 머티리얼·베이크 -------------------------------------------------------

def _prepare_material(obj, name: str, size: int, normal_map: bool):
    """베이크 대상 이미지 노드만 심은 새 Principled 머티리얼. (images, nodes) 를 돌려준다.

    노드를 BSDF 에 잇는 것은 베이크가 끝난 뒤다(_connect_material) — 베이크 대상 이미지가
    대상 오브젝트의 셰이더에 물려 있으면 Cycles 가 순환 의존을 경고한다."""
    material = bpy.data.materials.new(safe_id_name(name + "_리토폴로지"))
    material.use_nodes = True
    tree = material.node_tree
    images, nodes = {}, {}
    plan = [item for item in _MAP_PLAN if normal_map or item[0] != "normal"]
    for index, (key, label, non_color, fill, _socket) in enumerate(plan):
        image = bpy.data.images.new(safe_id_name(f"{name}_{label}"), size, size, alpha=False)
        image.generated_color = fill
        if non_color:
            image.colorspace_settings.name = 'Non-Color'
        node = tree.nodes.new('ShaderNodeTexImage')
        node.image = image
        node.location = (-900, 400 - index * 340)
        images[key] = image
        nodes[key] = node
    obj.data.materials.clear()
    obj.data.materials.append(material)
    return images, nodes


def _connect_material(nodes) -> None:
    """구운 이미지 노드를 Principled 에 연결한다 — 노멀은 NORMAL_MAP 노드를 거친다."""
    sockets = {key: socket for key, _label, _nc, _fill, socket in _MAP_PLAN}
    for key, node in nodes.items():
        tree = node.id_data
        bsdf = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
        if key == "normal":
            normal = tree.nodes.new('ShaderNodeNormalMap')
            normal.location = (node.location.x + 380, node.location.y)
            tree.links.new(node.outputs["Color"], normal.inputs["Color"])
            tree.links.new(normal.outputs["Normal"], bsdf.inputs[sockets[key]])
        else:
            tree.links.new(node.outputs["Color"], bsdf.inputs[sockets[key]])
    # 활성 이미지 노드는 마지막으로 구운 노멀맵으로 남아 있다 — 뷰포트 텍스처 표시와
    # 텍스처 페인트가 이 노드를 보므로 베이스컬러로 되돌린다
    base = nodes.get("basecolor")
    if base is not None:
        for other in base.id_data.nodes:
            other.select = False
        base.select = True
        base.id_data.nodes.active = base


def _bake_maps(source, work, nodes, normal_map: bool, say) -> None:
    """Cycles Selected→Active 로 원본의 PBR 값을 작업본 UV 위에 굽는다."""
    scene = bpy.context.scene
    saved = _save_render(scene)
    try:
        _setup_bake(scene, _model_size(work))
        for key, socket, label in (("basecolor", "Base Color", "베이스컬러"),
                                   ("metallic", "Metallic", "메탈릭"),
                                   ("roughness", "Roughness", "러프니스")):
            say(f"베이크: {label}")
            with _emit_channel(source, socket) as linked:
                if linked:
                    _bake_pass(source, work, nodes[key], type='EMIT')
                else:
                    # 텍스처 없이 상수만 있는 채널 — 구울 것이 없으니 단색으로 채운다
                    nodes[key].image.generated_color = _channel_constant(source, socket)
        if normal_map:
            say("베이크: 노멀")
            _bake_pass(source, work, nodes["normal"], type='NORMAL')
    finally:
        _restore_render(scene, saved)


def _setup_bake(scene, size: float) -> None:
    try:
        scene.render.engine = 'CYCLES'
    except TypeError as e:
        raise RuntimeError(f"Cycles 를 켤 수 없습니다: {e}")
    cycles = getattr(scene, "cycles", None)
    if cycles is not None:
        cycles.device = 'CPU'
        cycles.samples = 1
    bake = scene.render.bake
    bake.target = 'IMAGE_TEXTURES'
    bake.use_selected_to_active = True
    bake.cage_extrusion = size * CAGE_RATIO
    bake.max_ray_distance = size * RAY_RATIO
    bake.margin = BAKE_MARGIN
    bake.use_clear = True
    bake.normal_space = 'TANGENT'


def _bake_pass(source, work, node, **kwargs) -> None:
    """node 를 활성 이미지 노드로 지정하고 한 패스를 굽는다.

    베이크 결과는 '활성 TEX_IMAGE 노드'로 들어간다 — 선택만 해서는 안 되고 active 여야 한다."""
    tree = node.id_data
    for other in tree.nodes:
        other.select = False
    node.select = True
    tree.nodes.active = node
    view_layer = bpy.context.view_layer
    for obj in view_layer.objects:
        try:
            obj.select_set(False)
        except RuntimeError:
            pass   # 숨겨진 오브젝트는 선택 상태를 바꿀 수 없다 — 어차피 선택되지 않는다
    source.select_set(True)
    work.select_set(True)
    view_layer.objects.active = work
    with bpy.context.temp_override(scene=bpy.context.scene, view_layer=view_layer,
                                   object=work, active_object=work,
                                   selected_objects=[source, work],
                                   selected_editable_objects=[source, work]):
        bpy.ops.object.bake(use_selected_to_active=True, use_clear=True,
                            margin=BAKE_MARGIN,
                            cage_extrusion=bpy.context.scene.render.bake.cage_extrusion,
                            max_ray_distance=bpy.context.scene.render.bake.max_ray_distance,
                            **kwargs)


@contextmanager
def _emit_channel(obj, socket_name: str):
    """소스 머티리얼의 Principled 입력을 임시로 Emission 에 물린다 — EMIT 베이크로 원값을 뽑기 위해.

    메탈릭·러프니스는 전용 베이크 패스가 없다(ROUGHNESS 패스는 Principled 입력이 아니라 최종 셰이더
    러프니스를 굽는다). 실측: 셰이프 서버 GLB 는 ORM 텍스처 → SEPARATE_COLOR 의 G/B 가 물려 있다.

    베이스컬러도 DIFFUSE 패스가 아니라 이 경로로 굽는다 — 실측(2026-09-20): 셰이프 서버 GLB 의 ORM
    B채널(메탈릭) 평균이 0.98 이라 모델이 사실상 전부 금속이고, 금속은 확산 성분이 없어서
    bake(type='DIFFUSE', pass_filter={'COLOR'}) 결과가 새까맣게 나온다. EMIT 은 메탈릭과 무관하게
    입력에 물린 값을 그대로 옮긴다.

    입력에 연결이 있었는지를 yield 한다."""
    changes = []
    linked = False
    for material in obj.data.materials:
        if not material or not material.use_nodes:
            continue
        tree = material.node_tree
        bsdf = next((n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        output = next((n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL'), None)
        if bsdf is None or output is None:
            continue
        socket = bsdf.inputs.get(socket_name)
        if socket is None:
            continue
        surface = output.inputs["Surface"]
        previous = surface.links[0].from_socket if surface.links else None
        emit = tree.nodes.new('ShaderNodeEmission')
        emit.location = (output.location.x - 260, output.location.y - 260)
        if socket.links:
            tree.links.new(socket.links[0].from_socket, emit.inputs["Color"])
            linked = True
        else:
            emit.inputs["Color"].default_value = _as_color(socket.default_value)
        tree.links.new(emit.outputs["Emission"], surface)
        changes.append((tree, emit, previous, surface))
    try:
        yield linked
    finally:
        for tree, emit, previous, surface in changes:
            tree.nodes.remove(emit)
            if previous is not None:
                tree.links.new(previous, surface)


def _as_color(value) -> tuple:
    """스칼라든 RGBA 든 Emission Color 에 넣을 수 있는 (r, g, b, a) 로 맞춘다."""
    if hasattr(value, "__len__"):
        channels = list(value)[:4]
        while len(channels) < 4:
            channels.append(1.0)
        return tuple(float(c) for c in channels)
    return (float(value), float(value), float(value), 1.0)


def _channel_constant(obj, socket_name: str) -> tuple:
    """소스 머티리얼들의 Principled 입력 상수값 평균을 (r, g, b, a) 로. 찾지 못하면 검정."""
    values = [_as_color(socket.default_value)
              for socket in _principled_sockets(obj, socket_name) if not socket.links]
    if not values:
        return (0.0, 0.0, 0.0, 1.0)
    return tuple(sum(v[i] for v in values) / len(values) for i in range(4))


def _principled_sockets(obj, socket_name: str):
    """오브젝트 머티리얼들의 Principled 입력 소켓."""
    for material in obj.data.materials:
        if not material or not material.use_nodes:
            continue
        bsdf = next((n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        socket = bsdf.inputs.get(socket_name) if bsdf else None
        if socket is not None:
            yield socket


def _save_render(scene) -> dict:
    bake = scene.render.bake
    cycles = getattr(scene, "cycles", None)
    return {
        "engine": scene.render.engine,
        "device": getattr(cycles, "device", None),
        "samples": getattr(cycles, "samples", None),
        "bake": {k: getattr(bake, k) for k in
                 ("target", "use_selected_to_active", "cage_extrusion", "max_ray_distance",
                  "margin", "use_clear", "normal_space")},
    }


def _restore_render(scene, saved: dict) -> None:
    try:
        scene.render.engine = saved["engine"]
    except TypeError:
        pass
    cycles = getattr(scene, "cycles", None)
    if cycles is not None:
        if saved["device"] is not None:
            cycles.device = saved["device"]
        if saved["samples"] is not None:
            cycles.samples = saved["samples"]
    for key, value in saved["bake"].items():
        setattr(scene.render.bake, key, value)


def find_retopo_target(collection):
    """컬렉션에서 리토폴로지할 메시. 보존본 표식이 있으면 건너뛴다. 없으면 None."""
    if collection is None:
        return None
    for obj in collection.objects:
        if obj.type == 'MESH' and not obj.get(SOURCE_KEY):
            return obj
    return None


def find_retopo_source(collection):
    """컬렉션에 보존된 리토폴로지 원본(`_원본`). 없으면 None — 있으면 다시 리토폴로지할 수 있다."""
    if collection is None:
        return None
    for obj in collection.objects:
        if obj.type == 'MESH' and obj.get(SOURCE_KEY):
            return obj
    return None


def has_retopo_source(collection) -> bool:
    """이미 리토폴로지가 끝난 컬렉션인지 — 보존본 표식이 붙은 오브젝트가 있으면 그렇다."""
    return bool(collection is not None
                and any(obj.get(SOURCE_KEY) for obj in collection.objects))
