"""A wheel must serve its built page without an adjacent source checkout."""

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
from test_view_sampling import write_tile

from zmart_viewer.views import ViewSet


def test_installed_wheel_serves_page_and_workers(tmp_path, built_dist):
    repo = Path(__file__).resolve().parents[1]
    # Build from the current tracked sources without touching checkout staging.
    checkout = tmp_path / "source"
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo).decode().split("\0")
    for name in filter(None, tracked):
        target = checkout / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / name, target)
    shutil.copytree(built_dist, checkout / "app/page/dist")
    positions = tmp_path / "positions"
    positions.mkdir()
    write_tile(positions, "p.ome.zarr", np.full((1, 1, 3, 8, 8), 40000, dtype="uint16"))
    view = ViewSet(
        tmp_path / "view",
        acquisition="a",
        projections=("min", "max", "sum"),
        projection_folder=tmp_path / "projections",
        piece=4,
    )
    try:
        view.publish(
            positions,
            {"p.ome.zarr": 1},
            {"x_um": [0, 8], "y_um": [0, 8]},
            composition={"regions": "complete", "order": ["p.ome.zarr"]},
        )
        expected = {
            name: output.composer().bytes_for(2, 0, 0, 0).hex()
            for name, output in view.outputs.items()
        }
    finally:
        view.close()
    wheel_dir = tmp_path / "wheels"
    staging = checkout / "build/lib/zmart_viewer"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "retired.py").write_text("raise RuntimeError('retired staging module')\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            str(checkout),
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheel_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    (wheel,) = wheel_dir.glob("zmart_viewer-*.whl")
    with zipfile.ZipFile(wheel) as archive:
        prefix = "zmart_viewer/"
        packaged = {
            name[len(prefix) :]: archive.read(name)
            for name in archive.namelist()
            if name.startswith(prefix)
        }
        expected_files = {
            "_frontend/" + p.relative_to(built_dist).as_posix(): p.read_bytes()
            for p in built_dist.rglob("*")
            if p.is_file()
        }
        expected_files.update(
            {
                p.relative_to(repo / "zmart_viewer").as_posix(): p.read_bytes()
                for p in (repo / "zmart_viewer").rglob("*.py")
            }
        )
        assert packaged == expected_files, (
            "Wheel must contain exactly this build, with no retired assets"
        )
    installed = tmp_path / "installed"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--target",
            str(installed),
            str(wheel),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    environment = {
        **os.environ,
        "PYTHONPATH": str(installed),
        "ZMART_TEST_SAVED_VIEW": str(tmp_path / "view"),
        "ZMART_TEST_EXPECTED": json.dumps(expected),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib.metadata, pathlib, re, tempfile, threading, urllib.request, os, json
import zmart_viewer
from zmart_viewer.server import make_server, _FRONTEND_DIST
from zmart_viewer.views import ViewSet
assert 'installed' in pathlib.Path(zmart_viewer.__file__).parts
assert importlib.metadata.version('zmart-viewer') == '0.4.0'
assert _FRONTEND_DIST.name == '_frontend'
with tempfile.TemporaryDirectory() as data:
    saved = pathlib.Path(os.environ['ZMART_TEST_SAVED_VIEW'])
    server = make_server(port=0,data_dir=pathlib.Path(data),live=False,loads=[{'path':str(saved)}])
    worker = threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    address = 'http://127.0.0.1:'+str(server.server_address[1])
    try:
        page = urllib.request.urlopen(address).read().decode()
        assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"',page)
        assert assets
        for asset in assets:
            assert len(urllib.request.urlopen(address+asset).read()) > 1000
        workers = list(_FRONTEND_DIST.rglob('*worker*.js'))
        assert workers, 'Wheel has no Neuroglancer workers'
        for file in workers:
            body=urllib.request.urlopen(address+'/'+file.relative_to(_FRONTEND_DIST).as_posix()).read()
            assert len(body)>100000
        config = json.load(urllib.request.urlopen(address+'/api/config'))
        assert len(config['layers']) == 5
        expected = json.loads(os.environ['ZMART_TEST_EXPECTED'])
        for row in config['layers']:
            source = row['sources'][0].split('|')[0]
            name = source.rstrip('/').rsplit('/',1)[-1]
            actual = urllib.request.urlopen(address+source+'2/c/0/0/0').read()
            assert actual == bytes.fromhex(expected[name]), name
            if row['view'].get('method') == 'sum':
                assert row['window']['high'] > 65535, row
    finally:
        server.shutdown();server.server_close();worker.join(5)
print('Installed 0.4.0 page, workers and all five saved views served without the checkout')
""",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
