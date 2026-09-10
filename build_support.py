"""Wheel frontend ownership: validate a completed build, then copy an exact tree."""

import hashlib
import json
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.build_py import build_py
from setuptools.errors import SetupError


def validate_frontend(page):
    try:
        manifest = json.loads((page / "dist/build-manifest.json").read_text())
        inputs = []
        for folder, directories, files in os.walk(page):
            directories[:] = [d for d in directories if d not in ("node_modules", "dist")]
            inputs.extend(Path(folder) / name for name in files)
        inputs.extend(page / "../../zmart_viewer" / name
                      for name in ("embedding.js", "neuroglancer-growth.mjs"))
        outputs = [
            p for p in (page / "dist").rglob("*") if p.is_file() and p.name != "build-manifest.json"
        ]
        for paths, expected in ((inputs, manifest["inputs"]), (outputs, manifest["outputs"])):
            actual = {
                Path(os.path.relpath(p, page)).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
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
