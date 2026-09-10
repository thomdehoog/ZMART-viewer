"""Packaging rejects incomplete or stale frontend builds before copying them."""

import hashlib
import json

import pytest
from setuptools.errors import SetupError

from build_support import validate_frontend


def test_frontend_build_certificate(tmp_path):
    page = tmp_path / "page"
    (page / "src").mkdir(parents=True)
    (page / "scripts").mkdir()
    (page / "public").mkdir()
    (page / "dist").mkdir()
    for name in ("src/app.js", "dist/index.html", "dist/async_computation.bundle.js"):
        (page / name).write_text(name)
    with pytest.raises(SetupError, match="build successfully"):
        validate_frontend(page)
    manifest = {}
    for kind, names in (
        ("inputs", ["src/app.js"]),
        ("outputs", ["dist/index.html", "dist/async_computation.bundle.js"]),
    ):
        manifest[kind] = {
            name: hashlib.sha256((page / name).read_bytes()).hexdigest() for name in names
        }
    (page / "dist/build-manifest.json").write_text(json.dumps(manifest))
    validate_frontend(page)
    for name in ("src/app.js", "dist/index.html", "dist/async_computation.bundle.js"):
        original = (page / name).read_bytes()
        (page / name).write_text("changed or incomplete")
        with pytest.raises(SetupError):
            validate_frontend(page)
        (page / name).write_bytes(original)
    for name in ("src/new.js", "public/new.js", "dist/retired-worker.js"):
        (page / name).write_text("unexpected")
        with pytest.raises(SetupError):
            validate_frontend(page)
        (page / name).unlink()
