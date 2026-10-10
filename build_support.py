"""Wheel frontend ownership: validate a completed build, then copy an exact tree.

The wheel may only carry a page that was built from the sources now in the
checkout. ``gui/source/scripts/stamp-build.mjs`` writes a manifest of what went
in and what came out; this checks it before setuptools packages ``gui/build``.
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
    built = root / "gui/build"
    try:
        manifest = json.loads((built / "build-manifest.json").read_text())
        inputs = [
            *_files(root / "gui/source", skip=("node_modules",)),
            *_files(root / "engine/drawing", skip=("__pycache__",)),
        ]
        outputs = [p for p in _files(built) if p.name != "build-manifest.json"]
        for paths, expected in ((inputs, manifest["inputs"]), (outputs, manifest["outputs"])):
            actual = {
                Path(os.path.relpath(p, root)).as_posix(): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in paths
            }
            if actual != expected:
                raise ValueError(what_differs(actual, expected))
        for needed in ("index.html", "async_computation.bundle.js"):
            if not (built / needed).is_file():
                raise ValueError(f"gui/build/{needed} is missing")
    except (OSError, ValueError, KeyError) as error:
        raise SetupError(
            f"{error}. Run npm run build successfully before building the wheel"
        ) from error


def what_differs(actual, expected):
    """Name the files that differ from the build's record, a few at most.

    A developer told only that "something changed" has to hunt for it; a file
    left behind by a file browser (``.DS_Store``, ``Thumbs.db``) is the usual
    culprit and is easy to remove once it is named.
    """
    reasons = [
        *(f"{name} is not part of the build" for name in sorted(actual.keys() - expected.keys())),
        *(f"{name} is missing" for name in sorted(expected.keys() - actual.keys())),
        *(
            f"{name} changed after the build"
            for name in sorted(actual.keys() & expected.keys())
            if actual[name] != expected[name]
        ),
    ]
    shown = "; ".join(reasons[:5])
    if len(reasons) > 5:
        shown += f"; and {len(reasons) - 5} more"
    return shown


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
