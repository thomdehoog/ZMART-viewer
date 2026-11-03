"""A run saved as OME-TIFF is shown exactly as the same run saved as OME-Zarr.

ZMART can save each position of a run as an OME-Zarr image or as one OME-TIFF
file. The viewer composes both into one live picture the same way: these tests
save one run both ways, publish each folder as the ZMART interface does, and
check that the two pictures agree pixel for pixel at every zoom level. They
also check that a position captured again during the run shows its new pixels.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest
import tifffile
import zarr
from zmart_viewer.opening.open_folders import Library
from zmart_viewer.serving import picture_pieces as pieces
from zmart_viewer.views.publishing import STACK_STORE, PublishedFolders

PIXEL_UM = 0.5
Z_STEP_UM = 2.0
SIDE = 32
LEVELS = 3

#: What ZMART records about how a stack was taken, as the interface writes it.
Z_MODEL = {
    "model": "zmart-microscopy-specimen-z-v1",
    "frame": "specimen",
    "spacing_um": Z_STEP_UM,
    "lowest_plane_um": 10.0,
    "acquisition_provenance": {"requested_stage_focus_z_um": 12.0},
}

#: How the interface asks for a run to be composed.
COMPOSITION = {"regions": "complete", "xy_origin": "corner", "pyramid_reduction": "mean-xy2-crop-f32-rint-int"}


def _smaller_copies(volume: np.ndarray) -> list[np.ndarray]:
    """The full-size picture and its smaller copies, each the mean of 2 x 2 pixels."""
    copies = [volume]
    for _ in range(LEVELS - 1):
        last = copies[-1]
        height, width = last.shape[-2] // 2 * 2, last.shape[-1] // 2 * 2
        blocks = last[..., :height, :width].reshape(*last.shape[:-2], height // 2, 2, width // 2, 2)
        copies.append(np.rint(blocks.mean(axis=(-3, -1), dtype=np.float32)).astype(last.dtype))
    return copies


def write_zarr_position(path, volume, corner_zyx):
    """One position as ZMART writes it in OME-Zarr: every copy starts at the corner."""
    group = zarr.open_group(str(path), mode="w", zarr_format=3)
    datasets = []
    for level, copy in enumerate(_smaller_copies(volume)):
        group.create_array(str(level), data=copy, chunks=(1, 1, 1, 16, 16))
        factor = 2**level
        datasets.append(
            {
                "path": str(level),
                "coordinateTransformations": [
                    {"type": "scale", "scale": [1, 1, Z_STEP_UM, PIXEL_UM * factor, PIXEL_UM * factor]},
                    {"type": "translation", "translation": [0, 0, *corner_zyx]},
                ],
            }
        )
    axes = [
        {"name": a, "type": "time" if a == "t" else "channel" if a == "c" else "space"}
        | ({"unit": "micrometer"} if a in "zyx" else {})
        for a in "tczyx"
    ]
    group.attrs["ome"] = {
        "version": "0.5",
        "multiscales": [{"type": "mean", "axes": axes, "datasets": datasets}],
    }
    group.attrs["zmart_microscopy"] = {"z_coordinate": Z_MODEL}


def write_tiff_position(path, volume, corner_zyx):
    """The same position as one OME-TIFF file, with ZMART's note inside it.

    Written to a temporary name and moved into place, as a run does, so the
    viewer never sees a half-written file.
    """
    frames, channels, depth, height, width = volume.shape
    planes = "".join(
        f'<Plane TheT="{t}" TheC="{c}" TheZ="{z}" PositionX="{corner_zyx[2]}" PositionXUnit="µm" '
        f'PositionY="{corner_zyx[1]}" PositionYUnit="µm" '
        f'PositionZ="{corner_zyx[0] + z * Z_STEP_UM}" PositionZUnit="µm"/>'
        for t in range(frames)
        for c in range(channels)
        for z in range(depth)
    )
    note = (
        f'<M K="zmart_microscopy">{json.dumps({"z_coordinate": Z_MODEL})}</M>'
        '<M K="pyramid">mean</M><M K="position_origin">corner</M>'
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06">'
        '<Image ID="Image:0"><Pixels ID="Pixels:0" DimensionOrder="XYZCT" Type="uint16" '
        f'SizeX="{width}" SizeY="{height}" SizeZ="{depth}" SizeC="{channels}" SizeT="{frames}" '
        f'PhysicalSizeX="{PIXEL_UM}" PhysicalSizeXUnit="µm" PhysicalSizeY="{PIXEL_UM}" '
        f'PhysicalSizeYUnit="µm" PhysicalSizeZ="{Z_STEP_UM}" PhysicalSizeZUnit="µm">'
        + "".join(f'<Channel ID="Channel:0:{c}" SamplesPerPixel="1"/>' for c in range(channels))
        + f'<TiffData PlaneCount="{frames * channels * depth}"/>'
        + planes
        + '</Pixels><AnnotationRef ID="Annotation:0"/></Image>'
        '<StructuredAnnotations><MapAnnotation ID="Annotation:0" Namespace="zmart/position">'
        f"<Value>{note.replace('&', '&amp;')}</Value></MapAnnotation></StructuredAnnotations></OME>"
    )
    copies = [copy.reshape(-1, *copy.shape[-2:]) for copy in _smaller_copies(volume)]
    staging = path.with_name(f".{path.name}.writing")
    options = {"tile": (16, 16), "photometric": "minisblack", "compression": "zlib", "metadata": None}
    with tifffile.TiffWriter(staging) as tif:
        for index, plane in enumerate(copies[0]):
            tif.write(
                plane,
                subifds=LEVELS - 1,
                description=xml.encode("utf-8") if index == 0 else None,
                **options,
            )
            for copy in copies[1:]:
                tif.write(copy[index], subfiletype=1, **options)
    os.replace(staging, path)


def a_run(rng, count=3):
    """Three positions side by side, two channels and three planes each."""
    return {
        f"overview_P{index:03d}": (
            rng.integers(0, 4000, (1, 2, 3, SIDE, SIDE), dtype=np.uint16),
            (10.0, 0.0, index * SIDE * PIXEL_UM),
        )
        for index in range(count)
    }


def a_plane_of(made, level, *, c, z):
    """One whole plane of one zoom level of the published picture, piece by piece."""
    _, height, width = made.mosaic.shape(level)
    plane = np.zeros((height, width), dtype="uint16")
    for row in range(-(-height // made.piece)):
        for column in range(-(-width // made.piece)):
            values = made.values_for(level, z, row, column, moment=0, channel=c)
            if values is not None:
                top, left = row * made.piece, column * made.piece
                held = plane[top : top + made.piece, left : left + made.piece]
                held[:] = values[: held.shape[0], : held.shape[1]]
    return plane


def everything_shown(published, folder, channels=2, depth=3):
    """Every pixel of every zoom level, channel and plane of the published picture."""
    view = published.views[folder.resolve(), None][0].outputs[STACK_STORE]
    made = view.composer()
    return [
        a_plane_of(made, level, c=c, z=z)
        for level in range(made.mosaic.levels)
        for c in range(channels)
        for z in range(depth)
    ]


def publish(folder, names, revisions, bake):
    library = Library()
    library.open(folder)
    published = PublishedFolders(library)
    canvas = {"x_um": [0, len(names) * SIDE * PIXEL_UM], "y_um": [0, SIDE * PIXEL_UM]}
    published.open(
        folder,
        canvas=canvas,
        versions=revisions,
        bake=bake,
        composition={**COMPOSITION, "order": names},
    )
    return published


@pytest.mark.parametrize("bake", [False, True])
def test_a_run_looks_the_same_whether_saved_as_ome_tiff_or_ome_zarr(tmp_path, bake):
    run = a_run(np.random.default_rng(7))
    as_zarr, as_tiff = tmp_path / "zarr", tmp_path / "tiff"
    as_zarr.mkdir()
    as_tiff.mkdir()
    for stem, (volume, corner) in run.items():
        write_zarr_position(as_zarr / f"{stem}.ome.zarr", volume, corner)
        write_tiff_position(as_tiff / f"{stem}.ome.tif", volume, corner)

    zarr_names = [f"{stem}.ome.zarr" for stem in run]
    tiff_names = [f"{stem}.ome.tif" for stem in run]
    from_zarr = publish(as_zarr, zarr_names, dict.fromkeys(zarr_names, 1), bake)
    from_tiff = publish(as_tiff, tiff_names, dict.fromkeys(tiff_names, 1), bake)
    try:
        shown_zarr = everything_shown(from_zarr, as_zarr)
        shown_tiff = everything_shown(from_tiff, as_tiff)
        assert len(shown_tiff) == len(shown_zarr) > 2 * 3
        for zarr_picture, tiff_picture in zip(shown_zarr, shown_tiff, strict=True):
            np.testing.assert_array_equal(tiff_picture, zarr_picture)
        # The full-size picture is the positions themselves, side by side.
        whole = np.concatenate([volume[0, 1, 2] for volume, _ in run.values()], axis=-1)
        np.testing.assert_array_equal(shown_tiff[1 * 3 + 2], whole)
        assert not list(as_tiff.glob("*.zarr")), "nothing is written beside the files"
    finally:
        for folder, published in ((as_zarr, from_zarr), (as_tiff, from_tiff)):
            pieces.forget(folder / STACK_STORE)
            published.close()


@pytest.mark.parametrize("bake", [False, True])
@pytest.mark.parametrize("kept_open", ["256", "1"])
def test_a_position_captured_again_shows_its_new_pixels(tmp_path, monkeypatch, bake, kept_open):
    """The file is replaced while the viewer may still hold it open; the new one is shown."""
    monkeypatch.setenv("ZMART_TIFFS_KEPT_OPEN", kept_open)
    rng = np.random.default_rng(8)
    run = a_run(rng, count=2)
    for stem, (volume, corner) in run.items():
        write_tiff_position(tmp_path / f"{stem}.ome.tif", volume, corner)
    names = [f"{stem}.ome.tif" for stem in run]
    published = publish(tmp_path, names, dict.fromkeys(names, 1), bake)
    try:
        before = everything_shown(published, tmp_path)
        again = rng.integers(0, 4000, (1, 2, 3, SIDE, SIDE), dtype=np.uint16)
        write_tiff_position(tmp_path / names[0], again, run["overview_P000"][1])
        published.announce(
            [
                {
                    "path": str(tmp_path),
                    "source_revisions": {names[0]: 2, names[1]: 1},
                    "composition": {**COMPOSITION, "order": names},
                }
            ]
        )
        after = everything_shown(published, tmp_path)
        np.testing.assert_array_equal(after[0][:, :SIDE], again[0, 0, 0])
        np.testing.assert_array_equal(after[0][:, SIDE:], before[0][:, SIDE:])
    finally:
        pieces.forget(tmp_path / STACK_STORE)
        published.close()
