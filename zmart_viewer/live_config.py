"""Adapt a live run to the frontend's vocabulary.

The browser is handed the run's governed baked picture as the one live
source — the linked view stays an end-of-run product — with state
documents naming each source's URL and revisions. Imports go one way:
this package may import zmart_live, never the reverse.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from zmart_live.gateway import live_run_holding
from zmart_live.live_state import LiveStateSnapshot, LiveStateTracker
from zmart_live.model import ZmartLiveError
from zmart_live.omezarr import the_channels_described

from .contrast import intensity_histogram
from .declare import declare_a_governed_picture, the_scene_folder_name
from .stores import described_channels, zarr_scheme

LIVE_STATE_SET_SCHEMA = "zmart-live-frontend-state-set/1"

LIVE_PICTURE = "views/live/" + (
    the_scene_folder_name("picture") if the_scene_folder_name else "picture.zmartview.zarr"
)

_DECLARE_RETRY_S = 2.0


def the_live_picture_declared(run_root: Path, *, bake: bool = False) -> Path:
    """The governed picture this run is served by, declared if needed."""
    store = run_root / LIVE_PICTURE
    grown = _the_run_is_grown(run_root)
    if _already_this_runs_picture(store, run_root, grown, bake):
        return store
    began = time.perf_counter()
    made = declare_a_governed_picture(
        run_root / "views" / "live", run_root, name="picture", bake=bake
    )
    print(
        f"declared the {'grown, ' if grown else ''}"
        f"{'baked' if bake else 'unbaked'} live picture "
        f"{made} in {time.perf_counter() - began:.1f} s",
        flush=True,
    )
    return made


def _the_run_is_grown(run_root: Path) -> bool:
    """Whether this run's picture carries the (t, c) axes."""
    from zmart_live.gateway import _LiveRun

    profile = _LiveRun(run_root)._geometry()[1]
    if profile.timepoints > 1 or len(profile.channels) > 1:
        return True
    collection = run_root / "data" / "survey.ome.zarr"
    for member in sorted(collection.glob("*/0/zarr.json")):
        try:
            shape = json.loads(member.read_text(encoding="utf-8")).get("shape")
        except (OSError, ValueError):
            continue
        if shape and len(shape) == 5:
            return shape[0] > 1 or shape[1] > 1
    return False


def _already_this_runs_picture(store: Path, run_root: Path, grown: bool, bake: bool) -> bool:
    """Whether the store already is this run's picture, in the shape asked for."""
    try:
        described = json.loads((store / "zarr.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    ours = (described.get("attributes") or {}).get("zmart") or {}
    governs = ours.get("governed_from")
    if not governs:
        return False
    try:
        if Path(governs).resolve() != run_root.resolve():
            return False
    except OSError:
        return False
    axes = [
        axis.get("name")
        for axis in ((described.get("attributes") or {}).get("ome") or {})
        .get("multiscales", [{}])[0]
        .get("axes", [])
    ]
    right_shape = axes[:2] == ["t", "c"] if grown else axes[:1] == ["z"]
    return right_shape and (not bake or (store / "baked.json").is_file())


@dataclass(frozen=True)
class LiveBinding:
    """One manifest run and the library root through which it is served."""

    tracker: LiveStateTracker
    dataset_number: int
    dataset_root: Path
    group: str = ""

    @property
    def run_root(self) -> Path:
        return self.tracker.run_root

    def _source_store(self, relative_url: str) -> Path:
        return (self.run_root / relative_url).resolve()

    def source_url(self, relative_url: str) -> str:
        """A stable browser URL, relative to the opened library root."""
        store = self._source_store(relative_url)
        try:
            inside = store.relative_to(self.dataset_root)
        except ValueError as why:
            raise ValueError(
                f"The compiled live source {store} is outside the opened folder "
                f"{self.dataset_root}. Open the run root or its views folder."
            ) from why
        return f"/data/{self.dataset_number}/{inside.as_posix()}/|{zarr_scheme(store)}:"

    def state_json(self, snapshot: LiveStateSnapshot | None = None) -> dict:
        """The run's frontend state, naming the source actually served."""
        answer = (snapshot or self.tracker.snapshot()).to_json()
        for source in answer.get("sources", ()):
            if source.get("role") == "linked":
                source["url"] = LIVE_PICTURE
        answer["dataset"] = self.dataset_number
        return answer


class LiveRegistry:
    """Track manifest-governed datasets as the ordinary library opens/closes."""

    def __init__(self, library, wants_the_bake=None) -> None:
        self._library = library
        self._wants_the_bake = wants_the_bake or (lambda run_root: False)
        self._lock = threading.RLock()
        self._trackers_by_root: dict[Path, LiveStateTracker] = {}
        self._pictures: dict[Path, Path] = {}
        self._picture_refused: dict[Path, tuple[float, str]] = {}
        self._bindings: tuple[LiveBinding, ...] = ()
        self._dataset_numbers: frozenset[int] = frozenset()
        self._errors: dict[Path, str] = {}

    def refresh(self) -> tuple[tuple[LiveBinding, ...], frozenset[int]]:
        with self._lock:
            datasets = tuple(self._library.datasets())
            governed: set[int] = set()
            bindings: list[LiveBinding] = []
            used_roots: set[Path] = set()
            errors: dict[Path, str] = {}
            for dataset in datasets:
                run_root = live_run_holding(dataset.root)
                if run_root is None:
                    continue
                picture = (run_root / LIVE_PICTURE).resolve()
                opened = Path(dataset.root).resolve()
                if not (opened == picture or opened in picture.parents):
                    continue
                governed.add(dataset.number)
                dataset.watch = False
                tracker = self._trackers_by_root.get(run_root)
                if tracker is None:
                    try:
                        tracker = LiveStateTracker(run_root)
                    except (OSError, ValueError, ZmartLiveError) as why:
                        # Never let a recognizable but damaged manifest fall back
                        # to folder scanning. A later refresh retries strict open.
                        errors[run_root] = str(why)
                        continue
                    self._trackers_by_root[run_root] = tracker
                used_roots.add(run_root)
                refused = self._without_a_picture(run_root)
                if refused is not None:
                    errors[run_root] = refused
                    continue
                bindings.append(
                    LiveBinding(
                        tracker=tracker,
                        dataset_number=dataset.number,
                        dataset_root=dataset.root,
                    )
                )
            self._trackers_by_root = {
                root: tracker
                for root, tracker in self._trackers_by_root.items()
                if root in used_roots
            }
            self._pictures = {
                root: store for root, store in self._pictures.items() if root in used_roots
            }
            self._picture_refused = {
                root: held for root, held in self._picture_refused.items() if root in used_roots
            }
            self._bindings = tuple(bindings)
            self._dataset_numbers = frozenset(governed)
            self._errors = errors
            return self._bindings, self._dataset_numbers

    def _without_a_picture(self, run_root: Path) -> str | None:
        """Why this run has no served picture right now, or ``None`` once it has."""
        if run_root in self._pictures:
            return None
        stumbled = self._picture_refused.get(run_root)
        if stumbled is not None:
            if time.monotonic() - stumbled[0] < _DECLARE_RETRY_S:
                return stumbled[1]
            del self._picture_refused[run_root]
        try:
            made = the_live_picture_declared(run_root, bake=self._wants_the_bake(run_root))
        except Exception as why:  # noqa: BLE001 - reported and retried, never hidden
            self._picture_refused[run_root] = (time.monotonic(), str(why))
            return str(why)
        self._pictures[run_root] = made
        return None

    def bindings(self) -> tuple[LiveBinding, ...]:
        return self.refresh()[0]

    def dataset_numbers(self) -> frozenset[int]:
        return self.refresh()[1]

    def trackers(self) -> tuple[LiveStateTracker, ...]:
        bindings = self.bindings()
        return tuple(dict.fromkeys(binding.tracker for binding in bindings))

    @property
    def errors(self) -> dict[Path, str]:
        with self._lock:
            return dict(self._errors)


def capture_live_state(
    bindings: tuple[LiveBinding, ...],
) -> tuple[dict, dict[int, LiveStateSnapshot]]:
    """Observe and pin one immutable state/scene pair for every binding."""
    snapshots: dict[int, LiveStateSnapshot] = {}
    by_tracker: dict[LiveStateTracker, LiveStateSnapshot] = {}
    for binding in bindings:
        snapshot = by_tracker.get(binding.tracker)
        if snapshot is None:
            binding.tracker.observe()
            snapshot = by_tracker[binding.tracker] = binding.tracker.snapshot()
        snapshots[binding.dataset_number] = snapshot
    return live_state_document(bindings, snapshots=snapshots), snapshots


def live_state_document(
    bindings: tuple[LiveBinding, ...],
    *,
    snapshots: dict[int, LiveStateSnapshot] | None = None,
) -> dict:
    """Current authoritative state for all open manifest-driven runs."""
    if snapshots is None:
        snapshots = {}
        by_tracker: dict[LiveStateTracker, LiveStateSnapshot] = {}
        for binding in bindings:
            snapshot = by_tracker.get(binding.tracker)
            if snapshot is None:
                binding.tracker.observe()
                snapshot = by_tracker[binding.tracker] = binding.tracker.snapshot()
            snapshots[binding.dataset_number] = snapshot
    return {
        "schema": LIVE_STATE_SET_SCHEMA,
        "runs": [binding.state_json(snapshots[binding.dataset_number]) for binding in bindings],
    }


def the_runs_channels(run_root: Path) -> list[dict]:
    """What this run says about its colours: their names, tints and windows."""
    from zmart_live.gateway import _LiveRun

    profile = _LiveRun(run_root)._geometry()[1]
    return described_channels(
        the_channels_described(profile.channels, profile.dtype),
        len(profile.channels),
    )


def _display_for(described: list[dict], store: Path, channel_index: int, chosen_window) -> dict:
    channel = described[channel_index] if channel_index < len(described) else {}
    window = (
        {"low": float(chosen_window[0]), "high": float(chosen_window[1])}
        if chosen_window is not None
        else channel.get("window")
    )
    return {
        "color": list(channel["color"]) if channel.get("color") else None,
        "window": window,
        "volumeWindow": window,
        "histogram": intensity_histogram(store, channel=channel_index),
    }


def _the_url_served_for(source) -> str:
    """The run-relative store a compiled source is actually served from."""
    return LIVE_PICTURE if source.role == "linked" else source.url


def live_rows(
    binding: LiveBinding,
    *,
    chosen_window=None,
    group: str | None = None,
    snapshot: LiveStateSnapshot | None = None,
) -> list[dict]:
    """Rows for one compiled scene, bounded by views and channels."""
    snapshot = snapshot or binding.tracker.snapshot()
    scene = snapshot.scene
    if scene is None:
        return []
    frontend_sources = {source.source_id: source for source in snapshot.state.sources}
    compiled_sources = {source.source_id: source for source in scene.sources}
    described = the_runs_channels(binding.run_root)
    rows = []
    for layer in scene.layers:
        source_states = [frontend_sources[source_id] for source_id in layer.source_ids]
        sources = [compiled_sources[source_id] for source_id in layer.source_ids]
        urls = [binding.source_url(_the_url_served_for(source)) for source in sources]
        first_store = binding._source_store(_the_url_served_for(sources[0]))
        display = _display_for(described, first_store, layer.channel_index, chosen_window)
        available = [
            {"start": start, "stop": stop} for start, stop in source_states[0].committed_time_ranges
        ]
        contiguous_frames = (
            available[0]["stop"] if len(available) == 1 and available[0]["start"] == 0 else 0
        )
        rows.append(
            {
                "name": layer.name,
                "group": binding.group if group is None else group,
                "kind": layer.kind,
                "channelIndex": layer.channel_index,
                "localPosition": [layer.channel_index],
                "sources": urls,
                "sourceIds": [
                    f"{binding.dataset_number}/{snapshot.state.run_id}/{source.source_id}"
                    for source in source_states
                ],
                "sourceRevisions": [source.revision for source in source_states],
                "committedTimeRanges": available,
                "frames": contiguous_frames,
                "liveRunId": snapshot.state.run_id,
                **display,
            }
        )
    return rows
