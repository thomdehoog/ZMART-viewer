"""Wheel frontend ownership: validate a completed build, then copy an exact tree."""

import hashlib
import json
import shutil
from pathlib import Path

from setuptools.command.build_py import build_py
from setuptools.errors import SetupError


def validate_frontend(page):
    try:
        manifest = json.loads((page / "dist/build-manifest.json").read_text())
        inputs = [p for p in page.iterdir() if p.is_file()]
        inputs += [
            p for folder in ("src", "scripts") for p in (page / folder).rglob("*") if p.is_file()
        ]
        outputs = [
            p for p in (page / "dist").rglob("*") if p.is_file() and p.name != "build-manifest.json"
        ]
        for paths, expected in ((inputs, manifest["inputs"]), (outputs, manifest["outputs"])):
            actual = {
                p.relative_to(page).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in paths
            }
            if actual != expected:
                raise ValueError("frontend inputs or outputs changed after the build")
        if (
            not (page / "dist/index.html").is_file()
            or not (page / "dist/async_computation.bundle.js").is_file()
        ):
            raise ValueError("frontend entry point or worker missing")
    except (OSError, ValueError, KeyError) as error:
        raise SetupError(
            "Run npm --prefix app/page run build successfully before building the wheel"
        ) from error


class BuildPy(build_py):
    def run(self):
        validate_frontend(Path("app/page"))
        staging = Path(self.build_lib).resolve()
        frontend = (staging / "zmart_viewer/_frontend").resolve()
        frontend.relative_to(staging)
        if frontend.exists():
            shutil.rmtree(frontend)
        super().run()
