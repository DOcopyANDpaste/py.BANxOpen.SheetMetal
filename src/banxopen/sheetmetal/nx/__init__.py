"""BANxOpen SheetMetal: NX API.

Importable without NX: names are resolved lazily on first use, so tool tests and type checkers can import this.

    started = SheetMetalServices.create(ctx)
    if not started.ok:
        ...                                    # show started.message and stop
    services = started.value
    engine = MaterialEngine.create(ctx, services.material_modules)   # the Material Assignment dialog
    tool = BeadTool.create(services)                                 # the bead dialog
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from banxopen.sheetmetal.nx_adapters.bead_tool import BeadTool, BlockContents
    from banxopen.sheetmetal.nx_adapters.features import create_curve_section
    from banxopen.sheetmetal.nx_adapters.services import SheetMetalServices

__all__ = ["BeadTool", "BlockContents", "SheetMetalServices", "create_curve_section"]

_ADAPTERS = "banxopen.sheetmetal.nx_adapters"
_LAZY = {
    "BeadTool": "bead_tool",
    "BlockContents": "bead_tool",
    "SheetMetalServices": "services",
    "create_curve_section": "features",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f"{_ADAPTERS}.{module}"), name)
