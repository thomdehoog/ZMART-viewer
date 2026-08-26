"""Watch a finished dataset assemble itself, position by position.

    python replay.py "D:/OMEzarr data/some-scan" D:/watching --every 1.5

Then open ``D:/watching`` in the viewer -- or open it first and watch it fill.

**Nothing is copied, and the picture never changes shape.** Every position goes
in first as a description alone -- eight kilobytes standing for a seven-gigabyte
tile -- so the room is declared, not inferred, and has its final shape before a
single pixel is shown. Then the pixels arrive into it, in the order the stage
scanned them, each one a rename rather than a copy. A hundred-gigabyte dataset
costs a few kilobytes of pointers and a description.

The shape matters more than it looks. A picture whose extent grows has to tell
the page it grew, the page learns a new shape only by re-resolving its source,
and re-resolving throws the loaded source away before reading the description
again -- with nothing to draw in between. That is the flicker of 2026-08-23,
measured at 100-300 ms of black on every landing. A declared room never asks
the question.

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

**The room is declared, not inferred.** Every position goes in first as a
description alone -- eight kilobytes to stand for a seven-gigabyte tile -- so
the picture has its final shape before a single pixel is shown. Then the
pixels arrive in the order they were imaged. Without that the extent is the
bounding box of whatever is present, it changes on every arrival, and since
the page's in-place refresh re-reads pixels and never the description, tiles
end up drawn against coordinates that have moved.
"""
from __future__ import annotations

import argparse
import json
import os
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


def in_the_order_they_were_imaged(dataset: Path,
                                 positions: list[Path]) -> list[Path]:
    """The positions sorted the way a stage scans: top-left, then rightwards.

    By where each one SITS, read from its own translation, not by what it is
    called. A filename order is whatever the exporter felt like, and watching
    a survey fill in that order tells an operator nothing.

    Nothing depends on this order being right -- the room is declared before
    any of it arrives, so the picture's shape is settled whatever sequence the
    pixels come in. It is here because an operator watching a survey fill
    should see it fill the way it was imaged.
    """
    mosaic = read_the_transfer(dataset)
    by_name = {one.name: one for one in positions}
    sits_at = {}
    for tile in mosaic.tiles:
        if tile.copies:
            down, across = tile.copies[0].corner_um[-2:]
            sits_at[Path(tile.store).name] = (round(down, 1), round(across, 1))
    ordered = [by_name[name] for name in
               sorted(sits_at, key=lambda name: sits_at[name])
               if name in by_name]
    # Anything whose place could not be read goes last rather than missing.
    return ordered + [one for one in positions if one not in ordered]


def reveal(appearing: Path, position: Path) -> None:
    """Swap a position's description for its pixels, without a gap.

    Deleting the stub and then making the junction leaves a moment when the
    store is not there at all, and a request landing in it does not get
    "nothing here" -- it gets an exception. Two renames instead: the junction
    is built beside its place and moved in, so the path always resolves either
    to the empty version or to the full one (2026-08-26).
    """
    spot = appearing / position.name
    arriving = appearing / f"{position.name}.arriving"
    leaving = appearing / f"{position.name}.leaving"
    for one in (arriving, leaving):
        if one.exists():
            shutil.rmtree(one)
    _junction(arriving, position)
    os.replace(spot, leaving)
    os.replace(arriving, spot)
    shutil.rmtree(leaving)




def declare_the_room(position: Path, into: Path) -> None:
    """Put a position in the picture as a description, with none of its pixels.

    Its ``zarr.json`` and each level's, copied -- eight kilobytes to stand for
    a seven-gigabyte tile. The reader places it exactly as it will be placed
    when the pixels arrive, because placing is what a description is for; the
    composer simply finds no chunks there yet and leaves that ground empty.

    This is what lets the room be DECLARED rather than inferred. The extent of
    a built picture is the bounding box of what is present, so a picture grown
    by adding positions keeps changing shape -- and the page's in-place
    refresh re-reads pixels, never the description, so every tile ends up
    drawn against coordinates that have moved. The operator sees tiles arrive
    in the wrong places, which is worse than seeing nothing at all.

    Guessing which few positions bound the box was tried first and is a trap:
    a corner is not an extent, and one position can hold the smallest y while
    another holds the smallest x. Declaring all of them costs kilobytes and
    cannot be wrong.
    """
    into.mkdir(parents=True, exist_ok=True)
    if (position / "zarr.json").exists():
        # version 3: one zarr.json for the group, one for each level
        described = json.loads(
            (position / "zarr.json").read_text(encoding="utf-8"))
        multiscales = described["attributes"]["ome"]["multiscales"][0]
        naming = ["zarr.json"]
    else:
        # version 2, which older exports still carry: flat .zattrs beside
        # .zgroup, and a .zarray per level. Refusing these would refuse
        # datasets the viewer opens without complaint.
        described = json.loads(
            (position / ".zattrs").read_text(encoding="utf-8"))
        multiscales = described["multiscales"][0]
        naming = [".zattrs", ".zgroup", ".zarray"]
    for name in naming:
        if (position / name).exists():
            shutil.copy2(position / name, into / name)
    for level in multiscales["datasets"]:
        (into / level["path"]).mkdir(parents=True, exist_ok=True)
        for name in naming:
            if (position / level["path"] / name).exists():
                shutil.copy2(position / level["path"] / name,
                             into / level["path"] / name)


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

    # The folder GROWS, the way the spiral demo does: a position that has not
    # arrived is simply absent.
    #
    # Declaring the whole room up front instead -- every position present as a
    # description with no pixels -- places everything perfectly and then shows
    # nothing: each piece composes empty on first sight and is served from
    # that. See declare_the_room, kept for the day the piece cache can be told
    # a picture changed (2026-08-26).
    # The room is declared from every position's description -- kilobytes,
    # no pixels -- so the picture has its final shape before anything is
    # shown and the page never has to be told the shape changed. Learning a
    # new shape means re-resolving the source, and that is the flicker of
    # 2026-08-23: the layer has nothing to draw between throwing the old
    # description away and reading the new one.
    for position in positions:
        declare_the_room(position, appearing / position.name)
    picture = declare_a_built_picture(folder, appearing, name=dataset.name)
    if announce:
        announce()

    # Then the pixels, in the order the stage scanned them. Nothing jumps the
    # queue, because nothing has to hold the shape up.
    order = in_the_order_they_were_imaged(dataset, positions)
    for number, position in enumerate(order, start=1):
        reveal(appearing, position)
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
