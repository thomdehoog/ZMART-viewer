"""Channels of one place mix; where positions overlap, the later one covers.

The rule (Thom, 2026-10-18) holds for every picture the viewer draws. A
picture the viewer composed holds its channels together and its positions
already laid in order, so its layers simply add. The case this file is about
is the other one: a folder the composer cannot make one picture of, opened
store by store, so one channel's row is drawn from several stores at once.

Neuroglancer blends a layer's stores with the layer's own blend, so such a
row could either add -- and sum overlapping positions into a bright seam --
or cover, and hide every channel beneath the top one. Split place by place,
it does both: each place stands on a black ground that covers what came
before it, and its channels are added on top.

The browser gate photographs the canvas: a channel that loads and draws
nothing is invisible to every number the engine reports.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import numpy as np
import pytest
import zarr
from zmart_viewer.serving.server import make_server

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
DIM = 1000
WINDOW = (0, 4000)
VOXEL = 0.35


def run_node(script: str) -> object:
    """Run *script* as a module from the repository root and read the JSON it prints."""
    if NODE is None:
        pytest.skip("node is not installed")
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


def drawn(rows: list[dict]) -> list[dict]:
    """What the viewer's page tells the engine for these rows of one acquisition."""
    return run_node(
        """
        globalThis.window = { location: { origin: 'http://viewer' } };
        const page = await import('./gui/source/drawing/layers.ts');
        const rows = %s;
        const state = rows.map(() => ({ visible: true, color: [1, 0, 0], lut: null,
                                        opacity: 1, window: null }));
        const layers = page.layersFor({ layers: rows }, 'flat', state, {}, ['overview']);
        console.log(JSON.stringify(layers.map(layer => ({
          name: layer.name, source: layer.source, blend: layer.blend ?? 'default',
          ground: layer.name.startsWith('__ground__'),
        }))));
        """
        % json.dumps(rows)
    )


def a_row(name: str, sources: list[str], places: list[str] | None = None) -> dict:
    row = {
        "name": name,
        "group": "overview",
        "sources": sources,
        "frameCounts": [1] * len(sources),
        "window": {"low": 0, "high": 100},
    }
    if places is not None:
        row["sourcePlaces"] = places
    return row


def test_each_place_stands_on_a_ground_and_its_channels_add():
    layers = drawn(
        [
            a_row("Ch488", ["/data/0/a_Ch488.zarr", "/data/0/b_Ch488.zarr"], ["a_", "b_"]),
            a_row("Ch561", ["/data/0/a_Ch561.zarr", "/data/0/b_Ch561.zarr"], ["a_", "b_"]),
        ]
    )

    assert [(layer["ground"], layer["source"], layer["blend"]) for layer in layers] == [
        (True, ["http://viewer/data/0/a_Ch488.zarr"], "default"),
        (False, ["http://viewer/data/0/a_Ch488.zarr"], "additive"),
        (False, ["http://viewer/data/0/a_Ch561.zarr"], "additive"),
        (True, ["http://viewer/data/0/b_Ch488.zarr"], "default"),
        (False, ["http://viewer/data/0/b_Ch488.zarr"], "additive"),
        (False, ["http://viewer/data/0/b_Ch561.zarr"], "additive"),
    ]
    assert len({layer["name"] for layer in layers}) == len(layers)


def test_a_store_holding_several_channels_is_one_place():
    """Rows that read one store (a channel axis) are the same place without being told."""
    layers = drawn(
        [
            a_row("chA", ["/data/0/a.zarr", "/data/0/b.zarr"]),
            a_row("chB", ["/data/0/a.zarr", "/data/0/b.zarr"]),
        ]
    )

    assert [(layer["ground"], layer["source"][0][-6:]) for layer in layers] == [
        (True, "a.zarr"),
        (False, "a.zarr"),
        (False, "a.zarr"),
        (True, "b.zarr"),
        (False, "b.zarr"),
        (False, "b.zarr"),
    ]


def test_one_channel_of_several_stores_covers_as_one_layer():
    """Nothing to mix: the positions cover, in one layer, as before."""
    layers = drawn([a_row("Ch488", ["/data/0/a_Ch488.zarr", "/data/0/b_Ch488.zarr"], ["a_", "b_"])])

    assert [(layer["ground"], len(layer["source"]), layer["blend"]) for layer in layers] == [
        (False, 2, "default"),
    ]


def a_store(path: Path, values: list[int], colours: list[str], shift_x: int) -> None:
    """A store of one channel per value, each flat at its value, placed ``shift_x`` voxels along."""
    group = zarr.open_group(str(path), mode="w", zarr_format=2)
    axes = [
        {"name": "z", "type": "space", "unit": "micrometer"},
        {"name": "y", "type": "space", "unit": "micrometer"},
        {"name": "x", "type": "space", "unit": "micrometer"},
    ]
    shape, scale, translation = [1, 64, 64], [1.0, VOXEL, VOXEL], [0.0, 0.0, shift_x * VOXEL]
    if len(values) > 1:
        axes.insert(0, {"name": "c", "type": "channel"})
        shape, scale, translation = [len(values), *shape], [1.0, *scale], [0.0, *translation]
    data = np.zeros(shape, dtype=np.uint16)
    for index, value in enumerate(values):
        data[index if len(values) > 1 else ...] = value
    group.create_array("0", shape=data.shape, chunks=data.shape, dtype="uint16")[:] = data
    channels = [
        {
            "label": f"ch{chr(ord('A') + index)}",
            "color": colour,
            "window": {"min": 0, "max": 65535, "start": WINDOW[0], "end": WINDOW[1]},
        }
        for index, colour in enumerate(colours)
    ]
    (path / ".zattrs").write_text(
        json.dumps(
            {
                "multiscales": [
                    {
                        "version": "0.4",
                        "axes": axes,
                        "datasets": [
                            {
                                "path": "0",
                                "coordinateTransformations": [
                                    {"type": "scale", "scale": scale},
                                    {"type": "translation", "translation": translation},
                                ],
                            }
                        ],
                    }
                ],
                "omero": {"channels": channels},
            }
        ),
        encoding="utf-8",
    )


#: Two places of one channel each per store, the channel named by wavelength.
ONE_CHANNEL_STORES = ["a_Ch488.ome.zarr", "a_Ch561.ome.zarr", "b_Ch488.ome.zarr", "b_Ch561.ome.zarr"]


@pytest.fixture
def two_places(tmp_path):
    folder = tmp_path / "specimen"
    folder.mkdir()
    for name in ONE_CHANNEL_STORES:
        a_store(folder / name, [DIM], ["FFFFFF"], 0 if name.startswith("a") else 32)
    return folder


def test_the_server_says_which_stores_show_the_same_place(two_places):
    server = make_server(port=0, data_dir=two_places, store=ONE_CHANNEL_STORES)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        address = f"http://127.0.0.1:{server.server_address[1]}/api/config"
        with urllib.request.urlopen(address, timeout=10) as answer:
            config = json.loads(answer.read())
    finally:
        server.shutdown()
        thread.join(timeout=5)
    rows = [row for row in config["layers"] if len(row.get("sources", [])) > 1]

    assert len(rows) == 2
    for row in rows:
        places = row["sourcePlaces"]
        assert len(places) == len(row["sources"]) == 2
        assert places[0] != places[1]
    assert rows[0]["sourcePlaces"] == rows[1]["sourcePlaces"]


def colours_across(page) -> list[np.ndarray]:
    """The picture's colour a sixth, a half and five sixths of the way across."""
    from PIL import Image

    box = page.locator("canvas").first.bounding_box()
    shot = np.array(Image.open(io.BytesIO(page.screenshot(clip=box))).convert("RGB"), dtype=float)
    middle = shot[shot.shape[0] // 2]
    lit = np.flatnonzero(middle.max(axis=1) > 8)
    assert lit.size, "nothing was drawn"
    left, right = lit[0], lit[-1]
    width = right - left
    return [middle[int(left + width * share)] for share in (1 / 6, 1 / 2, 5 / 6)]


#: Place a is red alone. Place b, half over a, is red and green together, so
#: its two channels mixing reads yellow, and either one hiding the other reads
#: a primary. Where b lies over a it must read b's yellow: a's red under it
#: added would read orange.
TWO_PLACES = {"a.ome.zarr": ([2 * DIM, 0], 0), "b.ome.zarr": ([DIM, DIM], 32)}


def test_on_screen_the_later_place_covers_and_its_channels_mix(browser, built_dist, tmp_path):
    folder = tmp_path / "specimen"
    folder.mkdir()
    for name, (values, shift) in TWO_PLACES.items():
        a_store(folder / name, values, ["FF0000", "00FF00"], shift)
    server = make_server(port=0, data_dir=folder, site_dir=built_dist, store=list(TWO_PLACES))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser.new_page(viewport={"width": 900, "height": 700})
    try:
        page.goto(f"http://127.0.0.1:{server.server_address[1]}", wait_until="domcontentloaded")
        page.wait_for_function("() => window.zmartConfig !== undefined", timeout=30_000)
        page.wait_for_function(
            "() => window.zmartSourcesWaiting && window.zmartSourcesWaiting() === 0",
            timeout=60_000,
        )
        page.wait_for_timeout(1_500)
        only_a, overlap, only_b = colours_across(page)
    finally:
        page.close()
        server.shutdown()
        thread.join(timeout=5)

    red, green = 0, 1
    assert only_a[red] > 60 and only_a[green] < 8, f"place a must read red alone: {only_a}"
    assert only_b[red] > 30 and only_b[green] > 30, (
        f"place b reads {only_b}: its red and green must both show, added together"
    )
    assert np.abs(overlap - only_b).max() <= 3, (
        f"where b lies over a the picture reads {overlap}, but b alone reads "
        f"{only_b}: the later place must cover, not add to, the earlier"
    )
