"""Exercise the installed engine patch's lifecycle without a browser startup."""

import json
import subprocess
from pathlib import Path


def test_geometry_refresh_waits_for_initial_binding_and_cancels_on_disposal():
    root = Path(__file__).resolve().parents[1] / "app/page"
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "--eval",
            r"""
        import assert from 'node:assert/strict';
        import {RefCounted} from 'neuroglancer/unstable/util/disposable.js';
        import {NullarySignal} from 'neuroglancer/unstable/util/signal.js';
        import {refreshGeometry, geometryRefreshPending} from '../../zmart_viewer/embedding.js';
        for (const dispose of [false, true]) {
          const source = new RefCounted();
          source.changed = new NullarySignal();
          source.spec = {url:'http://test/view/|zarr3:'};
          source.layer = {dataSources:[source]};
          let refreshed = 0;
          source.refreshMetadata = async () => {
            assert.ok(source.loadState);
            refreshed++;
            return true;
          };
          const manager = {memoize:{map:new Map()}};
          const pending = refreshGeometry(source, manager, new Set(), new Set());
          assert.equal(refreshed, 0);
          assert.equal(geometryRefreshPending(source), true);
          if (dispose) {
            source.layer.dataSources = [];
            source.dispose();
          } else {
            source.loadState = {};
            source.changed.dispatch();
          }
          await pending;
          assert.equal(refreshed, dispose ? 0 : 1);
          assert.equal(geometryRefreshPending(source), false);
          if (!dispose) source.dispose();
        }
        """,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_top_bounds_use_transforms_when_global_depth_units_change():
    root = Path(__file__).resolve().parents[1] / "app/page"
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "--eval",
            r"""
        import assert from 'node:assert/strict';
        import {readFileSync} from 'node:fs';
        import {CoordinateSpaceCombiner, WatchableCoordinateSpaceTransform,
          makeCoordinateSpace, makeIdentityTransform, makeIdentityTransformedBoundingBox,
          emptyInvalidCoordinateSpace} from 'neuroglancer/unstable/coordinate_transform.js';
        import {WatchableValue} from 'neuroglancer/unstable/trackable_value.js';
        import {NullarySignal} from 'neuroglancer/unstable/util/signal.js';
        const {keepDepthLocal} = await import('../../zmart_viewer/embedding.js');
        const keep = (layer, viewer) => keepDepthLocal(layer, viewer,
          transform => new WatchableCoordinateSpaceTransform(transform));
        const space = (scale, depth) => makeCoordinateSpace({names:['z'], units:['m'],
          scales:Float64Array.of(scale), boundingBoxes:[makeIdentityTransformedBoundingBox({
            lowerBounds:Float64Array.of(0), upperBounds:Float64Array.of(depth)})]});
        for (const reverse of [false,true]) {
          const global = new WatchableValue(emptyInvalidCoordinateSpace);
          const combiner = new CoordinateSpaceCombiner(global, () => true);
          const viewer = {layerSpecification:{coordinateSpaceCombiner:combiner},
            navigationState:{position:{changed:new NullarySignal(), coordinateSpace:global}}};
          const sources = [], disposers = [];
          for (const [scale,depth] of (reverse ? [[2e-6,5],[1e-6,2]] : [[1e-6,2],[2e-6,5]])) {
            const source = {changed:new NullarySignal(), loadState:{transform:
              new WatchableCoordinateSpaceTransform(makeIdentityTransform(space(scale,depth)))}};
            sources.push(source);
            const local = new WatchableValue(emptyInvalidCoordinateSpace);
            keep({dataSources:[source], localCoordinateSpace:local,
              registerDisposer:d => disposers.push(d)}, viewer);
          }
          const bounds = () => [global.value.bounds.lowerBounds[0]*global.value.scales[0],
                                global.value.bounds.upperBounds[0]*global.value.scales[0]];
          assert.ok(Math.abs(bounds()[0]) < 1e-12);
          assert.ok(Math.abs(bounds()[1]-10e-6) < 1e-12, JSON.stringify(bounds()));
          // A metadata replacement must also update the native global range.
          const long = sources[reverse ? 0 : 1];
          long.loadState.transform = new WatchableCoordinateSpaceTransform(makeIdentityTransform(space(2e-6,7)));
          long.changed.dispatch();
          assert.ok(Math.abs(bounds()[1]-14e-6) < 1e-12, JSON.stringify(bounds()));
          for (const dispose of disposers.reverse()) dispose();
          assert.equal(combiner.bindings.size, 0);
        }
        // Another acquisition changes local units, not this source's plane lattice.
        for (const depth of [1, 5]) {
          const scale = depth === 1 ? 1e-6 : 2e-6;
          const native = makeCoordinateSpace({names:['z'], units:['m'],
            scales:Float64Array.of(scale), boundingBoxes:[makeIdentityTransformedBoundingBox({
              lowerBounds:Float64Array.of(-0.5), upperBounds:Float64Array.of(depth-0.5)})]});
          const global = new WatchableValue(space(1e-6, 40));
          const combiner = new CoordinateSpaceCombiner(global, () => true);
          const position = {changed:new NullarySignal(), coordinateSpace:global, value:Float32Array.of(0)};
          const viewer = {layerSpecification:{coordinateSpaceCombiner:combiner},
            navigationState:{position}};
          const source = {changed:new NullarySignal(), loadState:{transform:
            new WatchableCoordinateSpaceTransform(makeIdentityTransform(native))}};
          const local = new WatchableValue(makeCoordinateSpace({names:["z'"], units:['m'],
            scales:Float64Array.of(1.133333e-6)}));
          const localPosition = {value:Float32Array.of(0)};
          const disposers = [];
          keep({dataSources:[source], localCoordinateSpace:local, localPosition,
            registerDisposer:d => disposers.push(d)}, viewer);
          for (const requested of [-20, 0, 20]) {
            position.value = Float32Array.of(requested);
            position.changed.dispatch();
            const actual = localPosition.value[0]*local.value.scales[0];
            const wanted = Math.max(0, Math.min((depth-1)*scale, requested*global.value.scales[0]));
            assert.ok(Math.abs(actual-wanted) < 1e-11, `${actual} != ${wanted}`);
          }
          for (const dispose of disposers.reverse()) dispose();
        }
    """,
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_metadata_refresh_clears_pending_and_detaches_before_invalidation():
    script = Path(__file__).resolve().parents[1] / "zmart_viewer/neuroglancer-growth.mjs"
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "--eval",
            f"""
        import assert from 'node:assert/strict';
        const {{growthPatches}} = await import({json.dumps(script.as_uri())});
        const patch = growthPatches('').find(p => p.marker.startsWith('async refreshMetadata'));
        const method = patch.replacement.split('\\n  set spec(spec)')[0];
        const Source = new Function('stableStringify', `return class {{
          ${{method}}
          get spec() {{ return {{url: 'test'}}; }}
          set spec(value) {{ this.detached = true; }}
        }}`)(JSON.stringify);
        for (const held of [undefined, {{error: new Error('initial read failed')}}]) {{
          const source = new Source();
          source.loadState_ = held;
          source.metadataRefresh = new AbortController();
          await source.refreshMetadata();
          assert.equal(source.metadataRefresh, undefined);
          assert.equal(source.detached, true);
        }}
        const source = new Source();
        let invalidations = 0;
        const holder = {{parameters: {{url:'test', metadata: {{shape:[1]}}}}, invalidateCache() {{
          assert.equal(source.detached, true);
          invalidations++;
        }}}};
        source.refCounted_ = {{registerDisposer() {{}}, unregisterDisposer() {{}}}};
        source.loadState_ = {{subsources: [{{subsourceEntry: {{id: 'volume', subsource: {{
          volume: {{sourceCache: new Map([['shared', [[{{chunkSource: holder}}]]]])}}
        }}}}}}]}};
        source.layer = {{manager: {{root: {{}}, dataSourceProviderRegistry: {{
          async get() {{ return {{subsources: [{{id:'volume', subsource: {{volume: {{
            multiscale: {{scales:[{{url:'test', metadata:{{shape:[1]}}}}]}}
          }}}}}}]}}; }}
        }}}}}};
        const refreshed = new Set();
        assert.equal(await source.refreshMetadata(refreshed), true);
        assert.equal(await source.refreshMetadata(refreshed), true);
        assert.equal(invalidations, 1);
        assert.equal(source.metadataRefresh, undefined);
        const patches = growthPatches('');
        const extension = patches.find(p => p.marker === 'extendBounds(other, refreshed)').replacement;
        const extend = extension.slice(extension.indexOf('  extendBounds('), extension.indexOf('  get dataType()'));
        const Volume = new Function(`return class {{ ${{extend}} }}`)();
        let receive;
        const workerPatch = patches.find(p => p.marker.startsWith('registerRPC("zarr/extendBounds"')).replacement;
        new Function('registerRPC', workerPatch.split('export let ZarrVolumeChunkSource')[0])(
          (name, handler) => receive = handler);
        const spec = () => ({{rank: 1, upperVoxelBound: new Float32Array([1]),
          upperChunkBound: new Float32Array([1]), chunkDataSize: [1]}});
        let workerInvalidations = 0;
        const worker = {{spec: spec(), parameters: {{metadata: {{shape: [1]}}}},
          chunkManager: {{queueManager: {{invalidateSourceCache() {{workerInvalidations++;}}}}}}}};
        const chunk = {{spec: spec(), parameters: {{url: 'test'}}, rpcId: 1,
          rpc: {{invoke(name, payload) {{receive.call({{get: () => worker}}, payload);}}}}}};
        const volume = new Volume();
        volume.sourceCache = new Map([['shared', [[{{chunkSource: chunk}}]]]]);
        const other = {{multiscale: {{scales: [{{url: 'test', metadata: {{shape: [3],
          codecs: {{layoutInfo: [{{physicalToLogicalDimension: [0]}}]}}
        }}}}]}}}};
        // Pixels may have been refreshed already, but worker bounds must still grow.
        volume.extendBounds(other, new Set([chunk]));
        assert.deepEqual(worker.parameters.metadata.shape, [3]);
        assert.equal(worker.spec.upperVoxelBound[0], 3);
        assert.equal(workerInvalidations, 0);
        volume.extendBounds(other, new Set());
        assert.equal(workerInvalidations, 1);
        """,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
