"""What loading costs per format: 0.4, 0.5, grown, and every awkward store.

The static half of the adoption question. Each case is declared through the
one door twice -- linked (composed on request) and baked -- and served
through the same functions the HTTP door calls: the declare time, the cold
first piece, and the warm median are the numbers. Live is deliberately not
a column here: the live path writes the writer's own format, measured by
the scale matrix. Position pixels are three stamped bodies reused across
every case -- nothing big is generated.
"""

import json
import os
import statistics
import sys
import time
from pathlib import Path

_VIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_VIZ))
sys.path.insert(0, str(_VIZ / "tests"))
sys.path.insert(0, str(_VIZ / "testdata"))

import numpy as np  # noqa: E402
from test_positions_land_wherever_they_are_put import write_position  # noqa: E402

from zmart_viewer import pieces as served  # noqa: E402
from zmart_viewer.building import declare_a_built_picture  # noqa: E402

WORK = Path(os.environ.get("ZMART_FORMAT_BENCH", "/tmp/zmart-format-bench"))
AWKWARD = _VIZ / "test_stores" / "awkward"

PLACES = [
    (0.0, 0.0),
    (120.0, 95.0),
    (120.0, 95.0),
    (10.3, 240.8),
    (240.0, 0.0),
    (0.0, 260.0),
    (250.5, 250.5),
    (60.0, 130.0),
    (130.0, 60.0),
]


def a_case_folder(name: str, **kwargs) -> Path:
    folder = WORK / "cases" / name

    if not folder.is_dir():
        folder.mkdir(parents=True)

        for index, place in enumerate(PLACES):
            write_position(folder / f"pos_{index}.zarr", 100 + index % 3, place, size=192, **kwargs)
    return folder


def levels_of(store: Path) -> list[int]:
    return sorted(int(one.name) for one in store.iterdir() if one.name.isdigit() and one.is_dir())


def piece_path(store: Path, level: int) -> str:
    shape = json.loads((store / str(level) / "zarr.json").read_text())["shape"]
    return f"{level}/c/" + "/".join("0" for _ in shape)


def one_case(name: str, transfer: Path) -> None:
    views = WORK / "views" / name

    try:
        began = time.perf_counter()
        store = declare_a_built_picture(views, transfer, name="linked")
        declared_ms = (time.perf_counter() - began) * 1000
    except Exception as refusal:
        print(f"  {name:32} REFUSED to declare: {str(refusal).splitlines()[0][:90]}")
        return

    coarse = piece_path(store, levels_of(store)[-1])
    laps, absent = [], 0

    for _ in range(12):
        marked = time.perf_counter()
        answer = served.built_bytes_behind(store, coarse)
        laps.append((time.perf_counter() - marked) * 1000)
        absent += answer is None
    served.forget(store)

    began = time.perf_counter()
    baked_store = declare_a_built_picture(views, transfer, name="baked", bake=True)
    baked_ms = (time.perf_counter() - began) * 1000
    baked_piece = piece_path(baked_store, levels_of(baked_store)[-1])
    baked_laps, baked_absent = [], 0

    for _ in range(12):
        marked = time.perf_counter()
        answer = served.built_bytes_behind(baked_store, baked_piece)
        baked_laps.append((time.perf_counter() - marked) * 1000)
        baked_absent += answer is None
    served.forget(baked_store)
    notes = "".join(
        f"  [{count} ABSENT {kind}]"
        for kind, count in (("linked", absent), ("baked", baked_absent))
        if count
    )
    print(
        f"  {name:32} declare {declared_ms:7.0f}ms | linked cold {laps[0]:6.1f}ms "
        f"warm {statistics.median(laps[1:]):5.2f}ms | bake {baked_ms:7.0f}ms | "
        f"baked warm {statistics.median(baked_laps[1:]):5.2f}ms{notes}",
        flush=True,
    )


def main() -> None:
    print("== positions in both generations, nominal and grown\n")
    one_case("v04_scattered", a_case_folder("v04", version="0.4"))
    one_case("v05_scattered", a_case_folder("v05", version="0.5", names=("z", "y", "x")))
    one_case(
        "v05_grown_t_c", a_case_folder("v05tc", version="0.5", names=("t", "c", "z", "y", "x"))
    )
    one_case("v04_uint8", a_case_folder("v04u8", version="0.4", dtype=np.uint8))

    print("\n== every awkward store, each through the same door\n")

    if not AWKWARD.is_dir():
        print("  (awkward stores not on disk -- run testdata/make_awkward_stores.py)")
        return

    for awkward in sorted(AWKWARD.iterdir()):
        if awkward.is_dir():
            one_case(awkward.name, awkward)


if __name__ == "__main__":
    main()
