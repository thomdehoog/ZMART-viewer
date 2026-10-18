"""Where positions of one channel overlap, the later one covers, or they average.

Cover is the default: the position last in the composition's ``order`` lies
on top. ``"overlap": "average"`` shows the mean of every position that imaged
a voxel instead, one smooth picture with no seam. Either way a voxel nobody
imaged stays unimaged, and a view keeps the mode it was published with.
"""

import numpy as np
import pytest
import zarr
from test_acquired_composition import pixels, region, source
from test_acquired_native_pyramids import write_pyramid
from test_published_transfer import write_position
from zmart_viewer.picture.arrangement import (
    MEAN_REDUCTION,
    Composer,
    Mosaic,
    read_the_mosaic_as_written,
    the_mosaic_written_down,
)
from zmart_viewer.views.publishing import STORE, PublishedTransfer


def composed(tmp_path, values, *, overlap, x=4, order=("a.ome.zarr", "b.ome.zarr")):
    """Two 8x8 positions, the second ``x`` voxels along, laid out on 2x8x16."""
    a = source(tmp_path, "a.ome.zarr", 0)
    b = source(tmp_path, "b.ome.zarr", 0, x=x)
    for tile, fine in zip((a, b), values, strict=True):
        write_pyramid(tile, fine)
    mosaic = Mosaic(
        [a, b], 3, ("z", "y", "x"), "uint16", averaged=True, extent_um=(2, 8, 16)
    ).with_acquired_regions(
        {a.name: [region(0, 8)], b.name: [region(0, 8)]},
        order=list(order),
        pyramid_reduction=MEAN_REDUCTION,
        overlap=overlap,
    )
    return Composer(mosaic, piece=4)


def halved(picture):
    h, w = picture.shape
    even = np.pad(picture, ((0, h % 2), (0, w % 2)), mode="edge")
    return np.rint(even.reshape(-1, 2, even.shape[1] // 2, 2).mean((1, 3))).astype(picture.dtype)


def flat(value):
    return np.full((2, 2, 2, 8, 8), value, dtype=np.uint16)


@pytest.mark.parametrize("order", [("a.ome.zarr", "b.ome.zarr"), ("b.ome.zarr", "a.ome.zarr")])
def test_cover_shows_the_later_position_in_the_overlap(tmp_path, order):
    composer = composed(tmp_path, (flat(100), flat(200)), overlap="cover", order=order)
    try:
        image, mask = pixels(composer, 0)
        later = 200 if order[-1] == "b.ome.zarr" else 100
        assert (image[:, :4] == 100).all()
        assert (image[:, 4:8] == later).all()
        assert (image[:, 8:12] == 200).all()
        assert not image[:, 12:].any()
        np.testing.assert_array_equal(mask[0], [1] * 12 + [0] * 4)
    finally:
        composer.close()


@pytest.mark.parametrize("x", [4, 5], ids=["aligned", "misaligned"])
def test_average_shows_the_mean_in_the_overlap_and_each_position_outside_it(tmp_path, x):
    composer = composed(tmp_path, (flat(100), flat(200)), overlap="average", x=x)
    try:
        image, mask = pixels(composer, 0)
        assert (image[:, :x] == 100).all()
        assert (image[:, x:8] == 150).all()
        assert (image[:, 8 : 8 + x] == 200).all()
        assert not image[:, 8 + x :].any()
        np.testing.assert_array_equal(mask[0], [1] * (8 + x) + [0] * (8 - x))
    finally:
        composer.close()


@pytest.mark.parametrize("x", [4, 5], ids=["aligned", "misaligned"])
def test_every_coarse_level_agrees_with_averaging_the_finest(tmp_path, x):
    """Aligned positions read their own coarse copies and average those, a
    misaligned one has its finer level averaged first and then halved; both
    are the 2x2 mean of the averaged finest picture, to the rounding of one
    level."""
    rng = np.random.default_rng(7)
    values = [rng.integers(0, 4096, (2, 2, 2, 8, 8), dtype=np.uint16) for _ in range(2)]
    composer = composed(tmp_path, values, overlap="average", x=x)
    levels_read = set()
    read = composer._read_from

    def counted(copy, *args):
        levels_read.add(int(copy.held_in.name))
        return read(copy, *args)

    composer._read_from = counted
    try:
        composer.values_for(2, 0, 0, 0)
        assert levels_read == ({2} if x == 4 else {0})
        finest = np.zeros((8, 16), dtype=np.float64)
        count = np.zeros((8, 16))
        for at, fine in ((0, values[0]), (x, values[1])):
            finest[:, at : at + 8] += fine[0, 0, 0]
            count[:, at : at + 8] += 1
        expected = np.rint(
            np.divide(finest, count, out=np.zeros_like(finest), where=count > 0)
        ).astype(np.uint16)
        image, _ = pixels(composer, 0)
        np.testing.assert_array_equal(image, expected)
        for level in (1, 2):
            expected = halved(expected)
            image, _ = pixels(composer, level)
            np.testing.assert_allclose(image, expected, atol=1, rtol=0)
    finally:
        composer.close()


def test_the_mode_survives_being_written_down(tmp_path):
    composer = composed(tmp_path, (flat(100), flat(200)), overlap="average")
    try:
        written = the_mosaic_written_down(composer.mosaic)
        assert written["overlap"] == "average"
        assert read_the_mosaic_as_written(written).overlap == "average"
    finally:
        composer.close()
    assert "overlap" not in the_mosaic_written_down(
        Mosaic(composer.mosaic.tiles, 3, ("z", "y", "x"), "uint16")
    )


def test_an_unknown_mode_is_refused(tmp_path):
    with pytest.raises(ValueError, match="cover or average"):
        composed(tmp_path, (flat(100), flat(200)), overlap="blend")


CANVAS = {"x_um": [0, 192], "y_um": [0, 128]}


def published(tmp_path, **extra):
    """Two 128-voxel positions overlapping by 64, published as one view."""
    write_position(tmp_path, "a.ome.zarr", 0, 100)
    write_position(tmp_path, "b.ome.zarr", 64, 200)
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    composition = {"regions": "complete", "order": ["a.ome.zarr", "b.ome.zarr"], **extra}
    view.publish(tmp_path, {"a.ome.zarr": 1, "b.ome.zarr": 1}, CANVAS, composition=composition)
    return view, composition


def row_of(view, level):
    composer = view.composer()
    width = composer.mosaic.shape(level)[2]
    return np.concatenate(
        [composer.values_for(level, 0, 0, col)[0] for col in range(-(-width // 64))]
    )[:width]


def test_a_published_view_covers_by_default(tmp_path):
    view, _ = published(tmp_path)
    try:
        line = row_of(view, 0)
        assert (line[:64] == 100).all() and (line[64:] == 200).all()
        assert view.composer().mosaic.overlap == "cover"
    finally:
        view.close()


def test_a_published_view_averages_at_every_level_when_asked(tmp_path):
    view, _ = published(tmp_path, overlap="average")
    try:
        baked = zarr.open_group(str(view._shown), mode="r").attrs["zmart"]["baked"]
        assert baked
        for level in range(view.composer().mosaic.levels):
            factor = 2**level
            expected = np.repeat([100, 150, 200], 64 // factor)
            np.testing.assert_array_equal(row_of(view, level), expected)
            if level in baked:
                written = zarr.open_array(str(view._shown / str(level)), mode="r")[0, 0]
                np.testing.assert_array_equal(written, expected)
    finally:
        view.close()
    reopened = PublishedTransfer(tmp_path / STORE, piece=64)
    try:
        assert (row_of(reopened, 0)[64:128] == 150).all()
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "first,then",
    [
        ({}, {"overlap": "average"}),
        ({"overlap": "average"}, {}),
        ({"overlap": "average"}, {"overlap": "cover"}),
    ],
)
def test_a_view_keeps_the_overlap_it_was_published_with(tmp_path, first, then):
    view, composition = published(tmp_path, **first)
    try:
        composition = {k: v for k, v in composition.items() if k != "overlap"} | then
        with pytest.raises(
            ValueError, match="overlapping positions are drawn cannot change in place"
        ):
            view.publish(
                tmp_path, {"a.ome.zarr": 1, "b.ome.zarr": 1}, CANVAS, composition=composition
            )
    finally:
        view.close()


def test_a_named_cover_is_the_default_and_republishes_unchanged(tmp_path):
    view, composition = published(tmp_path)
    try:
        before = view.revision
        view.publish(
            tmp_path,
            {"a.ome.zarr": 1, "b.ome.zarr": 1},
            CANVAS,
            composition={**composition, "overlap": "cover"},
        )
        assert view.revision == before
    finally:
        view.close()


def test_an_unknown_overlap_in_a_request_is_refused(tmp_path):
    with pytest.raises(ValueError, match="either cover one another"):
        published(tmp_path, overlap="blend")
