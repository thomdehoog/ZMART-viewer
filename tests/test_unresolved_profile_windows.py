"""An absent profile window stays absent from disk through the live UI."""

from __future__ import annotations

from zmart_viewer.library import described_channels
from zmart_viewer.record.model import Channel
from zmart_viewer.record.omezarr import (
    the_channels_described,
    the_channels_for_display,
    the_image_description,
)
from zmart_viewer.record.profiles import plan_the_writing


def test_an_unresolved_profile_omits_the_whole_omero_block():
    profile, _ = plan_the_writing(
        "overview", frame=(640, 640), z_planes=1, channels=("488", "561")
    )

    described = the_image_description(
        profile, name="position", channels=profile.channels
    )

    assert "omero" not in described
    assert the_channels_described(profile.channels, profile.dtype) == []


def test_an_unresolved_profile_keeps_names_without_inventing_a_live_window():
    display = described_channels(
        the_channels_for_display(("488", "561"), "uint16"), 2
    )

    assert [channel["name"] for channel in display] == ["488", "561"]
    assert [channel["window"] for channel in display] == [None, None]
    assert [channel["range"] for channel in display] == [
        {"low": 0.0, "high": 65535.0},
        {"low": 0.0, "high": 65535.0},
    ]


def test_a_chosen_profile_window_is_written_completely():
    described = the_channels_described(
        (Channel("488", window=(200, 3200)),), "uint16"
    )

    assert described[0]["window"] == {
        "min": 0,
        "max": 65535,
        "start": 200,
        "end": 3200,
    }
