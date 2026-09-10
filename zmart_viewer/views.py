"""Named acquisition views over separate original positions and shared publication."""

from __future__ import annotations

import json
import threading
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from .compose import MEAN_REDUCTION, _read_one_tile
from .library import _read_attrs_at
from .projections import METHODS, write_projection
from .published import PublishedTransfer


def view_metadata(store):
    return (_read_attrs_at(Path(store)).get("zmart") or {}).get("view")


def view_key(metadata):
    return metadata.get("method") if metadata["type"] == "projection" else metadata["type"]


class ViewSet:
    """At most five named sources; the number does not depend on position count."""

    def __init__(
        self,
        folder,
        *,
        acquisition,
        modes=("slice", "top"),
        projections=(),
        projection_folder=None,
        piece=512,
    ):
        if not acquisition or Path(acquisition).name != acquisition or acquisition in (".", ".."):
            raise ValueError("Acquisition must be a plain nonempty name")
        if any(mode not in ("top", "slice") for mode in modes) or any(
            p not in METHODS for p in projections
        ):
            raise ValueError("Views support Top, Slice and Min/Max/Sum projections")
        keys = list(dict.fromkeys([*modes, *projections]))
        if not keys:
            raise ValueError("Choose at least one view")
        if projections and projection_folder is None:
            raise ValueError("Saved projections need an explicit output folder")
        self.folder = Path(folder).resolve()
        self.projection_folder = Path(projection_folder).resolve() if projection_folder else None
        self.acquisition = acquisition
        self.modes, self.projections = frozenset(modes), frozenset(projections)
        self._lock = threading.RLock()
        self.outputs = {
            f"{acquisition}_{key}.zmartview.zarr": PublishedTransfer(
                self.folder / f"{acquisition}_{key}.zmartview.zarr", piece
            )
            for key in keys
        }
        self.keys = keys
        self.source_folder = None
        self._references = {}
        for key, output in zip(keys, self.outputs.values()):
            pending_path = output._shown / "pending.json"
            pending = (
                json.loads(pending_path.read_text(encoding="utf-8"))
                if pending_path.exists()
                else {}
            )
            identity = view_metadata(output._shown) or pending.get("view")
            if output._shown.exists() and identity != self._identity(key):
                if any(path.name != ".bake.lock" for path in output._shown.iterdir()):
                    raise ValueError(
                        f"View output is not owned by this acquisition: {output._shown}"
                    )
            snapshot = output._shown / "publication.json"
            original = pending.get("originals")
            if snapshot.exists():
                output._read_snapshot()
                original = output._state["composition"]["originals"]
            if original is not None:
                original = Path(original).resolve()
                if self.source_folder is not None and self.source_folder != original:
                    raise ValueError("Saved views disagree about their original source folder")
                self.source_folder = original

    def _identity(self, key):
        return {
            "acquisition": self.acquisition,
            "type": "projection" if key in METHODS else key,
            **({"method": key} if key in METHODS else {}),
        }

    @property
    def sources(self):
        return list(self.outputs)

    @property
    def revision(self):
        return sum(output.revision for output in self.outputs.values())

    @property
    def bake(self):
        return next(iter(self.outputs.values())).bake

    def close(self):
        with self._lock:
            for output in self.outputs.values():
                output.close()

    def publish(self, folder, versions, canvas, *, composition, bake=True):
        """Serialize producer snapshots; config/status reads never acquire this lock."""
        with self._lock:
            return self._publish(folder, versions, canvas, composition=composition, bake=bake)

    def _publish(self, folder, versions, canvas, *, composition, bake):
        folder = Path(folder).resolve()
        if self.source_folder is not None and self.source_folder != folder:
            raise ValueError("A view set cannot change its original source folder")
        if not isinstance(composition, dict) or not {"regions", "order"} <= composition.keys():
            raise ValueError("Named views require authoritative coverage and source order")
        if set(composition["order"]) != set(versions) or len(composition["order"]) != len(versions):
            raise ValueError("View order must name every completed position once")
        if any(Path(name).name != name or not name.endswith(".ome.zarr") for name in versions):
            raise ValueError("View inputs must be separate position OME-Zarr stores")
        self.source_folder = folder
        references = dict(composition.get("z_references", {}))
        for name in versions:
            if name not in references:
                cached = self._references.get(name)
                if cached is None or cached[0] != versions[name]:
                    attrs = _read_attrs_at(folder / name)
                    model = attrs.get("zmart_microscopy", {}).get("z_coordinate", {})
                    reference = model.get("acquisition_provenance", {}).get(
                        "requested_stage_focus_z_um"
                    )
                    reference = (
                        reference
                        if reference is not None
                        else _read_one_tile(folder / name).copies[0].corner_um[0]
                    )
                    cached = self._references[name] = (versions[name], reference)
                references[name] = cached[1]
        prepared = []
        for key, output in zip(self.keys, self.outputs.values()):
            snapshot = deepcopy(composition)
            snapshot["originals"] = str(folder)
            snapshot["z_references"] = references
            snapshot["view"] = self._identity(key)
            source = self.source_folder
            projected_versions = versions
            if key in METHODS:
                source = self.projection_folder / key
                coverage, projected_versions, names = {}, {}, {}
                for name, revision in versions.items():
                    regions = snapshot["regions"]
                    regions = regions if regions == "complete" else regions[name]
                    recipe = json.dumps([str(folder / name), revision, regions], sort_keys=True)
                    suffix = sha256(recipe.encode()).hexdigest()[:20]
                    derived = names[name] = f"{name.removesuffix('.ome.zarr')}_r{suffix}.ome.zarr"
                    store = write_projection(
                        folder / name, source / derived, key, revision=revision, regions=regions
                    )
                    coverage[derived] = _read_attrs_at(store)["zmart_projection"]["regions"]
                    projected_versions[derived] = revision
                snapshot["regions"] = coverage
                snapshot["order"] = [names[name] for name in composition["order"]]
                snapshot["z_references"] = dict.fromkeys(projected_versions, 0)
                snapshot["pyramid_reduction"] = MEAN_REDUCTION
                snapshot["xy_origin"] = "center"
            prepared.append((output, source, projected_versions, snapshot))
        # Validate all views before advancing any publication. Projection inputs
        # are immutable, so a rejected product cannot change the previous picture.
        with ExitStack() as stack:
            commits = [
                stack.enter_context(
                    output.prepare(source, revisions, canvas, composition=snapshot, bake=bake)
                )
                for output, source, revisions, snapshot in prepared
            ]
            for commit in commits:
                commit()
        return self.revision
