"""Make ``import zmart_viewer`` work from a checkout that was never installed.

The engine lives in ``engine/`` but is imported as ``zmart_viewer``. An
installed package (``pip install -e .``) is found the usual way; without one,
this points the name at the folder. Import this module before the engine.
"""

import importlib.util
import sys
from pathlib import Path

try:
    import zmart_viewer  # noqa: F401
except ImportError:
    _engine = Path(__file__).resolve().parents[1] / "engine"
    _spec = importlib.util.spec_from_file_location(
        "zmart_viewer", _engine / "__init__.py", submodule_search_locations=[str(_engine)]
    )
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["zmart_viewer"] = _module
    _spec.loader.exec_module(_module)
    # The viewer's own window lives in gui/ but is imported as
    # zmart_viewer.gui, the way the installed package lays it out.
    _gui = _engine.parent / "gui"
    _spec = importlib.util.spec_from_file_location(
        "zmart_viewer.gui",
        _gui / "__init__.py",
        submodule_search_locations=[str(_gui)],
    )
    _window = importlib.util.module_from_spec(_spec)
    sys.modules["zmart_viewer.gui"] = _window
    _module.gui = _window
    _spec.loader.exec_module(_window)
