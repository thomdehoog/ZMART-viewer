"""The wheel refuses a page that was not built from exactly these sources."""

import hashlib
import json

import pytest
from setuptools.errors import SetupError

from build_support import validate_frontend


def test_frontend_build_certificate(tmp_path):
    root = tmp_path
    source = root / "gui" / "source"
    drawing = root / "engine" / "drawing"
    for folder in (root / "gui" / "build", source / "drawing", source / "scripts", drawing):
        folder.mkdir(parents=True)
    sources = {
        "gui/source/App.jsx": "app",
        "gui/source/drawing/viewer.js": "viewer",
        "gui/source/scripts/stamp-build.mjs": "stamp",
        "gui/source/package.json": "{}",
        "gui/source/package-lock.json": "{}",
        "gui/source/vite.config.js": "export default {}",
        "engine/drawing/embedding.js": "embedding",
        "engine/drawing/neuroglancer-growth.mjs": "growth",
    }
    outputs = {
        "gui/build/index.html": "<html>",
        "gui/build/async_computation.bundle.js": "worker",
    }
    for name, text in {**sources, **outputs}.items():
        (root / name).write_text(text)
    with pytest.raises(SetupError, match="build successfully"):
        validate_frontend(root)
    manifest = {
        kind: {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}
        for kind, names in (("inputs", sources), ("outputs", outputs))
    }
    (root / "gui/build/build-manifest.json").write_text(json.dumps(manifest))
    validate_frontend(root)
    for name in (*sources, *outputs):
        original = (root / name).read_bytes()
        (root / name).write_text("changed or incomplete")
        with pytest.raises(SetupError):
            validate_frontend(root)
        (root / name).write_bytes(original)
    for name in (
        "gui/source/new.js",
        "gui/source/drawing/new.js",
        "engine/drawing/new.js",
        "gui/build/retired-worker.js",
    ):
        (root / name).write_text("new")
        with pytest.raises(SetupError):
            validate_frontend(root)
        (root / name).unlink()
    (source / "node_modules").mkdir()
    (source / "node_modules" / "downloaded.js").write_text("not a source")
    (root / "gui" / "window.py").write_text("# opens the window, not part of the page")
    validate_frontend(root)
