"""``zmart_viewer.__version__`` is the version that was installed."""

import tomllib
from pathlib import Path

import zmart_viewer


def test_the_version_matches_pyproject():
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert zmart_viewer.__version__ == project["project"]["version"]
