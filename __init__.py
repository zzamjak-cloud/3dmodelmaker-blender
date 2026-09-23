# SPDX-License-Identifier: GPL-3.0-or-later
# AI LowPoly ModelMaker — GPT-6 Astra로 로우폴리 모델을 생성하는 애드온
import importlib
import pkgutil
import sys
from pathlib import Path

from . import preferences, properties
from .ui import operators, panel, previews, ring_guides

# 등록 순서를 보장하는 모듈 목록 (의존성 순)
_MODULES = (preferences, properties, previews, operators, ring_guides, panel)
_SUBPACKAGES = ("lowpoly", "agents", "core", "pipeline", "texturing")


def _discover_submodules():
    """패키지 안의 모든 하위 모듈을 임포트해 sys.modules에 올린다.

    목록을 손으로 관리하면 새 모듈을 추가할 때마다 갱신해야 하고, 빠뜨리면
    Dev Reload가 그 모듈만 구버전으로 남겨 'has no attribute' 오류가 난다.
    실제로 그런 사고가 있었으므로 자동 탐색으로 바꿨다."""
    for sub in _SUBPACKAGES:
        pkg_name = f"{__package__}.{sub}"
        try:
            pkg = importlib.import_module(pkg_name)
        except ImportError:
            continue
        for info in pkgutil.iter_modules(pkg.__path__):
            try:
                importlib.import_module(f"{pkg_name}.{info.name}")
            except ImportError:
                pass  # 선택적 의존 모듈은 건너뛴다


def _reload_targets():
    """리로드 대상 모듈 — 깊은 모듈부터(자식 먼저) 갱신해야 상위가 새 객체를 본다."""
    _discover_submodules()
    prefix = __package__ + "."
    names = [n for n in sys.modules if n.startswith(prefix) and sys.modules[n] is not None]
    # 업데이트로 삭제된 모듈은 실행 중 세션에 남아 있어도 다시 로드하지 않는다.
    removed = [n for n in names
               if getattr(sys.modules[n], "__file__", None)
               and not Path(sys.modules[n].__file__).is_file()]
    names = [n for n in names if n not in removed]
    # ui.* 는 _MODULES에서 따로 리로드하므로 제외
    names = [n for n in names if not n.startswith(prefix + "ui")]
    names.sort(key=lambda n: (-n.count("."), n))
    return [sys.modules[n] for n in names]


def dev_reload():
    """코드 수정 후 Blender 재시작 없이 리로드.

    importlib.reload만으로는 이미 등록된 클래스(PropertyGroup/Operator/Panel)가
    구버전으로 남으므로, 등록 해제 → 리로드 → 재등록까지 해야
    프로퍼티 정의 변경(이름·기본값 등)이 반영된다.

    실패하면 조용히 넘어가지 않는다 — 부분 리로드 상태는 구버전 모듈이 섞여
    원인을 찾기 어려운 오류를 낸다."""
    unregister()
    failed = []
    # 같은 깊이의 형제 모듈이 서로 임포트하면(예: scene_session → session) 한 번의
    # 리로드로는 먼저 갱신된 모듈이 구버전 객체를 물고 있으므로 정순·역순 두 번 돌린다.
    targets = _reload_targets()
    for pass_targets in (targets, list(reversed(targets))):
        for mod in pass_targets:
            try:
                importlib.reload(mod)
            except Exception as e:
                failed.append(f"{mod.__name__}: {e}")
    for mod in _MODULES:
        try:
            importlib.reload(mod)
        except Exception as e:
            failed.append(f"{mod.__name__}: {e}")
    register()
    if failed:
        raise RuntimeError("리로드 실패: " + " / ".join(failed))


def register():
    for mod in _MODULES:
        mod.register()


def unregister():
    for mod in reversed(_MODULES):
        mod.unregister()
