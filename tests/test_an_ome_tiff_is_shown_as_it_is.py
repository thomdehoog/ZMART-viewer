"""An OME-TIFF file opens in the viewer without being converted.

The viewer draws with Neuroglancer, which reads only OME-Zarr. These tests
check that an OME-TIFF is presented as OME-Zarr all the same: a small
description is written into the viewer's own folder (never beside the file),
and every piece Neuroglancer asks for is read from the TIFF when it is asked
for. The pixels that arrive must be exactly the pixels in the file, at every
pyramid level, for every channel and plane, including the partly filled
pieces at the right and bottom edges.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

VIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VIZ))

tifffile = pytest.importorskip("tifffile")
numcodecs = pytest.importorskip("numcodecs")

from zmart_viewer.opening import open_a_path as loading  # noqa: E402
from zmart_viewer.opening.ome_tiff import TiffPicture, declare_a_tiff_picture  # noqa: E402
from zmart_viewer.serving import picture_pieces as served  # noqa: E402
from zmart_viewer.serving.server import make_server  # noqa: E402

TILE = 128


def write_a_pyramidal_ome_tiff(path: Path, data: np.ndarray, axes: str = "CZYX", **options):
    """Write ``data`` as a tiled OME-TIFF holding a full-size copy and one half-size copy."""
    tiling = {"tile": (TILE, TILE), "compression": "zlib", "photometric": "minisblack"}
    tiling.update(options)
    with tifffile.TiffWriter(path, bigtiff=True) as writer:
        writer.write(
            data,
            subifds=1,
            metadata={
                "axes": axes,
                "PhysicalSizeX": 0.5,
                "PhysicalSizeY": 0.5,
                "PhysicalSizeZ": 2.0,
                "Channel": {"Name": ["DAPI", "GFP"]},
            },
            **tiling,
        )
        writer.write(data[..., ::2, ::2], subfiletype=1, **tiling)


@pytest.fixture
def an_ome_tiff(tmp_path):
    """A two-channel, three-plane OME-TIFF whose size is not a multiple of its tiles."""
    rng = np.random.default_rng(7)
    data = rng.integers(1, 4000, (2, 3, 300, 410), dtype=np.uint16)
    path = tmp_path / "data" / "cells.ome.tif"
    path.parent.mkdir()
    write_a_pyramidal_ome_tiff(path, data)
    return path, data


def _decoded(body: bytes, dtype=np.uint16) -> np.ndarray:
    raw = numcodecs.Zstd().decode(body)
    return np.frombuffer(raw, dtype=np.dtype(dtype).newbyteorder("<")).reshape(TILE, TILE)


def test_the_description_says_what_the_file_holds(an_ome_tiff, tmp_path):
    """Size, pyramid, pixel size and channel names all come from the TIFF."""
    tiff, data = an_ome_tiff
    store = declare_a_tiff_picture(tmp_path / "session", tiff)

    assert store.name == "cells.zmartview.zarr"
    assert not (tiff.parent / "cells.zmartview.zarr").exists(), "nothing is written beside the data"

    group = json.loads((store / "zarr.json").read_text())
    multiscale = group["attributes"]["ome"]["multiscales"][0]
    assert [axis["name"] for axis in multiscale["axes"]] == ["t", "c", "z", "y", "x"]
    scales = [one["coordinateTransformations"][0]["scale"] for one in multiscale["datasets"]]
    assert scales == [[1.0, 1.0, 2.0, 0.5, 0.5], [1.0, 1.0, 2.0, 1.0, 1.0]]
    labels = [one["label"] for one in group["attributes"]["ome"]["omero"]["channels"]]
    assert labels == ["DAPI", "GFP"]

    level_zero = json.loads((store / "0" / "zarr.json").read_text())
    level_one = json.loads((store / "1" / "zarr.json").read_text())
    assert level_zero["shape"] == [1, 2, 3, 300, 410]
    assert level_one["shape"] == [1, 2, 3, 150, 205]
    assert level_zero["chunk_grid"]["configuration"]["chunk_shape"] == [1, 1, 1, TILE, TILE]


def test_every_piece_is_the_files_own_pixels(an_ome_tiff):
    """Each piece, edges included, matches the TIFF exactly, at both levels."""
    tiff, data = an_ome_tiff
    picture = TiffPicture(tiff)

    try:
        for level, held in enumerate((data, data[..., ::2, ::2])):
            rows, columns = picture.grid(level)
            for channel in range(2):
                for plane in range(3):
                    for row in range(rows):
                        for column in range(columns):
                            piece = _decoded(
                                picture.bytes_for(level, 0, channel, plane, row, column)
                            )
                            expected = held[
                                channel,
                                plane,
                                row * TILE : (row + 1) * TILE,
                                column * TILE : (column + 1) * TILE,
                            ]
                            down, across = expected.shape
                            assert np.array_equal(piece[:down, :across], expected)
                            assert not piece[down:, :].any() and not piece[:, across:].any()

        assert picture.bytes_for(0, 0, 2, 0, 0, 0) is None, "there is no third channel"
        assert picture.bytes_for(2, 0, 0, 0, 0, 0) is None, "there is no third level"
    finally:
        picture.close()


def test_an_rgb_tiff_is_shown_as_three_channels(tmp_path):
    """A colour image (brightfield, histology) keeps its red, green and blue apart."""
    rng = np.random.default_rng(3)
    data = rng.integers(0, 255, (200, 260, 3), dtype=np.uint8)
    path = tmp_path / "slide.ome.tif"
    tifffile.imwrite(path, data, photometric="rgb", tile=(TILE, TILE), metadata={"axes": "YXS"})

    picture = TiffPicture(path)
    try:
        assert picture.shapes[0] == (1, 3, 1, 200, 260)
        for channel in range(3):
            piece = picture.values_for(0, 0, channel, 0, 1, 2)
            assert np.array_equal(
                piece[: 200 - TILE, : 260 - 2 * TILE], data[TILE:, 2 * TILE :, channel]
            )
    finally:
        picture.close()


def test_a_tiff_of_an_undrawable_kind_is_refused_plainly(tmp_path):
    """64-bit pixels cannot be drawn, and the refusal says so at the door."""
    path = tmp_path / "wide.ome.tif"
    tifffile.imwrite(path, np.zeros((32, 32), dtype=np.float64), metadata={"axes": "YX"})

    with pytest.raises(loading.CannotOpen, match="cannot draw"):
        loading.load(path, scenes=tmp_path / "session")


def test_the_server_serves_an_opened_ome_tiff(an_ome_tiff, tmp_path, monkeypatch):
    """Opened through the viewer's own door, the TIFF's pixels reach the browser."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # the session folder goes here
    tiff, data = an_ome_tiff
    server = make_server(port=0, live=False)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/stores/open",
            data=json.dumps({"path": str(tiff)}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as answer:
            config = json.load(answer)

        # The viewer names each picture it shows by the address its pieces are served from.
        sources = [
            source
            for layer in config["layers"]
            for source in layer.get("sources", [])
            if "cells.zmartview.zarr" in source
        ]
        assert sources, f"the opened TIFF is not among the viewer's pictures: {config}"
        base = sources[0].split("|", 1)[0].rstrip("/")

        with urllib.request.urlopen(f"http://127.0.0.1:{port}{base}/zarr.json", timeout=30) as one:
            assert json.load(one)["attributes"]["ome"]["multiscales"]

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{base}/0/c/0/1/2/2/3", timeout=30
        ) as one:
            piece = _decoded(one.read())

        expected = data[1, 2, 2 * TILE :, 3 * TILE :]
        assert np.array_equal(piece[: expected.shape[0], : expected.shape[1]], expected)

        with pytest.raises(urllib.error.HTTPError) as outside:
            urllib.request.urlopen(f"http://127.0.0.1:{port}{base}/0/c/0/0/0/9/9", timeout=30)
        assert outside.value.code == 404, "ground outside the picture is absent, not an error"
    finally:
        server.shutdown()
        for store in (tmp_path / "home").rglob("cells.zmartview.zarr"):
            served.forget(store)  # lets go of the TIFF file, so it can be cleaned up


def test_an_opened_ome_tiff_is_drawn_in_the_browser(
    an_ome_tiff, tmp_path, monkeypatch, browser, built_dist
):
    """The real page, drawing the TIFF: specimen must be on the screen, not black."""
    import io

    from PIL import Image

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    tiff, _ = an_ome_tiff
    server = make_server(port=0, site_dir=built_dist, live=False)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    page = browser.new_page(viewport={"width": 800, "height": 700})

    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/stores/open",
            data=json.dumps({"path": str(tiff)}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(request, timeout=30).close()

        page.goto(f"http://127.0.0.1:{port}", wait_until="domcontentloaded")
        page.wait_for_function("() => window.zmartViewer !== undefined", timeout=30_000)
        page.wait_for_function(
            """() => {
              const v = window.zmartViewer;
              let needed = 0, available = 0;
              for (const managed of v.layerManager.managedLayers)
                for (const rl of (managed.layer && managed.layer.renderLayers) || []) {
                  const p = rl.layerChunkProgressInfo;
                  if (p) { needed += p.numVisibleChunksNeeded;
                           available += p.numVisibleChunksAvailable; }
                }
              return needed > 0 && available >= needed;
            }""",
            timeout=60_000,
        )
        page.wait_for_timeout(1_000)
        grey = np.asarray(Image.open(io.BytesIO(page.screenshot())).convert("L"))
        assert float((grey > 40).mean()) > 0.05, "the TIFF was opened but nothing was drawn"
    finally:
        page.close()
        server.shutdown()
        for store in (tmp_path / "home").rglob("cells.zmartview.zarr"):
            served.forget(store)


def test_a_tiff_stored_in_strips_is_cut_into_pieces(tmp_path):
    """A TIFF without tiles (as many writers make them) is read through the general reader."""
    rng = np.random.default_rng(11)
    data = rng.integers(0, 4000, (3, 700, 900), dtype=np.uint16)
    path = tmp_path / "strips.ome.tif"
    tifffile.imwrite(path, data, metadata={"axes": "ZYX"}, rowsperstrip=37, compression="zlib")

    picture = TiffPicture(path)
    try:
        assert picture.pieces == [(512, 512)]
        for plane in range(3):
            for row in range(2):
                for column in range(2):
                    piece = picture.values_for(0, 0, 0, plane, row, column)
                    expected = data[
                        plane, row * 512 : (row + 1) * 512, column * 512 : (column + 1) * 512
                    ]
                    assert np.array_equal(piece[: expected.shape[0], : expected.shape[1]], expected)
    finally:
        picture.close()


def test_many_pieces_read_at_once_are_all_correct(an_ome_tiff):
    """The server reads several pieces at the same time; none may be mixed up."""
    from concurrent.futures import ThreadPoolExecutor

    tiff, data = an_ome_tiff
    picture = TiffPicture(tiff)
    rows, columns = picture.grid(0)
    asked = [
        (channel, plane, row, column)
        for channel in range(2)
        for plane in range(3)
        for row in range(rows)
        for column in range(columns)
    ] * 4

    def correct(at):
        channel, plane, row, column = at
        piece = picture.values_for(0, 0, channel, plane, row, column)
        expected = data[
            channel, plane, row * TILE : (row + 1) * TILE, column * TILE : (column + 1) * TILE
        ]
        return np.array_equal(piece[: expected.shape[0], : expected.shape[1]], expected)

    try:
        with ThreadPoolExecutor(16) as readers:
            assert all(readers.map(correct, asked))
    finally:
        picture.close()


def test_an_lzw_compressed_tiff_is_read(tmp_path):
    """LZW is common in pyramidal TIFFs; with imagecodecs installed it reads exactly."""
    pytest.importorskip("imagecodecs")
    rng = np.random.default_rng(5)
    data = rng.integers(0, 4000, (2, 3, 300, 410), dtype=np.uint16)
    path = tmp_path / "lzw.ome.tif"
    write_a_pyramidal_ome_tiff(path, data, compression="lzw")

    picture = TiffPicture(path)
    try:
        piece = picture.values_for(1, 0, 1, 2, 1, 1)
        expected = data[1, 2, ::2, ::2][TILE:, TILE:]
        assert np.array_equal(piece[: expected.shape[0], : expected.shape[1]], expected)
    finally:
        picture.close()


def test_a_damaged_file_is_refused_in_plain_words(tmp_path):
    """A file named like an OME-TIFF that is not one gets a reason a person can act on."""
    path = tmp_path / "damaged.ome.tif"
    path.write_bytes(b"this was never a TIFF file")

    with pytest.raises(loading.CannotOpen, match="may be damaged, incompletely copied"):
        loading.load(path, scenes=tmp_path / "session")


def write_a_folder_of_positions(folder: Path) -> np.ndarray:
    """Cut a known picture into six OME-TIFF positions, each saying where the stage stood."""
    rng = np.random.default_rng(4)
    whole = rng.integers(1, 4000, (2, 600, 900), dtype=np.uint16)  # channel, Y, X
    side = 300
    folder.mkdir()

    for row in range(2):
        for column in range(3):
            tile = whole[:, row * side : (row + 1) * side, column * side : (column + 1) * side]
            metadata = {
                "axes": "CYX",
                "PhysicalSizeX": 0.5,
                "PhysicalSizeY": 0.5,
                "Plane": {
                    "PositionX": [column * side * 0.5] * 2,
                    "PositionY": [row * side * 0.5] * 2,
                },
            }
            with tifffile.TiffWriter(folder / f"position_{row}_{column}.ome.tif") as writer:
                writer.write(
                    tile, subifds=1, tile=(TILE, TILE), metadata=metadata, photometric="minisblack"
                )
                writer.write(
                    tile[:, ::2, ::2], subfiletype=1, tile=(TILE, TILE), photometric="minisblack"
                )

    return whole


@pytest.mark.parametrize("kept_open", [256, 2])
def test_a_folder_of_ome_tiffs_is_put_together_by_stage_position(tmp_path, monkeypatch, kept_open):
    """Six positions become one picture, every pixel where it belongs.

    Run twice: once with plenty of files kept open, and once with only two, so
    files are closed and opened again all the time, as with thousands of positions.
    """
    import zmart_viewer.opening.ome_tiff as ome_tiff

    monkeypatch.setenv("ZMART_TIFFS_KEPT_OPEN", str(kept_open))
    whole = write_a_folder_of_positions(tmp_path / "run")
    scene = loading.load(tmp_path / "run", scenes=tmp_path / "session").target

    try:
        described = json.loads((scene / "zarr.json").read_text())
        assert described["attributes"]["zmart"]["baked"], "the overview is baked into files"
        level_zero = json.loads((scene / "0" / "zarr.json").read_text())
        assert level_zero["shape"][-2:] == [600, 900]
        piece = level_zero["chunk_grid"]["configuration"]["chunk_shape"][-1]
        dtype = np.dtype(level_zero["data_type"]).newbyteorder("<")

        for channel in range(2):
            for row in range(-(-600 // piece)):
                for column in range(-(-900 // piece)):
                    body = served.built_bytes_behind(scene, f"0/c/0/{channel}/0/{row}/{column}")
                    got = np.frombuffer(numcodecs.Zstd().decode(body), dtype=dtype)
                    got = got.reshape(piece, piece)
                    expected = whole[
                        channel,
                        row * piece : (row + 1) * piece,
                        column * piece : (column + 1) * piece,
                    ]
                    assert np.array_equal(got[: expected.shape[0], : expected.shape[1]], expected)

        assert len(ome_tiff._kept_open) <= kept_open
        assert not list((tmp_path / "run").glob("*.zarr")), "nothing is written beside the data"
    finally:
        served.forget(scene)


def test_a_folder_of_tiffs_without_stage_positions_is_refused(tmp_path):
    """Files that do not say where they were taken would all land on one spot; refuse instead."""
    folder = tmp_path / "planes"
    folder.mkdir()
    for plane in range(3):
        tifffile.imwrite(
            folder / f"capture_T000000_C00_Z{plane:05d}.ome.tiff",
            np.full((64, 64), plane + 1, np.uint16),
            metadata={"axes": "YX", "PhysicalSizeX": 0.5, "PhysicalSizeY": 0.5},
        )

    with pytest.raises(loading.CannotOpen, match="do not say where the stage stood"):
        loading.load(folder, scenes=tmp_path / "session")
