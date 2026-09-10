"""Exercise the installed engine patch's lifecycle without a browser startup."""

import json
import subprocess
from pathlib import Path


def test_metadata_refresh_clears_pending_and_detaches_before_invalidation():
    script = Path(__file__).resolve().parents[1] / "app/page/scripts/patch_neuroglancer_growth.mjs"
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
