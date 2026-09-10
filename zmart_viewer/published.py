"""Publish aggregate pictures from completed external position stores.

The acquisition supplies stable specimen bounds and completed-store revisions.
Pixels stay in their original stores; only coarse chunks are materialized here.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import threading
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple

from .acquired import AcquiredRegion, canonical_regions
from .building import ComposedPicture, _after_a_windows_reader, _holding_the_bake_lock
from .compose import (
    Composer,
    Mosaic,
    _read_one_tile,
    _refuse_tiles_that_disagree,
    read_the_mosaic_as_written,
    the_frame_room_of,
    the_mosaic_written_down,
    uses_legacy_mean,
)
from .library import _description_file, _read_attrs_at, discover

STORE = ".zmart-viewer/overview.ome.zarr"
STACK_STORE = ".zmart-viewer/stack.ome.zarr"
ACQUISITION_RENDERING_VERSION = 1


def validate_canvas(canvas):
    for axis in ("x_um", "y_um"):
        try:
            low, high = canvas[axis]
            valid = all(math.isfinite(value) for value in (low, high)) and high > low
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError(f"Invalid specimen canvas bounds for {axis}")


def _place_depth(tiles, *, spacing=None, combine=False):
    """Display flats together; keep stacks on their common specimen-Z lattice."""
    flat = [tile.copies[0].shape[0] == 1 for tile in tiles]
    if any(flat) and not all(flat) and not combine:
        raise ValueError("Separate flat and stack aggregates are required for mixed sources")
    if combine and not all(flat):
        if spacing is None:
            spacing = next(t.copies[0].voxel_um[0] for t in tiles if t.copies[0].shape[0] > 1)
        tiles = [
            replace(
                t,
                copies=[
                    replace(
                        c, corner_um=(0.0, *c.corner_um[1:]), voxel_um=(spacing, *c.voxel_um[1:])
                    )
                    for c in t.copies
                ],
            )
            if is_flat
            else t
            for t, is_flat in zip(tiles, flat)
        ]
    if all(flat):
        placed = [
            replace(
                tile,
                copies=[
                    replace(
                        copy,
                        corner_um=(0.0, *copy.corner_um[1:]),
                        voxel_um=(1.0, *copy.voxel_um[1:]),
                    )
                    for copy in tile.copies
                ],
            )
            for tile in tiles
        ]
        return placed, 0.0, 1.0
    if spacing is None:
        spacing = tiles[0].copies[0].voxel_um[0]
    if (
        not math.isfinite(spacing)
        or spacing <= 0
        or any(
            not math.isfinite(copy.voxel_um[0])
            or copy.voxel_um[0] <= 0
            # Match the origin-alignment tolerance over the full stack, not per plane.
            or abs(copy.voxel_um[0] - spacing) * copy.shape[0] / spacing > 1e-7
            for tile in tiles
            for copy in tile.copies
        )
    ):
        raise ValueError("Stack aggregates require the same positive Z spacing at every level")
    tiles = [
        replace(
            tile,
            copies=[replace(copy, voxel_um=(spacing, *copy.voxel_um[1:])) for copy in tile.copies],
        )
        for tile in tiles
    ]
    lower = min(tile.copies[0].corner_um[0] for tile in tiles)
    offsets = [(tile.copies[0].corner_um[0] - lower) / spacing for tile in tiles]
    if any(
        not math.isfinite(n) or not math.isclose(n, round(n), rel_tol=0, abs_tol=1e-7)
        for n in offsets
    ):
        raise ValueError("Stack origins must be aligned to a common specimen-Z grid")
    planes = max(
        round(offset) + tile.copies[0].shape[0] for tile, offset in zip(tiles, offsets, strict=True)
    )
    return tiles, lower, planes * spacing


class _Owner(NamedTuple):
    view: object
    canvas: dict
    automatic: bool
    dataset: int


class PublishedFolders:
    """Server-owned optional views, driven by its existing publication requests."""

    def __init__(self, library, *, bake=False, canvas=None):
        self.library = library
        self.bake = bake
        self.canvas = canvas
        self.views = {}
        self._lock = threading.RLock()
        self._opening_lock = threading.Lock()

    def open(self, path, *, canvas=None, versions=None, composition=None, bake=True, views=None):
        with self._opening_lock:
            return self._open(
                path,
                canvas=canvas,
                versions=versions,
                composition=composition,
                bake=bake,
                views=views,
            )

    def _dataset(self, key):
        held = self.views.get(key)
        if held is not None:
            return self.library.dataset(held.dataset)
        root, acquisition = key
        return next(
            (
                d
                for d in self.library.datasets()
                if d.root == root
                and (
                    d.acquisition == acquisition
                    if acquisition is not None
                    else not isinstance(d.acquisition, str)
                )
            ),
            None,
        )

    def _owner_for(self, number):
        dataset = self.library.dataset(number)
        if dataset is None:
            return None
        acquisition = dataset.acquisition if isinstance(dataset.acquisition, str) else None
        held = self.views.get((dataset.root, acquisition))
        return held if held is not None and held.dataset == number else None

    def _open(self, path, *, canvas=None, versions=None, composition=None, bake=True, views=None):
        """Open or update the single publisher for a resolved acquisition folder."""
        bounds = canvas if canvas is not None else self.canvas
        if not bounds:
            raise ValueError("A live baked folder needs its full specimen canvas bounds")
        if composition is not None and versions is None:
            raise ValueError("Acquired composition needs explicit completed source revisions")
        if views is not None:
            from .views import ViewSet

            if composition is None or versions is None:
                raise ValueError("Named views require explicit coverage and completed revisions")
            destination = Path(views["path"]).resolve()
            key = (destination, views["acquisition"])
            with self._lock:
                held = self.views.get(key)
                if any(
                    root == destination
                    and owner != key[1]
                    and getattr(entry[0], "source_folder", None) == Path(path).resolve()
                    for (root, owner), entry in self.views.items()
                ):
                    raise ValueError(
                        "This original source folder already has an acquisition owner in this view folder"
                    )
            view = (
                held[0]
                if held
                else ViewSet(
                    destination,
                    acquisition=views["acquisition"],
                    modes=views.get("modes", ("slice", "top")),
                    projections=views.get("projections", ()),
                    projection_folder=views.get("projection_path"),
                )
            )
            if held and (
                view.modes != set(views.get("modes", ("slice", "top")))
                or view.projections != set(views.get("projections", ()))
                or view.projection_folder
                != (
                    Path(views["projection_path"]).resolve()
                    if views.get("projection_path")
                    else None
                )
            ):
                raise ValueError("An open view set cannot change its output options")
            try:
                view.publish(path, versions, bounds, composition=composition, bake=bake)
                dataset = self._dataset(key)
                number = (
                    dataset.number
                    if dataset
                    else self.library.open(destination, names=view.sources, watch=True)
                )
                with self._lock:
                    self.views[key] = _Owner(view, bounds, False, number)
                return number
            except Exception:
                if not held:
                    view.close()
                raise
        automatic = versions is None
        root = discover(path)[0].resolve()
        dataset = self._dataset((root, None))
        created = dataset is None
        number = self.library.open(path) if created else dataset.number
        with self._lock:
            held = self.views.get((root, None))
        view = (
            held[0]
            if held
            else (
                PublishedAcquisition(root)
                if composition is not None
                else PublishedTransfer(root / STORE)
            )
        )
        try:
            if automatic:
                versions = self._versions_on_disk(root)
            view.publish(root, versions, bounds, composition=composition, bake=bake)
        except Exception:
            if not held:
                view.close()
            if created:
                self.library.close(number)
            raise
        with self._lock:
            self.views[root, None] = _Owner(view, bounds, automatic, number)
        return number

    @staticmethod
    def _versions_on_disk(folder):
        root, names = discover(folder)
        return {name: _description_file(root / name).stat().st_mtime_ns for name in names}

    def announce(self, publications):
        if not isinstance(publications, list):
            raise ValueError("publications must be a list of completed folder snapshots")
        with self._lock:
            opened = list(self.views.items())
        for publication in publications:
            matched = False
            folder = Path(publication["path"]).resolve()
            for key, (view, canvas, _automatic, _number) in opened:
                dataset = self._dataset(key)
                if dataset and getattr(view, "source_folder", dataset.root) == folder:
                    view.publish(
                        folder,
                        publication["source_revisions"],
                        canvas,
                        composition=publication.get("composition"),
                        bake=view.bake,
                    )
                    matched = True
            if not matched:
                raise ValueError(f"No published folder is open at {folder}")

    def refresh(self):
        with self._lock:
            opened = list(self.views.items())
        for key, (view, canvas, automatic, _number) in opened:
            try:
                dataset = self._dataset(key)
                if dataset is None:
                    with self._lock:
                        if self.views.get(key, (None,))[0] is not view:
                            continue
                        del self.views[key]
                    view.close()
                elif automatic:
                    view.publish(dataset.root, self._versions_on_disk(dataset.root), canvas)
            except Exception:
                # This background boundary must keep other owners observable.
                logging.getLogger(__name__).exception("Cannot refresh publication %s", key)
        return self.revisions()

    def revisions(self):
        """Committed publication state, without starting work or waiting for a bake."""
        with self._lock:
            revisions = {}
            for key, (view, _, _, _number) in self.views.items():
                dataset = self._dataset(key)
                if dataset is not None:
                    revisions[dataset.number] = view.revision
            return tuple(revisions.items())

    def entries(self, entries):
        with self._lock:
            result, seen = [], set()
            for number, root, name in entries:
                dataset = self.library.dataset(number)
                held = (
                    self._owner_for(number)
                    if dataset and not isinstance(dataset.acquisition, str)
                    else None
                )
                if held is None:
                    result.append((number, root, name))
                elif number not in seen:
                    view = held[0]
                    names = view.sources if hasattr(view, "sources") else [STORE]
                    result.extend((number, root, name) for name in names)
                    seen.add(number)
            return result

    def source_revision(self, number, name=STORE):
        with self._lock:
            held = self._owner_for(number)
            if held is None:
                return None
            view = held[0]
            if hasattr(view, "outputs"):
                return view.outputs[name].revision if name in view.outputs else None
            return view.revision

    def source_depth(self, number, name):
        held = self._owner_for(number)
        if held and isinstance(held[0], PublishedAcquisition) and name in held[0].outputs:
            return "flat" if name == STORE else "stack"
        return None

    def close(self):
        with self._lock:
            for view, _, _, _number in self.views.values():
                view.close()
            self.views.clear()


def _atomic_json(path: Path, value: dict) -> None:
    arriving = path.with_name(path.name + ".publishing")
    arriving.write_text(json.dumps(value), encoding="utf-8")
    _after_a_windows_reader(os.replace, arriving, path)


class PublishedAcquisition:
    """One acquisition, at most two pictures: persistent flats and relative-Z stacks."""

    def __init__(self, folder, piece=512):
        self.folder, self.piece = folder, piece
        self.outputs = {}
        self._sources = {}
        for name in (STORE, STACK_STORE):
            if (folder / name / "publication.json").exists():
                output = PublishedTransfer(folder / name, piece)
                output._read_snapshot()
                self.outputs[name] = output

    @property
    def sources(self):
        return [name for name in (STORE, STACK_STORE) if name in self.outputs]

    @property
    def revision(self):
        return sum(output.revision for output in self.outputs.values())

    @property
    def bake(self):
        return next(iter(self.outputs.values())).bake if self.outputs else True

    def close(self):
        for output in self.outputs.values():
            output.close()

    def publish(self, folder, versions, canvas, *, composition=None, bake=True):
        if not isinstance(versions, dict) or (not versions and not self.outputs):
            raise ValueError("An acquisition needs completed source revisions")
        if not isinstance(composition, dict) or not {"regions", "order"} <= composition.keys():
            raise ValueError("An acquisition needs explicit acquired coverage and order")
        regions, order = composition["regions"], composition["order"]
        if (
            not isinstance(order, list)
            or any(not isinstance(n, str) for n in order)
            or len(order) != len(versions)
            or set(order) != set(versions)
            or (
                regions != "complete"
                and (not isinstance(regions, dict) or set(regions) != set(versions))
            )
        ):
            raise ValueError("Coverage and order must name every source exactly once")
        explicit = composition.get("z_references", {})
        if not isinstance(explicit, dict) or explicit.keys() - versions.keys():
            raise ValueError("Z references must name completed sources")
        sources = {}
        for name, revision in versions.items():
            if Path(name).name != name or not name.endswith(".ome.zarr"):
                raise ValueError("Position names must name OME-Zarr stores directly in the folder")
            cached = self._sources.get(name)
            if cached and cached[0] == revision:
                sources[name] = cached
                continue
            tile = _read_one_tile(folder / name)
            # Nearest-neighbour sampling onto the aggregate grid moves a footprint by
            # at most half a finest voxel. Apply the same shift to every native level;
            # original pixels, metadata and authoritative source-local coverage stay intact.
            base = tile.copies[0]
            shift = [0.0]
            for axis, key in ((1, "y_um"), (2, "x_um")):
                start, step = base.corner_um[axis], base.voxel_um[axis]
                low = canvas[key][0]
                shift.append(low + math.floor((start - low) / step + 0.5) * step - start)
            tile = replace(
                tile,
                copies=[
                    replace(
                        copy,
                        corner_um=tuple(a + b for a, b in zip(copy.corner_um, shift, strict=True)),
                    )
                    for copy in tile.copies
                ],
            )
            kind = STORE if tile.copies[0].shape[0] == 1 else STACK_STORE
            if cached and cached[1] != kind:
                raise ValueError("A published position cannot change between flat and stack")
            model = _read_attrs_at(tile.store).get("zmart_microscopy", {}).get("z_coordinate", {})
            reference = None
            if model.get("frame") == "specimen":
                reference = model.get("acquisition_provenance", {}).get(
                    "requested_stage_focus_z_um"
                )
            if reference is None:
                reference = tile.copies[0].corner_um[0]
            sources[name] = (revision, kind, tile, reference)
        channel_counts = {
            the_frame_room_of(tile.copies[0].outer_shape)[1] for _, _, tile, _ in sources.values()
        }
        channel_counts.update(output._held.mosaic.frame_room[1] for output in self.outputs.values())
        if len(channel_counts) > 1:
            raise ValueError("Flat and stack sources in an acquisition must share a channel count")
        outputs = dict(self.outputs)
        try:
            with ExitStack() as prepared:
                commits = []
                for kind in (STORE, STACK_STORE):
                    names = [name for name in order if sources[name][1] == kind]
                    if not names and kind not in outputs:
                        continue
                    if kind not in outputs:
                        outputs[kind] = PublishedTransfer(folder / kind, self.piece)
                    output = outputs[kind]
                    part = deepcopy(composition)
                    if names:
                        part["order"] = names
                        part["regions"] = (
                            regions if regions == "complete" else {n: regions[n] for n in names}
                        )
                        part["z_references"] = {n: explicit.get(n, sources[n][3]) for n in names}
                        revisions = {n: versions[n] for n in names}
                        tiles = {n: sources[n][2] for n in names}
                    else:
                        # Keep the published geometry/address, but remove every acquired region.
                        part = deepcopy(output._state["composition"])
                        revisions = output._state["versions"]
                        part["regions"] = {n: [] for n in revisions}
                        tiles = {}
                    commits.append(
                        prepared.enter_context(
                            output.prepare(
                                folder,
                                revisions,
                                canvas,
                                composition=part,
                                bake=bake,
                                _tiles=tiles,
                            )
                        )
                    )
                for commit in commits:
                    commit()
        except Exception:
            for name in outputs.keys() - self.outputs.keys():
                outputs[name].close()
            raise
        self.outputs, self._sources = outputs, sources
        return self.revision


class PublishedTransfer(ComposedPicture):
    def __init__(self, store: Path, piece: int = 512):
        pending = store / "pending.json"
        if pending.exists():
            piece = json.loads(pending.read_text(encoding="utf-8")).get("piece", piece)
        piece = (_read_attrs_at(store).get("zmart") or {}).get("piece", piece)
        super().__init__(store, piece)
        self._lock = threading.RLock()
        self._held = None
        self._state = None
        self._state_mark = None

    def composer(self) -> Composer:
        with self._lock:
            if (self._shown / "pending.json").exists():
                with _holding_the_bake_lock(self._shown):
                    if (self._shown / "pending.json").exists():
                        raise RuntimeError("The coarse overview publication needs recovery")
            return self._read_snapshot()

    def _read_snapshot(self) -> Composer:
        with self._lock:
            path = self._shown / "publication.json"
            mark = path.stat().st_mtime_ns
            if mark != self._state_mark:
                state = json.loads(path.read_text(encoding="utf-8"))
                made = Composer(read_the_mosaic_as_written(state["mosaic"]), piece=self._piece)
                previous, self._held = self._held, made
                self._state, self._state_mark = state, mark
                if previous is not None:
                    previous.stop_warming()
            return self._held

    def request_catch_up(self) -> None:
        # Publication already patches coarse chunks before announcing its revision.
        pass

    def close(self) -> None:
        with self._lock:
            if self._held is not None:
                self._held.close()
                self._held = None
                self._state_mark = None

    @property
    def revision(self) -> int:
        return self._state["revision"] if self._state else 0

    @property
    def bake(self) -> bool:
        return (self._state or {}).get("bake", True)

    def publish(
        self,
        folder: Path,
        versions: dict[str, int],
        canvas: dict,
        *,
        composition: dict | None = None,
        bake: bool = True,
    ) -> int:
        """Validate and commit one completed aggregate snapshot."""
        with self.prepare(folder, versions, canvas, composition=composition, bake=bake) as commit:
            return commit()

    @contextmanager
    def prepare(
        self,
        folder: Path,
        versions: dict[str, int],
        canvas: dict,
        *,
        composition: dict | None = None,
        bake: bool = True,
        _tiles=None,
    ):
        """Validate a snapshot, then yield its commit; unchanged snapshots do no pixel I/O."""
        if not isinstance(versions, dict):
            raise ValueError("source_revisions must map completed position names to revisions")
        versions, canvas, composition = dict(versions), deepcopy(canvas), deepcopy(composition)
        if isinstance(composition, dict) and isinstance(composition.get("regions"), dict):
            composition["regions"] = {
                name: canonical_regions(regions) for name, regions in composition["regions"].items()
            }
        if composition is not None and (
            not isinstance(composition, dict)
            or not {"regions", "order"} <= composition.keys()
            or composition.keys()
            - {
                "regions",
                "order",
                "pyramid_reduction",
                "xy_origin",
                "z_references",
                "view",
                "originals",
                "original_revisions",
            }
        ):
            raise ValueError("composition must contain explicit regions and order")
        if not bake and composition is None:
            raise ValueError("An unbaked external aggregate needs explicit acquired composition")
        view = (composition or {}).get("view")
        if view is not None and (
            not isinstance(view, dict)
            or view.get("type") not in ("top", "slice", "projection")
            or not isinstance(view.get("acquisition"), str)
        ):
            raise ValueError("Named views require an acquisition and top, slice or projection type")
        self._shown.mkdir(parents=True, exist_ok=True)
        with self._lock, _holding_the_bake_lock(self._shown):
            pending = self._shown / "pending.json"
            recovering = (
                json.loads(pending.read_text(encoding="utf-8")) if pending.exists() else None
            )
            if (self._shown / "publication.json").exists():
                self._read_snapshot()
            old_versions = (self._state or {}).get("versions", {})
            old_composition = (self._state or {}).get("composition")
            if self._state and view != (old_composition or {}).get("view"):
                raise ValueError("A named view's identity cannot change in place")
            if self._state and (composition or {}).get("originals") != (old_composition or {}).get(
                "originals"
            ):
                raise ValueError("A named view cannot change its original source folder")
            if self._state and (composition is None) != (old_composition is None):
                raise ValueError("The acquisition cannot change its acquired-coverage contract")
            if self._state and (composition or {}).get("xy_origin", "center") != (
                old_composition or {}
            ).get("xy_origin", "center"):
                raise ValueError("The acquisition cannot change its XY coordinate convention")
            if self._state and canvas != self._state["canvas"]:
                raise ValueError("The baked canvas cannot change within an open acquisition")
            if (
                self._state
                and versions == old_versions
                and composition == old_composition
                and bake == self.bake
                and not recovering
            ):
                yield lambda: self.revision
                return
            if not versions:
                raise ValueError("A baked overview needs at least one completed position")
            for name, revision in versions.items():
                if Path(name).name != name or not name.endswith(".ome.zarr"):
                    raise ValueError(
                        "Position names must name OME-Zarr stores directly in the folder"
                    )
                if type(revision) is not int or revision < old_versions.get(name, 0):
                    raise ValueError("Completed position revisions must not regress")
            validate_canvas(canvas)

            previous = self._held
            kept = {tile.name: tile for tile in previous.mosaic.tiles} if previous else {}
            changed = {
                name
                for name in versions.keys() | old_versions.keys()
                if versions.get(name) != old_versions.get(name)
            }
            references = (composition or {}).get("z_references", {})
            old_references = (old_composition or {}).get("z_references", {})
            changed.update(n for n in versions if references.get(n) != old_references.get(n))
            if recovering:
                changed = versions.keys() | old_versions.keys()
            affected = set(changed)
            if composition is not None:
                old_regions = (old_composition or {}).get("regions", {})
                regions, order = composition["regions"], composition["order"]
                if (regions != "complete" and not isinstance(regions, dict)) or not isinstance(
                    order, list
                ):
                    raise ValueError(
                        "Composition regions must be 'complete' or a map, and order a list"
                    )
                if any(not isinstance(name, str) for name in order):
                    raise ValueError("Composition order must contain source names")
                if isinstance(regions, dict) and isinstance(old_regions, dict):
                    affected.update(
                        name
                        for name in versions.keys() | old_versions.keys()
                        if regions.get(name) != old_regions.get(name)
                    )
                elif regions != old_regions:
                    affected.update(versions.keys() | old_versions.keys())
                old_order = (old_composition or {}).get("order", [])
                common = set(old_order) & set(order)
                before = [name for name in old_order if name in common]
                after = [name for name in order if name in common]
                affected.update(a for a, b in zip(before, after) if a != b)
            if bake != self.bake:
                affected.update(versions.keys() | old_versions.keys())
            if (composition or {}).get("pyramid_reduction") != (old_composition or {}).get(
                "pyramid_reduction"
            ):
                affected.update(versions.keys() | old_versions.keys())
            tiles = []
            for name in versions:
                retired = (
                    composition is not None
                    and isinstance(composition["regions"], dict)
                    and composition["regions"].get(name) == []
                    and versions[name] == old_versions.get(name)
                    and references.get(name) == old_references.get(name)
                )
                # Retired sources need only their committed geometry, even during recovery.
                # Re-reading would discard aggregate sampling and relative-Z placement.
                if name not in changed or (retired and name in kept):
                    tiles.append(kept[name])
                    continue
                tile = (_tiles or {}).get(name) or _read_one_tile(folder / name)
                if _read_attrs_at(tile.store)["multiscales"][0].get("type") != "mean":
                    raise ValueError("Coarse baking requires mean-reduced position pyramids")
                if name in references:
                    reference = references[name]
                    if not isinstance(reference, (float, int)) or not math.isfinite(reference):
                        raise ValueError(
                            "Z references must be finite specimen heights in micrometres"
                        )
                    tile = replace(
                        tile,
                        copies=[
                            replace(
                                copy, corner_um=(copy.corner_um[0] - reference, *copy.corner_um[1:])
                            )
                            for copy in tile.copies
                        ],
                    )
                tiles.append(tile)
            if composition is not None:
                if (
                    not view
                    and previous
                    and (tiles[0].copies[0].shape[0] == 1)
                    != (previous.mosaic.tiles[0].copies[0].shape[0] == 1)
                ):
                    raise ValueError("The aggregate cannot change between flat and stack sources")
                tiles, depth_origin, depth_extent = _place_depth(
                    tiles,
                    spacing=previous.mosaic.voxel_um(0)[0] if previous and not view else None,
                    combine=bool(view),
                )
                if previous and not view:
                    held = previous.mosaic
                    if (
                        depth_origin < held.corner_um[0] - 1e-7
                        or depth_origin + depth_extent
                        > held.corner_um[0] + held.extent_um[0] + 1e-7
                    ):
                        raise ValueError(
                            "The published Z domain cannot grow without a geometry refresh"
                        )
                    depth_origin, depth_extent = held.corner_um[0], held.extent_um[0]
            _refuse_tiles_that_disagree(tiles)
            if view and len({tile.time_calibration for tile in tiles}) != 1:
                raise ValueError("View contributors must share their time calibration")
            first = tiles[0]
            if first.keeps < 2 and composition is None:
                raise ValueError(
                    "Coarse baking requires a position pyramid with at least two levels"
                )
            for tile in tiles:
                if tile.turned or tile.axes not in (
                    ("z", "y", "x"),
                    ("c", "z", "y", "x"),
                    ("t", "c", "z", "y", "x"),
                ):
                    raise ValueError(
                        "Baking supports unrotated ZYX, CZYX and TCZYX position stores"
                    )
                if any(
                    copy.outer_shape != base.outer_shape
                    or (
                        composition is None
                        and (copy.shape[0], copy.corner_um[0]) != (base.shape[0], base.corner_um[0])
                    )
                    for copy, base in zip(tile.copies, first.copies, strict=True)
                ):
                    raise ValueError("Positions in a baked acquisition must share C/Z/T geometry")
            attrs = _read_attrs_at(first.store)
            scales = attrs["multiscales"]
            averaged = scales[0].get("type") == "mean"
            base = first.copies[0]
            corner = (
                depth_origin if composition is not None else base.corner_um[0],
                canvas["y_um"][0],
                canvas["x_um"][0],
            )
            extent = (
                depth_extent if composition is not None else base.shape[0] * base.voxel_um[0],
                canvas["y_um"][1] - corner[1],
                canvas["x_um"][1] - corner[2],
            )
            mosaic = Mosaic(
                tiles,
                first.keeps,
                ("z", "y", "x"),
                base.dtype,
                corner_um=corner,
                extent_um=extent,
                averaged=averaged,
                omero=attrs.get("omero"),
                sampling="top" if view and view["type"] == "top" else "slice",
            )
            if composition is not None:
                regions = composition["regions"]
                if regions == "complete":
                    moments, channels = mosaic.frame_room
                    regions = {
                        tile.name: [
                            AcquiredRegion(t, c, (0, 0, 0), tile.copies[0].shape).as_written()
                            for t in range(moments)
                            for c in range(channels)
                        ]
                        for tile in tiles
                    }
                mosaic = mosaic.with_acquired_regions(
                    regions,
                    order=composition["order"],
                    pyramid_reduction=composition.get("pyramid_reduction"),
                    xy_origin=composition.get("xy_origin", "center"),
                )
                while max(mosaic.shape(mosaic.levels - 1)[-2:]) > self._piece:
                    mosaic.levels += 1

            def geometry(of):
                return (
                    of.shape(0),
                    of.frame_room,
                    of.corner_um,
                    of.dtype,
                    of.averaged,
                    tuple(of.voxel_um(level) for level in range(of.levels)),
                    of.tiles[0].axes,
                    of.tiles[0].time_calibration,
                )

            geometry_changed = previous is not None and geometry(mosaic) != geometry(
                previous.mosaic
            )
            if geometry_changed:
                if not view or mosaic.dtype != previous.mosaic.dtype:
                    raise ValueError("The baked source's geometry changed; open a new acquisition")
                affected.update(versions.keys() | old_versions.keys())
            for tile in tiles:
                copy = tile.copies[0]
                if any(
                    copy.corner_um[axis] < corner[axis] - copy.voxel_um[axis] / 2
                    or copy.corner_um[axis] + copy.shape[axis] * copy.voxel_um[axis]
                    > corner[axis] + extent[axis] + copy.voxel_um[axis] / 2
                    for axis in (1, 2)
                ):
                    raise ValueError(f"{tile.name} extends outside the declared specimen canvas")

            def commit():
                made = Composer(mosaic, piece=self._piece)
                self._bake_legacy_reduction = uses_legacy_mean(mosaic.pyramid_reduction)
                if geometry_changed or recovering:
                    self._bake_below.clear()
                    self._bake_staging.clear()
                    self._bake_recipes.clear()
                # A failed publication can touch ground absent from both the old
                # snapshot and the retry (for example, an appended tile withdrawn
                # before retry). Retain those chunks until recovery completes.
                dirty = {
                    int(level): {tuple(chunk) for chunk in chunks}
                    for level, chunks in (recovering or {}).get("dirty", {}).items()
                }
                unbaked_dirty = {
                    int(level): {tuple(chunk) for chunk in chunks}
                    for level, chunks in (self._state or {}).get("unbaked_dirty", {}).items()
                }
                if bake:
                    for level, chunks in unbaked_dirty.items():
                        dirty.setdefault(level, set()).update(chunks)
                for source in (previous, made):
                    if source is None:
                        continue
                    if mosaic.has_acquired_regions:
                        for level in range(mosaic.levels):
                            dirty.setdefault(level, set()).update(
                                cell
                                for cell, tiles_here in source._tiles_in_each_piece(level).items()
                                if any(tile.name in affected for tile, _ in tiles_here)
                            )
                        continue
                    for tile in source.mosaic.tiles:
                        if tile.name not in changed:
                            continue
                        for level in range(mosaic.levels):
                            at = source.mosaic.lands_at(tile, level)
                            size = tile.copies[level].shape
                            deep, rows, cols = made.grid(level)
                            dirty.setdefault(level, set()).update(
                                (row, col)
                                for row in range(
                                    max(0, at[1] // self._piece),
                                    min(rows, (at[1] + size[1] - 1) // self._piece + 1),
                                )
                                for col in range(
                                    max(0, at[2] // self._piece),
                                    min(cols, (at[2] + size[2] - 1) // self._piece + 1),
                                )
                            )
                if previous and not geometry_changed:
                    stale = frozenset(
                        copy.held_in
                        for name, tile in kept.items()
                        if name in changed
                        for copy in tile.copies
                    )
                    made.inherit_the_unchanged(previous, dirty, stale=stale)
                self._shown.mkdir(parents=True, exist_ok=True)
                description = json.loads(made.group_json())
                _atomic_json(
                    pending,
                    {
                        "versions": versions,
                        "view": view,
                        "piece": self._piece,
                        "originals": (composition or {}).get("originals"),
                        "dirty": {str(level): sorted(chunks) for level, chunks in dirty.items()},
                    },
                )
                baked = self._declare_levels(
                    made, description, bake=bake, redeclare=geometry_changed or bool(recovering)
                )
                if bake and previous is not None and mosaic.sampling == "top":
                    self._discard_obsolete_top_chunks(previous, made, dirty, baked)
                moments, channels = mosaic.frame_room
                frames = (
                    [()]
                    if (moments, channels) == (1, 1)
                    else [
                        (moment, channel)
                        for moment in range(moments)
                        for channel in range(channels)
                    ]
                )
                for level in baked:
                    if level >= mosaic.levels:
                        break
                    # Sparse levels share one XY reduction grid. Once the level
                    # below is baked, propagate its changes instead of recomposing
                    # a larger footprint from originals at every ancestor.
                    if (
                        mosaic.has_acquired_regions
                        and level - 1 in baked
                        and mosaic.sampling != "top"
                    ):
                        self._rehalve_one_level(level, sorted(dirty.get(level, ())), frames)
                        continue
                    for row, col in sorted(dirty.get(level, ())):
                        for moment in range(moments):
                            for channel in range(channels):
                                for plane in range(made.grid(level)[0]):
                                    if mosaic.sampling == "top" and plane != made.canonical_plane(
                                        level, plane, row, col, moment, channel
                                    ):
                                        continue
                                    self._replace_one_piece(
                                        made, level, plane, row, col, moment=moment, channel=channel
                                    )
                reached = dirty.get(mosaic.levels - 1, set())
                for level in (one for one in baked if one >= mosaic.levels):
                    reached = {(row // 2, col // 2) for row, col in reached}
                    if reached:
                        self._rehalve_one_level(level, sorted(reached), frames)
                for level, chunks in dirty.items():
                    unbaked_dirty.setdefault(level, set()).update(chunks)
                state = {
                    "revision": self.revision + 1,
                    "geometry_revision": (self._state or {}).get("geometry_revision", 0)
                    + int(geometry_changed),
                    "versions": versions,
                    "canvas": canvas,
                    "composition": composition,
                    "bake": bake,
                    "unbaked_dirty": {}
                    if bake
                    else {str(level): sorted(chunks) for level, chunks in unbaked_dirty.items()},
                    "mosaic": the_mosaic_written_down(mosaic),
                }
                if (
                    not (self._shown / "zarr.json").exists()
                    or bake != self.bake
                    or geometry_changed
                    or recovering
                ):
                    description["attributes"]["zmart"] = {
                        "published_from": str(folder.resolve()),
                        "piece": self._piece,
                        "baked": baked,
                        **({"view": view} if view else {}),
                    }
                    _atomic_json(self._shown / "zarr.json", description)
                _atomic_json(self._shown / "publication.json", state)
                (self._shown / "pending.json").unlink()
                self._state = state
                self._state_mark = (self._shown / "publication.json").stat().st_mtime_ns
                self._held = made
                if previous is not None:
                    previous.stop_warming()
                return self.revision

            yield commit

    def _discard_obsolete_top_chunks(self, previous, made, dirty, baked):
        """A changed clamp range can retire a formerly materialized Z representative."""
        frames, channels = previous.mosaic.frame_room
        for level in baked:
            if level >= previous.mosaic.levels:
                continue
            for row, col in dirty.get(level, ()):
                for moment in range(frames):
                    for channel in range(channels):
                        for plane in range(previous.grid(level)[0]):
                            if (
                                previous.canonical_plane(level, plane, row, col, moment, channel)
                                != plane
                            ):
                                continue
                            if (
                                previous.mosaic.frame_room == made.mosaic.frame_room
                                and level < made.mosaic.levels
                                and plane < made.grid(level)[0]
                                and made.canonical_plane(level, plane, row, col, moment, channel)
                                == plane
                            ):
                                continue
                            outer = (
                                (str(moment), str(channel)) if (frames, channels) != (1, 1) else ()
                            )
                            chunk = self._shown.joinpath(
                                str(level), "c", *outer, str(plane), str(row), str(col)
                            )
                            if chunk.is_file():
                                _after_a_windows_reader(os.unlink, chunk)

    def _declare_levels(
        self, made: Composer, description: dict, *, bake=True, redeclare=False
    ) -> list[int]:
        datasets = description["attributes"]["ome"]["multiscales"][0]["datasets"]
        for level in range(made.mosaic.levels):
            path = self._shown / str(level)
            path.mkdir(exist_ok=True)
            if redeclare or not (path / "zarr.json").exists():
                metadata = json.loads(made.array_json(level))
                if (path / "zarr.json").exists():
                    old = json.loads((path / "zarr.json").read_text(encoding="utf-8"))
                    # Retire chunks outside the new extent as well as keys whose
                    # rank or chunk shape changed meaning.
                    if (
                        old["shape"] != metadata["shape"]
                        or old["chunk_grid"] != metadata["chunk_grid"]
                    ):
                        chunks = path / "c"
                        if chunks.exists():
                            shutil.rmtree(chunks)
                _atomic_json(path / "zarr.json", metadata)
        baked = sorted(level for level in made.pinned_levels if level > 0)
        if not bake:
            return []
        if made.mosaic.has_acquired_regions:
            return sorted(
                set(baked) | set(range(max(1, made.mosaic.tiles[0].keeps - 1), made.mosaic.levels))
            )
        level = made.mosaic.levels - 1
        metadata = json.loads(made.array_json(level))
        while max(metadata["shape"][-2:]) > self._piece:
            metadata["shape"][-2:] = [math.ceil(size / 2) for size in metadata["shape"][-2:]]
            previous = datasets[-1]
            transform = json.loads(json.dumps(previous["coordinateTransformations"]))
            previous_scale = next(part["scale"] for part in transform if part["type"] == "scale")[
                -2:
            ]
            for part in transform:
                if part["type"] == "scale":
                    part["scale"][-2:] = [value * 2 for value in part["scale"][-2:]]
                elif part["type"] == "translation" and made.mosaic.averaged:
                    part["translation"][-2:] = [
                        at + scale / 2
                        for at, scale in zip(part["translation"][-2:], previous_scale, strict=True)
                    ]
            level += 1
            datasets.append({"path": str(level), "coordinateTransformations": transform})
            path = self._shown / str(level)
            path.mkdir(exist_ok=True)
            if not (path / "zarr.json").exists():
                _atomic_json(path / "zarr.json", metadata)
            baked.append(level)
        return baked
