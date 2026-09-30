"""The wheel refuses a page that was not built from exactly these sources."""

import hashlib
import json

import pytest
from setuptools.errors import SetupError

from build_support import validate_frontend


def test_frontend_build_certificate(tmp_path):
    root = tmp_path
    interface = root / "gui"
    drawing = root / "engine" / "drawing"
    scripts = root / "scripts"
    for folder in (interface / "dist", drawing, scripts):
        folder.mkdir(parents=True)
    sources = {
        "gui/App.jsx": "app",
        "engine/drawing/embedding.js": "embedding",
        "engine/drawing/neuroglancer-growth.mjs": "growth",
        "scripts/stamp-build.mjs": "stamp",
        "package.json": "{}",
        "package-lock.json": "{}",
        "vite.config.js": "export default {}",
    }
    outputs = {
        "gui/dist/index.html": "<html>",
        "gui/dist/async_computation.bundle.js": "worker",
    }
    for name, text in {**sources, **outputs}.items():
        (root / name).write_text(text)
    with pytest.raises(SetupError, match="build successfully"):
        validate_frontend(root)
    manifest = {
        kind: {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}
        for kind, names in (("inputs", sources), ("outputs", outputs))
    }
    (interface / "dist/build-manifest.json").write_text(json.dumps(manifest))
    validate_frontend(root)
    for name in (*sources, *outputs):
        original = (root / name).read_bytes()
        (root / name).write_text("changed or incomplete")
        with pytest.raises(SetupError):
            validate_frontend(root)
        (root / name).write_bytes(original)
    for name in ("gui/new.js", "engine/drawing/new.js", "gui/dist/retired-worker.js"):
        (root / name).write_text("new")
        with pytest.raises(SetupError):
            validate_frontend(root)
        (root / name).unlink()
    validate_frontend(root)
