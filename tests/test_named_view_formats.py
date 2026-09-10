"""Named-view format gate: independent pixels, coverage and publication lifecycle."""

import gzip
import hashlib
import json

import numpy as np
import pytest
import zarr
from numcodecs import Zstd
from test_view_sampling import INPUT_FORMATS, write_tile

from zmart_viewer import coverage, pieces, published
from zmart_viewer.library import Library
from zmart_viewer.views import ViewSet


def regions(mask):
    # Deliberately independent of the production coverage helpers.
    assert (mask == mask[..., :1, :]).all()
    return [
        {
            "frame": t,
            "channel": c,
            "origin": dict(zip("zyx", (z, 0, x))),
            "shape": dict(zip("zyx", (1, mask.shape[-2], 1))),
        }
        for t, c, z, x in np.argwhere(mask[..., 0, :]).tolist()
    ]


def hashes(folder):
    return {
        p.relative_to(folder): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in folder.rglob("*")
        if p.is_file()
    }


def expected_plane(inputs, mode, z, t, c, level):
    dtype = "uint32" if mode == "sum" else "uint16"
    image = np.zeros((8, 32), dtype=dtype)
    mask = np.zeros(image.shape, dtype=bool)
    for data, acquired, x in inputs.values():
        values, valid = data[t, c], acquired[t, c]
        if mode in ("slice", "top"):
            plane = min(z, len(values) - 1) if mode == "top" else z
            if plane >= len(values):
                continue
            values, valid = values[plane], valid[plane]
        else:
            present = valid.any(axis=0)
            fill = np.iinfo(data.dtype).max if mode == "min" else 0
            values = getattr(np, mode)(np.where(valid, values, fill), axis=0).astype(dtype)
            values[~present] = 0
            valid = present
        target = image[:, x : x + 8]
        target[valid] = values[valid]
        mask[:, x : x + 8] |= valid
    for _ in range(level):
        h, w = image.shape
        image = np.rint(image.reshape(h // 2, 2, w // 2, 2).mean((1, 3))).astype(dtype)
        mask = mask.reshape(h // 2, 2, w // 2, 2).any((1, 3))
    return image, mask


def verify_products(view, inputs):
    checked = 0
    for key, output in zip(view.keys, view.outputs.values()):
        pieces.forget(output._shown)
        output.close()  # No comparison against the compositor that populated the bake.
        made = output.composer()
        assert made.mosaic.frame_room == (2, 2)
        assert made.mosaic.corner_um[0] == 0
        for level in range(3):
            shape = [2, 2, 4 if key in ("slice", "top") else 1, 8 >> level, 32 >> level]
            assert (
                json.loads((output._shown / str(level) / "zarr.json").read_text())["shape"] == shape
            )
            assert made.grid(level) == (shape[2], (shape[3] + 3) // 4, (shape[4] + 3) // 4)
            for t in range(2):
                for c in range(2):
                    for z in range(made.grid(level)[0]):
                        expected, acquired = expected_plane(inputs, key, z, t, c, level)
                        for y in range(made.grid(level)[1]):
                            for x in range(made.grid(level)[2]):
                                route = f"{level}/c/{t}/{c}/{z}/{y}/{x}"
                                raw = pieces.built_bytes_behind(output._shown, route)
                                actual = (
                                    np.zeros((4, 4), dtype=expected.dtype)
                                    if raw is None
                                    else np.frombuffer(
                                        Zstd().decode(raw), dtype=expected.dtype
                                    ).reshape(4, 4)
                                )
                                signal = np.zeros((4, 4), dtype=expected.dtype)
                                mask = np.zeros((4, 4), dtype=bool)
                                area = expected[y * 4 : (y + 1) * 4, x * 4 : (x + 1) * 4]
                                signal[: area.shape[0], : area.shape[1]] = area
                                mask[: area.shape[0], : area.shape[1]] = acquired[
                                    y * 4 : (y + 1) * 4, x * 4 : (x + 1) * 4
                                ]
                                np.testing.assert_array_equal(
                                    actual, signal, err_msg=f"{key} {route}"
                                )
                                if output.bake and level == 2 and signal.any():
                                    physical_z = (
                                        made.canonical_plane(level, z, y, x, t, c)
                                        if key == "top"
                                        else z
                                    )
                                    stored = (
                                        output._shown / f"{level}/c/{t}/{c}/{physical_z}/{y}/{x}"
                                    )
                                    assert stored.is_file(), (
                                        f"Bake fell back to composition: {stored}"
                                    )
                                    np.testing.assert_array_equal(
                                        np.frombuffer(
                                            Zstd().decode(stored.read_bytes()), dtype=expected.dtype
                                        ).reshape(4, 4),
                                        signal,
                                    )
                                actual_mask = np.frombuffer(
                                    gzip.decompress(coverage.answer(output._shown, route)),
                                    dtype="uint8",
                                ).reshape(4, 4)
                                np.testing.assert_array_equal(
                                    actual_mask, mask, err_msg=f"coverage {key} {route}"
                                )
                                checked += 1
        # Entirely empty coarse chunks need no stored file, even when baking.
        if len(inputs) == 2:
            assert not (output._shown / "2/c/0/0/0/0/1").exists()
    return checked


@pytest.mark.parametrize("input_format", INPUT_FORMATS)
@pytest.mark.parametrize("bake", [False, True])
def test_named_formats_pixels_coverage_append_rewrite_reopen(
    tmp_path, monkeypatch, input_format, bake
):
    source = tmp_path / "positions"
    source.mkdir()
    inputs = {}
    for name, depth, x in (("flat", 1, 0), ("stack", 4, 8)):
        data = np.arange(2 * 2 * depth * 8 * 8, dtype="uint16").reshape(2, 2, depth, 8, 8) + 10
        data[..., :2] = 0  # Acquired black, including omitted fill chunks/shards.
        if name == "flat":
            data.fill(0)
        mask = np.ones(data.shape, dtype=bool)
        mask[..., 2:4] = False
        if depth > 1:
            mask[1, 1, 1] = False
        inputs[name + ".ome.zarr"] = (data, mask, x)
    options = dict(
        acquisition="a",
        projections=("min", "max", "sum"),
        projection_folder=tmp_path / "projections",
        piece=4,
    )
    view = ViewSet(tmp_path / "view", **options)
    versions = dict.fromkeys(inputs, 1)
    checked = 0
    try:
        for stage in ("initial", "append", "rewrite"):
            if stage == "append":
                inputs["later.ome.zarr"] = (
                    np.full((2, 2, 2, 8, 8), 21, dtype="uint16"),
                    np.ones((2, 2, 2, 8, 8), bool),
                    16,
                )
                versions["later.ome.zarr"] = 1
            elif stage == "rewrite":
                data, mask, _ = inputs["stack.ome.zarr"]
                data[..., 2:4] = 37
                mask[..., 2:4] = True
                versions["stack.ome.zarr"] = 2
            written = (
                inputs
                if stage == "initial"
                else {
                    name: inputs[name]
                    for name in (["later.ome.zarr"] if stage == "append" else ["stack.ome.zarr"])
                }
            )
            for name, (data, _, x) in written.items():
                write_tile(source, name, data, x=x, z=100, input_format=input_format)
            original = hashes(source)
            first = source / "flat.ome.zarr"
            assert all(
                p.name in (".zarray", ".zattrs", "zarr.json") for p in (first / "0").iterdir()
            ), "An acquired all-zero image must also work with no stored chunks or shards"
            if input_format == "v2":
                assert (
                    json.loads((first / ".zattrs").read_text())["multiscales"][0]["version"]
                    == "0.4"
                )
                assert json.loads((first / "0/.zarray").read_text())["zarr_format"] == 2
            else:
                assert (
                    json.loads((first / "zarr.json").read_text())["attributes"]["ome"]["version"]
                    == "0.5"
                )
                codecs = json.loads((first / "0/zarr.json").read_text())["codecs"]
                assert any(c["name"] == "sharding_indexed" for c in codecs) == (
                    input_format == "v3-sharded"
                )
            composition = {
                "regions": {name: regions(mask) for name, (_, mask, _) in inputs.items()},
                "order": list(inputs),
                "z_references": dict.fromkeys(inputs, 100),
            }
            view.publish(
                source,
                versions,
                {"x_um": [0, 32], "y_um": [0, 8]},
                composition=composition,
                bake=bake,
            )
            assert len(view.sources) == 5
            assert hashes(source) == original
            if stage == "rewrite":
                view.close()
                view = ViewSet(tmp_path / "view", **options)
            checked += verify_products(view, inputs)
            chunks = [
                p
                for out in view.outputs.values()
                for p in out._shown.glob("2/c/*/*/*/*/*")
                if p.is_file()
            ]
            assert bool(chunks) == bake

            def no_write(*args, **kwargs):
                pytest.fail("Idle announcement wrote arrays or publication state")

            with monkeypatch.context() as patch:
                patch.setattr(zarr.Array, "__setitem__", no_write)
                patch.setattr(published, "_atomic_json", no_write)
                revision = view.revision
                assert (
                    view.publish(
                        source,
                        versions,
                        {"x_um": [0, 32], "y_um": [0, 8]},
                        composition=composition,
                        bake=bake,
                    )
                    == revision
                )
            assert hashes(source) == original
        cold = Library()
        cold.open(tmp_path / "view")
        assert len(cold.entries()) == 5
        print(
            {
                "format": input_format,
                "bake": bake,
                "image_and_coverage_chunks": checked,
                "sources_before_after_append": [5, 5],
                "originals_unchanged": True,
            }
        )
    finally:
        view.close()
        for output in view.outputs.values():
            pieces.forget(output._shown)
