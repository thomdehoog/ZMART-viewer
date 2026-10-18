"""A colour map's swatch shows the colours the picture is drawn in.

The colour chooser draws a small strip beside each map's name, so a map is
picked by eye. The strip was a gradient written by hand, three colours for a
map the shader draws through four -- fire's showed a bright red the picture
never has and none of its orange. The strip is now drawn from the shader's
own colours, so what is chosen is what is shown.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="needs Node.js")


def test_every_swatch_is_its_maps_own_colours_in_order():
    finished = subprocess.run(
        [NODE, "--input-type=module", "--eval", """
            const shared = await import('./engine/drawing/programs.js');
            const page = await import('./gui/source/colours.ts');
            console.log(JSON.stringify(shared.LOOKUP_TABLE_NAMES.map(name => ({
              name,
              swatch: page.LUT_GRADIENTS[name],
              stops: shared.LOOKUP_TABLES[name].map(shared.hexColour),
            }))));
        """],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert finished.returncode == 0, finished.stderr
    maps = json.loads(finished.stdout)
    assert [entry["name"] for entry in maps] == ["viridis", "magma", "fire", "ice"]
    for entry in maps:
        assert entry["swatch"] == f"linear-gradient(90deg, {', '.join(entry['stops'])})", entry
