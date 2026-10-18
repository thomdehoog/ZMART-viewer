"""A piece whose download was cancelled never writes into the engine's chunk.

When a store is refreshed twice in quick succession, the first refresh's
re-download of a piece can still be decoding when the second refresh cancels
it and starts another. The engine hands decoding to a helper worker and, when
one is free, does not take the task back on cancelling, so the first decode
finishes anyway -- and it used to write its older pixels into the chunk the
second download had just filled. On a busy machine that late write came after
the second one, the older picture was what reached the screen, and it stayed
there: every visible piece reported as available, nothing pending, the wrong
pixels kept until something refreshed the store again
(`test_mixed_acquisition.py::test_browser_mixed_relative_depth`, which failed
in three of four full suites, and reliably at eight times CPU throttling).

The cure is one line in the engine, applied with the refresh edits in
``engine/drawing/neuroglancer-patches.mjs`` so both pages get it: a cancelled
download stops before it writes. This checks it on the engine's own decoder.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "gui" / "source"
NODE = shutil.which("node")


def what_the_chunk_holds_after(cancelled: bool) -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    if not (SOURCE / "node_modules" / "neuroglancer").is_dir():
        pytest.skip("the page's node modules are not installed")
    script = """
      const decoders = await import(
        'neuroglancer/unstable/sliceview/backend_chunk_decoders/postprocess.js');
      const chunk = { data: 'the second download', source: { spec: {} } };
      const download = new AbortController();
      if (%s) download.abort();
      let threw = null;
      try {
        await decoders.postProcessRawData(chunk, download.signal, 'the first download');
      } catch (error) {
        threw = error.name;
      }
      console.log(JSON.stringify({ data: chunk.data, threw }));
    """ % ("true" if cancelled else "false")
    finished = subprocess.run(
        [NODE, "--input-type=module", "--eval", script],
        cwd=SOURCE,
        capture_output=True,
        text=True,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


def test_a_cancelled_download_leaves_the_chunk_as_it_was():
    held = what_the_chunk_holds_after(cancelled=True)

    assert held == {"data": "the second download", "threw": "AbortError"}, (
        "a download cancelled by a later refresh wrote its older pixels into the chunk"
    )


def test_a_download_that_runs_its_course_still_lands():
    held = what_the_chunk_holds_after(cancelled=False)

    assert held == {"data": "the first download", "threw": None}
