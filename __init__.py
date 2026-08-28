# AI LowPoly ModelMaker — Claude/Codex CLI 에이전트로 로우폴리 모델을 생성하는 애드온
import importlib

from . import preferences, properties
from .ui import operators, panel

# 리로드/등록 순서를 보장하는 모듈 목록 (의존성 순)
_MODULES = (preferences, properties, operators, panel)


def _submodules():
    # Dev Reload 시 하위 모듈까지 의존성 순서대로 갱신
    from . import lowpoly
    from .lowpoly import palette_data, colorsnap, primitives, modeling, palette, cleanup
    from .agents import parsing, base, claude_cli, codex_cli
    from .core import loop, prompts, executor, capture, runner, session, persist
    from .pipeline import export, assets
    return (
        lowpoly.palette_data, lowpoly.colorsnap, lowpoly.primitives, lowpoly.modeling, lowpoly.palette, lowpoly.cleanup, lowpoly,
        parsing, base, claude_cli, codex_cli,
        loop, prompts, executor, capture, runner, session, persist,
        export, assets,
    )


def dev_reload():
    # 코드 수정 후 Blender 재시작 없이 리로드
    for mod in _submodules():
        importlib.reload(mod)
    for mod in _MODULES:
        importlib.reload(mod)


def register():
    for mod in _MODULES:
        mod.register()


def unregister():
    for mod in reversed(_MODULES):
        mod.unregister()
