"""A rebuild consumes changed sources and never certifies an old patch body."""

import subprocess
from pathlib import Path


def test_growth_patch_checks_body_and_worker_rebuild_reads_modules(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "--eval",
            r"""
      import assert from 'node:assert/strict';
      import {mkdirSync, readFileSync, writeFileSync} from 'node:fs';
      import {dirname, join} from 'node:path';
      import {build} from 'esbuild';
      import {applyGrowthPatches, growthPatches, workerEntry} from '../../zmart_viewer/neuroglancer-growth.mjs';
      const lib = process.argv[1];
      for (const {file,anchor} of growthPatches(lib)) {
        mkdirSync(dirname(file), {recursive:true});
        let text = '';
        try {text = readFileSync(file,'utf8');} catch {}
        writeFileSync(file,text+'\n'+anchor+'\n');
      }
      applyGrowthPatches(lib);
      applyGrowthPatches(lib);
      const patch = growthPatches(lib).find(p => p.marker.includes('registerRPC("zarr/extendBounds"'));
      writeFileSync(patch.file,readFileSync(patch.file,'utf8').replace('if (!source) return;', 'if (false) return;'));
      assert.throws(()=>applyGrowthPatches(lib), /Outdated Neuroglancer/);
      const name='test.bundle.js';
      writeFileSync(join(lib,name),'import "./source.js";');
      writeFileSync(join(lib,'source.js'),'globalThis.value="first";');
      const compile=async()=>build({entryPoints:[workerEntry(lib,name)],bundle:true,write:false,format:'esm'});
      assert.match((await compile()).outputFiles[0].text,/first/);
      writeFileSync(join(lib,name),'// compiled output\n'.repeat(4000));
      writeFileSync(join(lib,'source.js'),'globalThis.value="second";');
      assert.match((await compile()).outputFiles[0].text,/second/);
    """,
            str(tmp_path),
        ],
        cwd=root / "app/page",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
