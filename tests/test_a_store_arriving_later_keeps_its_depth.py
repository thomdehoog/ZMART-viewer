"""A store handed to a depth-keeping layer after it was built keeps its depth too.

A layer that keeps its depth to itself -- a flat row, a projection, Top --
renames each store's `z` to the layer's own `z'`, so moving through the stack
does not move it. The rename used to be made once, for the stores the layer was
built with. A layer is built with at most one group of stores and fed the rest
afterwards, and a run's stores arrive one at a time, so every store after the
first group kept the shared `z`: those pieces moved with the stack while the
first ones stayed put, and outside its own planes a later store vanished.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "gui" / "source"
NODE = shutil.which("node")


def test_a_store_added_after_the_layer_is_built_keeps_its_depth_local():
    if NODE is None or not (SOURCE / "node_modules" / "neuroglancer").is_dir():
        pytest.skip("node and the page's node modules are needed")
    finished = subprocess.run(
        [
            NODE,
            "--input-type=module",
            "--eval",
            r"""
        import assert from 'node:assert/strict';
        import {WatchableCoordinateSpaceTransform, makeCoordinateSpace, makeIdentityTransform,
          makeIdentityTransformedBoundingBox} from 'neuroglancer/unstable/coordinate_transform.js';
        import {NullarySignal} from 'neuroglancer/unstable/util/signal.js';
        const {keepDepthLocal} = await import('../../engine/drawing/embedding.js');
        const space = makeCoordinateSpace({names:['z','y','x'], units:['m','m','m'],
          scales:Float64Array.of(1e-6,1e-6,1e-6), boundingBoxes:[makeIdentityTransformedBoundingBox({
            lowerBounds:Float64Array.of(0,0,0), upperBounds:Float64Array.of(1,4,4)})]});
        const aStore = () => ({changed:new NullarySignal(), loadState:{transform:
          new WatchableCoordinateSpaceTransform(makeIdentityTransform(space))}});
        const disposers = [];
        const layer = {dataSources:[aStore()], dataSourcesChanged:new NullarySignal(),
          registerDisposer:d => disposers.push(d)};
        keepDepthLocal(layer);
        const depthOf = source => Array.from(source.loadState.transform.value.outputSpace.names);
        assert.deepEqual(depthOf(layer.dataSources[0]), ["z'", 'y', 'x']);

        // The feeding hands the layer another store, as a later group or a later sync does.
        const later = aStore();
        layer.dataSources.push(later);
        layer.dataSourcesChanged.dispatch();
        assert.deepEqual(depthOf(later), ["z'", 'y', 'x'], 'a store added later kept the shared z');

        // A store already kept is not kept twice.
        layer.dataSourcesChanged.dispatch();
        assert.deepEqual(depthOf(layer.dataSources[0]), ["z'", 'y', 'x']);
        for (const dispose of disposers.reverse()) dispose();
        console.log('ok');
    """,
        ],
        cwd=SOURCE,
        capture_output=True,
        text=True,
        check=False,
    )
    assert finished.returncode == 0, finished.stdout + finished.stderr
