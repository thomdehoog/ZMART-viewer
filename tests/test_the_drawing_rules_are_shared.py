"""The drawing rules another page needs are the engine's, and shipped with it.

ZMART-interface draws with the same neuroglancer as the viewer's own window,
and needs the same two things from it: the little programs that turn a stored
value into a colour on screen, and the edits that make neuroglancer keep an
image's transparency. Both used to be copied into the interface and kept in
line by hand, and a copy kept by hand drifts. So both live in
``engine/drawing/``, ship in the wheel, and the viewer's own page imports them
from there exactly as the interface does.

These tests run the modules under Node, as the page builds do.
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

#: What the viewer's page drew before the programs moved, as the first sixteen
#: hex digits of each program's SHA-256, for every flat or volume, colour map
#: and opaque choice. Moving the programs must not change a single character.
PROGRAMS_BEFORE_THE_MOVE = [
    [False, None, False, "d3c1cf205c632942"], [False, None, True, "24c948f0d4024fe3"],
    [False, "viridis", False, "d2335f71122cdbb6"], [False, "viridis", True, "5435c0ef341e75ed"],
    [False, "magma", False, "933bc419c3935403"], [False, "magma", True, "5162bfb6b052dbc1"],
    [False, "fire", False, "fb7eced5ba51e002"], [False, "fire", True, "68b2536d04bb8a08"],
    [False, "ice", False, "65118b59c8d4c680"], [False, "ice", True, "0f982c1346455526"],
    [True, None, False, "2a03c072a98bd4b3"], [True, None, True, "2a03c072a98bd4b3"],
    [True, "viridis", False, "6cb1ca01f6e97494"], [True, "viridis", True, "6cb1ca01f6e97494"],
    [True, "magma", False, "c7df0d45643cf03b"], [True, "magma", True, "c7df0d45643cf03b"],
    [True, "fire", False, "3674dd10551d2602"], [True, "fire", True, "3674dd10551d2602"],
    [True, "ice", False, "7ef036757cf753d2"], [True, "ice", True, "7ef036757cf753d2"],
]


def run_node(script: str) -> object:
    """Run *script* as a module from the repository root and read the JSON it prints."""
    finished = subprocess.run(
        [NODE, "--input-type=module", "--eval", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


def test_the_programs_are_the_engines_and_unchanged_by_the_move():
    answer = run_node("""
        import {createHash} from 'node:crypto';
        import * as shared from './engine/drawing/programs.js';
        globalThis.window = { location: { origin: 'http://viewer' } };
        const page = await import('./gui/source/drawing/layers.ts');
        const digest = text => createHash('sha256').update(text).digest('hex').slice(0, 16);
        const programs = [];
        for (const volumetric of [false, true])
          for (const lut of [null, ...shared.LOOKUP_TABLE_NAMES])
            for (const opaque of [false, true])
              programs.push([volumetric, lut, opaque, digest(shared.shaderFor(volumetric, lut, opaque))]);
        // What the page hands the engine for one channel, in each mode and with
        // a colour map: its program is the engine's, character for character.
        const config = { layers: [{ name: 'c0', group: 'overview', source: '/data/0', opaque: true,
                                    window: { low: 0, high: 100 } }] };
        const state = [{ visible: true, color: [1, 0, 1], lut: 'magma', opacity: 1, window: null }];
        const drawn = mode => page.layersFor(config, mode, state, {}, ['overview'])
          .find(layer => layer.name.endsWith('c0'));
        const samePage = ['flat', 'volume'].every(mode =>
          drawn(mode).shader === shared.shaderFor(mode === 'volume', 'magma', false));
        console.log(JSON.stringify({ programs, samePage }));
    """)
    assert answer["programs"] == PROGRAMS_BEFORE_THE_MOVE
    assert answer["samePage"] is True


def test_gamma_is_offered_only_when_asked_for():
    """The interface lets the operator bend the curve; the viewer's own page does not, yet.

    Asked for, gamma is one more control and one ``pow``; not asked for, the
    program is exactly what it was, because ``pow(v, 1.0)`` is not always
    exactly ``v`` on a graphics card.
    """
    answer = run_node("""
        import {shaderFor, shaderControlsFor} from './engine/drawing/programs.js';
        console.log(JSON.stringify({
          plain: shaderFor(false),
          bent: shaderFor(false, null, false, {gamma: true}),
          volume: shaderFor(true, null, false, {gamma: true}),
          controls: shaderControlsFor({low: 1, high: 9}, true, 0.5, [1, 0.5, 0], null, 2, 0.7),
          without: shaderControlsFor({low: 1, high: 9}, false, 1, [1, 1, 1]),
        }));
    """)
    assert "gamma" not in answer["plain"]
    assert answer["bent"].splitlines()[:5] == [
        "#uicontrol invlerp normalized",
        "#uicontrol float weight slider(min=0, max=1, default=1)",
        "#uicontrol float gamma slider(min=0.1, max=4, default=1)",
        "#uicontrol invlerp imaged(range=[0, 1], clamp=false)",
        '#uicontrol vec3 color color(default="white")',
    ]
    assert "  float v = pow(normalized(), gamma);" in answer["bent"].splitlines()
    assert "  float v = pow(normalized(), gamma);" in answer["volume"].splitlines()
    assert answer["controls"] == {
        "normalized": {"range": [1, 9]}, "weight": 0.5, "color": "#ff8000",
        "attenuation": 2, "gamma": 0.7,
    }
    assert "gamma" not in answer["without"]


def test_the_neuroglancer_edits_are_the_engines_and_the_page_keeps_no_table():
    answer = run_node("""
        import {transparencyPatches, refreshPatches} from './engine/drawing/neuroglancer-patches.mjs';
        const lib = 'LIB';
        const files = list => list.map(patch => patch.file.replaceAll('\\\\', '/').replace(/^LIB\\//, ''));
        console.log(JSON.stringify({
          transparency: files(transparencyPatches(lib)),
          refresh: files(refreshPatches(lib)),
        }));
    """)
    assert answer["transparency"] == [
        "display_context.js", "display_context.js", "perspective_view/panel.js",
        *["sliceview/panel.js"] * 7, "viewer.js", "viewer.js",
    ]
    assert answer["refresh"] == [
        "sliceview/backend_chunk_decoders/postprocess.js",
        *["chunk_manager/backend.js"] * 5, *["chunk_manager/frontend.js"] * 2,
    ]
    script = (ROOT / "gui/source/scripts/patch_neuroglancer.mjs").read_text(encoding="utf-8")
    assert "anchor:" not in script and "replacement:" not in script, (
        "the page's patch script should apply the engine's tables, not keep its own"
    )
