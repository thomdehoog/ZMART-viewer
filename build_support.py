"""Wheel frontend ownership: validate a completed build, then copy an exact tree.

The wheel may only carry a page that was built from the sources now in the
checkout. ``scripts/stamp-build.mjs`` writes a manifest of what went in and
what came out; this checks it before setuptools packages ``gui/dist``.
"""

import hashlib
import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.build_py import build_py
from setuptools.errors import SetupError


def _files(folder, skip=()):
    for here, directories, names in os.walk(folder):
        directories[:] = [d for d in directories if d not in skip]
        for name in names:
            yield Path(here) / name


def validate_frontend(root):
    """Refuse a page that was not built from exactly these sources."""
    root = Path(root)
    dist = root / "gui/dist"
    try:
        manifest = json.loads((dist / "build-manifest.json").read_text())
        inputs = [
            *_files(root / "gui", skip=("dist", "node_modules", "__pycache__")),
            *_files(root / "engine/drawing", skip=("__pycache__",)),
            *_files(root / "scripts"),
            root / "package.json", root / "package-lock.json", root / "vite.config.js",
        ]
        outputs = [p for p in _files(dist) if p.name != "build-manifest.json"]
        for paths, expected in ((inputs, manifest["inputs"]), (outputs, manifest["outputs"])):
            actual = {
                Path(os.path.relpath(p, root)).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in paths
            }
            if actual != expected:
                raise ValueError("frontend inputs or outputs changed after the build")
        if (
            not (dist / "index.html").is_file()
            or not (dist / "async_computation.bundle.js").is_file()
        ):
            raise ValueError("frontend entry point or worker missing")
    except (OSError, ValueError, KeyError) as error:
        raise SetupError(
            "Run npm run build successfully before building the wheel"
        ) from error


class BuildPy(build_py):
    def run(self):
        validate_frontend(Path("."))
        staging = Path(self.build_lib).resolve()
        frontend = (staging / "zmart_viewer/_frontend").resolve()
        frontend.relative_to(staging)
        if frontend.exists():
            shutil.rmtree(frontend)
        super().run()


class Wheel(bdist_wheel):
    def run(self):
        # Neither an old Python module nor an old asset may leak from build/lib.
        # Only this invocation's temporary staging is removed on exit.
        with TemporaryDirectory(prefix="zmart-wheel-") as staging:
            build = self.reinitialize_command("build", reinit_subcommands=True)
            build.build_base = staging
            build.build_lib = str(Path(staging) / "lib")
            self.bdist_dir = str(Path(staging) / "wheel")
            self.skip_build = False
            super().run()
