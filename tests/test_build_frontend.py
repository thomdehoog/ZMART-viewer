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


def a_checked_build(root):
    """Write the smallest page that passes the check, and return its root."""
    for folder in (root / "gui" / "build", root / "gui" / "source", root / "engine" / "drawing"):
        folder.mkdir(parents=True)
    sources = {"gui/source/App.jsx": "app", "engine/drawing/embedding.js": "embedding"}
    outputs = {
        "gui/build/index.html": "<html>",
        "gui/build/async_computation.bundle.js": "worker",
    }
    for name, text in {**sources, **outputs}.items():
        (root / name).write_text(text)
    manifest = {
        kind: {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}
        for kind, names in (("inputs", sources), ("outputs", outputs))
    }
    (root / "gui/build/build-manifest.json").write_text(json.dumps(manifest))
    validate_frontend(root)
    return root


def test_the_refusal_names_the_file_that_was_changed(tmp_path):
    """A changed source is named, so the developer knows which file to look at."""
    root = a_checked_build(tmp_path)
    (root / "gui/source/App.jsx").write_text("edited after the build")
    with pytest.raises(SetupError) as refused:
        validate_frontend(root)
    assert "gui/source/App.jsx" in str(refused.value)
    assert "changed" in str(refused.value)


def test_the_refusal_names_a_stray_file(tmp_path):
    """A file the build never saw, such as one a file browser leaves behind, is named."""
    root = a_checked_build(tmp_path)
    (root / "gui/source/.DS_Store").write_text("left by a file browser")
    with pytest.raises(SetupError) as refused:
        validate_frontend(root)
    assert "gui/source/.DS_Store" in str(refused.value)
    assert "not part of the build" in str(refused.value)


def test_the_refusal_names_a_missing_file(tmp_path):
    root = a_checked_build(tmp_path)
    (root / "gui/build/index.html").unlink()
    with pytest.raises(SetupError) as refused:
        validate_frontend(root)
    assert "gui/build/index.html" in str(refused.value)
    assert "missing" in str(refused.value)
