"""A request the server never answers is said, never thrown.

The page's short conversations with the server (`gui/source/asking-python`)
each answer with something the page can show. Four of them did not catch a
server that dropped the connection: the promise was rejected, and the button
that asked stayed busy for good -- the store list never came back, a close
never finished, a build stayed "running" with nothing to end it. A poll of a
build that goes unanswered is not an error: the build may well still be
running, so the page asks again rather than ending it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "gui" / "source" / "asking-python.ts"


def _answers_with_no_server(expression: str):
    finished = subprocess.run(
        [
            "node",
            "--input-type=module",
            "--eval",
            (
                "globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));\n"
                f"const m = await import({json.dumps(MODULE.as_uri())});\n"
                f"const answer = await ({expression});\n"
                "console.log(JSON.stringify(answer ?? null));"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if finished.returncode == 127:
        pytest.skip("node is not installed, so the page's requests could not be run")
    assert finished.returncode == 0, finished.stderr
    return json.loads(finished.stdout)


@pytest.mark.parametrize(
    "call",
    [
        "m.startConstruction('/data/run', '/views', false)",
        "m.listFolders(null)",
        "m.closeGroup('overview')",
    ],
)
def test_a_dropped_server_is_an_error_the_page_can_show(call):
    answer = _answers_with_no_server(call)
    assert isinstance(answer, dict) and answer.get("error"), answer
    assert "did not answer" in answer["error"]


def test_an_unanswered_poll_of_a_build_is_no_answer_not_a_failed_build():
    assert _answers_with_no_server("m.constructionStatus()") is None
