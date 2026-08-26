"""Watch a finished dataset assemble itself, position by position.

    python replay.py "D:/OMEzarr data/some-scan" D:/watching --every 1.5

Then open ``D:/watching`` in the viewer -- or open it first and watch it fill.

**Nothing is copied.** The positions stay exactly where they are; what grows is
a folder of junctions pointing at them, and the picture declared over that
folder is re-declared each time one appears. The viewer is told to look again,
re-reads what is on screen, and draws the new tile. A hundred-gigabyte dataset
costs a few kilobytes of pointers and a description.

This replaced a version that did the opposite (2026-08-26). It handed every
position's pixels to the live writer, which wrote them all again with a fresh
pyramid: measured at 1.25x the dataset on disk and 161 seconds for 87 MB, and
it inherited every rule the writer has -- a grid to sit on, a legal chunk
size, whole-number halving, an overlap band that fits -- so it refused
datasets the viewer opens without complaint. None of that is needed to look at
data that already exists. The rule this project is built on is that pixels are
pointed at, not copied, and a replay was the one place breaking it.

What it still rehearses, which is the part worth rehearsing: positions
appearing one at a time, the view being re-declared under a watching page, the
announcement, the in-place refresh, and the picture growing on screen. What it
no longer rehearses is the writer, which has its own tests and nothing to
prove about data it did not write.

One rule, learned the hard way by the spiral demo this grew out of: **the room
is pinned first.** The declared extent comes from the positions present, and
the page's in-place refresh re-reads pixels rather than the description -- so a
picture whose SHAPE grows only shows the growth after a reload. The two
furthest-apart positions go down before anything else, and the canvas covers
the whole survey from the first frame.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

_VIEWER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_VIEWER / "app" / "picture"))
sys.path.insert(0, str(_VIEWER.parent))

from declare import declare_a_built_picture  # noqa: E402
from mosaic import read_the_transfer  # noqa: E402


def _junction(link: Path, target: Path) -> None:
    """Make one folder appear inside another without copying a byte.

    A junction rather than a symbolic link because Windows grants junctions to
    an ordinary user and symbolic links only to an administrator, and this has
    to work on a microscope PC nobody is going to elevate.
    """
    link.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(
            f"could not point at {target}:\n  {done.stdout.strip()}"
            f"{done.stderr.strip()}")


def the_positions_of(dataset: Path) -> list[Path]:
    """Every position store in a dataset, in the order they were imaged.

    By name, because that is the order a microscope writes them in and the
    order an operator expects to watch them arrive.
    """
    found = sorted(one for one in dataset.iterdir()
                   if one.is_dir() and one.name.endswith(".ome.zarr"))
    if not found:
        raise SystemExit(f"no positions found in {dataset}\n"
                         "  (looking for folders named *.ome.zarr)")
    return found


def the_two_corners(dataset: Path, positions: list[Path]) -> list[Path]:
    """The two positions furthest apart, which pin the picture's shape.

    Read through the same mosaic reader the viewer uses, so what is measured
    here is what the viewer will place.
    """
    mosaic = read_the_transfer(dataset)
    where = {}
    for tile in mosaic.tiles:
        at = getattr(tile, "corner_um", None) or getattr(tile, "origin_um", None)
        if at is not None:
            where[Path(getattr(tile, "store", "")).name] = tuple(at[-2:])
    if len(where) < 2:
        return positions[:1]
    nearest = min(where, key=lambda name: where[name])
    furthest = max(where, key=lambda name: where[name])
    by_name = {one.name: one for one in positions}
    return [by_name[name] for name in dict.fromkeys((nearest, furthest))
            if name in by_name]


def _say_something_changed(where: str) -> None:
    """Tell an open viewer to look again, the way a microscope would.

    Best effort: a replay nobody is watching is a perfectly good replay, and a
    viewer that has been closed must not stop it.
    """
    try:
        request = urllib.request.Request(
            where.rstrip("/") + "/api/announce",
            data=json.dumps({"wrote_image_in_place": True}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(request, timeout=5).close()
    except OSError:
        pass


def replay_the_dataset(dataset: str | Path, folder: str | Path, *,
                       every_s: float = 0.0, told=None, announce=None) -> Path:
    """Reveal a dataset one position at a time, copying nothing.

    Returns the picture's folder -- the one thing a viewer opens.
    """
    dataset, folder = Path(dataset).resolve(), Path(folder)
    positions = the_positions_of(dataset)

    # The junctions live beside the dataset, not beside the picture, so that
    # every pointer resolves within the same root the pixels are in.
    appearing = dataset.parent / f"{dataset.name}-appearing"
    for one in (appearing, folder):
        if one.exists():
            shutil.rmtree(one)
        one.mkdir(parents=True)

    corners = the_two_corners(dataset, positions)
    order = corners + [one for one in positions if one not in corners]

    picture = None
    for number, position in enumerate(order, start=1):
        _junction(appearing / position.name, position)
        picture = declare_a_built_picture(folder, appearing, name=dataset.name)
        if told:
            told(number, len(order))
        if announce:
            announce()
        if every_s and number < len(order):
            time.sleep(every_s)
    return picture


def main() -> int:
    asked = argparse.ArgumentParser(
        description="Watch a finished dataset assemble itself, position by "
                    "position, copying nothing.")
    asked.add_argument("dataset", type=Path,
                       help="the folder of positions to reveal, one OME-Zarr each")
    asked.add_argument("folder", type=Path,
                       help="where the picture's description goes. This is the "
                            "folder you open in the viewer.")
    asked.add_argument("--every", type=float, default=0.0, metavar="SECONDS",
                       help="how long to wait between positions. The first "
                            "goes immediately.")
    asked.add_argument("--tell", default=None, metavar="ADDRESS",
                       help="a running viewer to notify after each position, "
                            "for example http://127.0.0.1:8848")
    given = asked.parse_args()

    positions = the_positions_of(given.dataset.resolve())
    print("")
    print(f"  {len(positions)} positions, revealed one at a time")
    print(f"  the picture goes in {given.folder}")
    print("")

    def told(done: int, total: int) -> None:
        print(f"\r  {done}/{total} showing", end="", flush=True)

    picture = replay_the_dataset(
        given.dataset, given.folder, every_s=given.every, told=told,
        announce=(lambda: _say_something_changed(given.tell))
        if given.tell else None)
    print("")
    print("")
    print(f"  all of it is showing: {picture}")
    print("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
