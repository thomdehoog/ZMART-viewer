"""One acquisition-wide display description, shared by every position.

The sidecar lives beside the position stores as ``zmart-acquisition.json``.
It is deliberately converted to ordinary OME channel metadata at the composed
source boundary: browsers and generic OME readers see one conventional source,
while the ZMART provenance remains recoverable beside ``ome``.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path

DESCRIPTION_NAME = "zmart-acquisition.json"
SCHEMA = "zmart-acquisition-display/1"


def _finite_numbers(value, what: str) -> None:
    """Refuse NaN/infinity anywhere, including preserved provenance fields."""
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ValueError(f"{what} contains a number that is not finite")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _finite_numbers(child, f"{what}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _finite_numbers(child, f"{what}[{index}]")


def _text(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non-empty string")
    return value.strip()


def _number(value, what: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a finite number")
    if not math.isfinite(float(value)):
        raise ValueError(f"{what} must be a finite number")
    return value


def _whole(value, what: str, *, at_least: int = 0) -> int:
    value = _number(value, what)
    if int(value) != value or int(value) < at_least:
        raise ValueError(f"{what} must be a whole number of at least {at_least}")
    return int(value)


def _pair(value, what: str, low_name: str, high_name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{what} must be an object")
    low = _number(value.get(low_name), f"{what}.{low_name}")
    high = _number(value.get(high_name), f"{what}.{high_name}")
    if float(high) <= float(low):
        raise ValueError(f"{what}.{high_name} must be greater than {what}.{low_name}")
    return {low_name: low, high_name: high}


def validate_acquisition_description(
    value: object, *, acquisition_type: str, channel_count: int
) -> dict:
    """Validate and canonicalise version 1 of the acquisition display sidecar."""
    if not isinstance(value, dict):
        raise ValueError("the acquisition display description must be an object")
    _finite_numbers(value, "the acquisition display description")
    if value.get("schema") != SCHEMA:
        raise ValueError(f"the acquisition display schema must be {SCHEMA!r}")

    wanted_type = _text(acquisition_type, "the acquisition folder name")
    found_type = _text(value.get("acquisitionType"), "acquisitionType")
    if found_type != wanted_type:
        raise ValueError(
            f"the acquisition description names {found_type!r}, but its folder is {wanted_type!r}"
        )

    raw_channels = value.get("channels")
    if not isinstance(raw_channels, list):
        raise ValueError("channels must be a list")
    if len(raw_channels) != channel_count:
        raise ValueError(
            f"the acquisition describes {len(raw_channels)} channel(s), but its pixels hold "
            f"{channel_count}"
        )

    channels: list[dict] = []
    keys: set[str] = set()
    indices: set[int] = set()
    for at, raw in enumerate(raw_channels):
        if not isinstance(raw, dict):
            raise ValueError(f"channels[{at}] must be an object")
        key = _text(raw.get("key"), f"channels[{at}].key")
        index = _whole(raw.get("index"), f"channels[{at}].index")
        label = _text(raw.get("label"), f"channels[{at}].label")
        if key in keys:
            raise ValueError(f"channel key {key!r} is repeated")
        if index in indices:
            raise ValueError(f"channel index {index} is repeated")
        keys.add(key)
        indices.add(index)

        channel = {"key": key, "index": index, "label": label}
        color = raw.get("color")
        if color is not None:
            if not isinstance(color, str) or len(color) != 6:
                raise ValueError(f"channels[{at}].color must be six hexadecimal digits")
            try:
                int(color, 16)
            except ValueError as exc:
                raise ValueError(
                    f"channels[{at}].color must be six hexadecimal digits"
                ) from exc
            channel["color"] = color.upper()

        declared_range = raw.get("range")
        if declared_range is not None:
            channel["range"] = _pair(declared_range, f"channels[{at}].range", "min", "max")

        window = raw.get("displayWindow")
        provenance = raw.get("windowProvenance")
        if (window is None) != (provenance is None):
            raise ValueError(
                f"channels[{at}] must carry displayWindow and windowProvenance together"
            )
        if window is not None:
            if "range" not in channel:
                raise ValueError(f"channels[{at}].displayWindow requires range")
            channel["displayWindow"] = _pair(
                window, f"channels[{at}].displayWindow", "start", "end"
            )
            if (
                float(channel["displayWindow"]["start"]) < float(channel["range"]["min"])
                or float(channel["displayWindow"]["end"]) > float(channel["range"]["max"])
            ):
                raise ValueError(f"channels[{at}].displayWindow must lie inside range")
            if not isinstance(provenance, dict):
                raise ValueError(f"channels[{at}].windowProvenance must be an object")
            normal_provenance = deepcopy(provenance)
            normal_provenance["method"] = _text(
                provenance.get("method"), f"channels[{at}].windowProvenance.method"
            )
            normal_provenance["resolvedFrom"] = _text(
                provenance.get("resolvedFrom"),
                f"channels[{at}].windowProvenance.resolvedFrom",
            )
            if "sampleCount" in provenance:
                normal_provenance["sampleCount"] = _whole(
                    provenance["sampleCount"],
                    f"channels[{at}].windowProvenance.sampleCount",
                )
            if "resolvedAtRevision" in provenance:
                normal_provenance["resolvedAtRevision"] = _whole(
                    provenance["resolvedAtRevision"],
                    f"channels[{at}].windowProvenance.resolvedAtRevision",
                )
            algorithm = provenance.get("algorithm")
            if algorithm is not None and not isinstance(algorithm, str):
                raise ValueError(
                    f"channels[{at}].windowProvenance.algorithm must be a string or null"
                )
            channel["windowProvenance"] = normal_provenance
        channels.append(channel)

    if indices != set(range(channel_count)):
        raise ValueError(f"channel indices must be exactly 0 through {channel_count - 1}")
    channels.sort(key=lambda channel: channel["index"])
    return {"schema": SCHEMA, "acquisitionType": found_type, "channels": channels}


def read_acquisition_description(folder: str | Path, *, channel_count: int) -> dict | None:
    """Read the sidecar in ``folder``, or ``None`` when this is a foreign run."""
    folder = Path(folder)
    source = folder / DESCRIPTION_NAME
    if not source.is_file():
        return None
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{source} is not a readable acquisition display description") from exc
    return validate_acquisition_description(
        value, acquisition_type=folder.name, channel_count=channel_count
    )


def source_metadata(description: dict) -> tuple[dict, dict]:
    """Convert a validated sidecar into ordinary OME plus ZMART provenance."""
    channels = []
    recorded = []
    for channel in description["channels"]:
        shown = {"label": channel["label"]}
        if channel.get("color") is not None:
            shown["color"] = channel["color"]
        if channel.get("range") is not None:
            window = dict(channel["range"])
            if channel.get("displayWindow") is not None:
                window.update(channel["displayWindow"])
            shown["window"] = window
        channels.append(shown)
        recorded.append(
            {
                "key": channel["key"],
                "index": channel["index"],
                **(
                    {"displayWindow": deepcopy(channel["displayWindow"])}
                    if channel.get("displayWindow") is not None
                    else {}
                ),
                **(
                    {"windowProvenance": deepcopy(channel["windowProvenance"])}
                    if channel.get("windowProvenance") is not None
                    else {}
                ),
            }
        )
    return (
        {"channels": channels},
        {
            "acquisitionDisplaySchema": SCHEMA,
            "acquisitionType": description["acquisitionType"],
            "displayWindowSource": DESCRIPTION_NAME,
            "displayWindows": recorded,
        },
    )


def _legacy_window(entry: dict, first: str, second: str) -> tuple[float, float] | None:
    window = entry.get("window")
    if not isinstance(window, dict):
        return None
    low, high = window.get(first), window.get(second)
    if (
        isinstance(low, bool)
        or isinstance(high, bool)
        or not isinstance(low, (int, float))
        or not isinstance(high, (int, float))
        or not math.isfinite(float(low))
        or not math.isfinite(float(high))
        or float(high) <= float(low)
    ):
        return None
    return float(low), float(high)


def legacy_source_metadata(
    descriptions: list[dict | None], *, acquisition_type: str, channel_count: int
) -> tuple[dict | None, dict | None]:
    """Reconcile position OME metadata without making the first tile authority."""
    if not any(isinstance(one, dict) for one in descriptions):
        return None, None

    identities = []
    display_pairs = []
    other_omero = []
    for at, description in enumerate(descriptions):
        description = description if isinstance(description, dict) else {}
        base = {key: deepcopy(value) for key, value in description.items() if key != "channels"}
        other_omero.append(base)
        raw = description.get("channels")
        raw = raw if isinstance(raw, list) else []
        if len(raw) > channel_count:
            raise ValueError(
                f"position {at + 1} describes {len(raw)} channels but its pixels hold "
                f"{channel_count}"
            )
        one_identity = []
        one_display = []
        for index in range(channel_count):
            entry = raw[index] if index < len(raw) and isinstance(raw[index], dict) else {}
            label = str(entry.get("label") or entry.get("name") or f"channel {index + 1}")
            color = entry.get("color")
            color = color.upper() if isinstance(color, str) else None
            one_identity.append(
                (label, color, _legacy_window(entry, "min", "max"), entry.get("active") is not False)
            )
            one_display.append(_legacy_window(entry, "start", "end"))
        identities.append(one_identity)
        display_pairs.append(one_display)

    if any(value != other_omero[0] for value in other_omero[1:]):
        raise ValueError("the positions disagree about their non-channel OME display metadata")
    if any(value != identities[0] for value in identities[1:]):
        raise ValueError(
            "the positions disagree about channel count, labels, colours, ranges, or visibility"
        )

    channels = []
    provenance = []
    for index, (label, color, declared_range, active) in enumerate(identities[0]):
        entry: dict = {"label": label}
        if color is not None:
            entry["color"] = color
        if not active:
            entry["active"] = False
        window = None
        if declared_range is not None:
            window = {"min": declared_range[0], "max": declared_range[1]}
        candidates = [position[index] for position in display_pairs]
        consensus = candidates[0] if all(candidate == candidates[0] for candidate in candidates) else None
        if window is not None:
            if consensus is not None:
                window.update({"start": consensus[0], "end": consensus[1]})
            entry["window"] = window
        channels.append(entry)
        if consensus is not None:
            provenance.append(
                {
                    "key": label,
                    "index": index,
                    "displayWindow": {"start": consensus[0], "end": consensus[1]},
                    "windowProvenance": {
                        "method": "legacy-consensus",
                        "resolvedFrom": "legacy-position-consensus",
                    },
                }
            )

    omero = {**other_omero[0], "channels": channels}
    zmart = {
        "acquisitionDisplaySchema": SCHEMA,
        "acquisitionType": acquisition_type,
        "displayWindowSource": "legacy-position-metadata",
        "displayWindows": provenance,
    }
    return omero, zmart


def metadata_for_transfer(
    folder: str | Path, descriptions: list[dict | None], *, channel_count: int
) -> tuple[dict | None, dict | None]:
    """The one display description for a composed transfer."""
    folder = Path(folder)
    declared = read_acquisition_description(folder, channel_count=channel_count)
    if declared is not None:
        return source_metadata(declared)
    return legacy_source_metadata(
        descriptions, acquisition_type=folder.name, channel_count=channel_count
    )
