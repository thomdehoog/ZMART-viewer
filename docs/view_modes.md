# Named acquisition views in 0.3.0

One renderer displays the available views of an acquisition. The dropdown defaults
to **Slice**, then Top or a projection if Slice is absent. Opening saved data never
generates a missing view. Different acquisitions can select different views.

| View | Z behavior |
| --- | --- |
| Slice | Show only acquired data intersecting the selected relative-Z plane. |
| Top | Sample each contributor independently; hold its nearest boundary plane outside its range. |
| Min / Max / Sum | Reduce all acquired Z samples of each original position, independently for each T/C, before aggregating positions. |

Top is not a projection. Interior holes stay missing. Single-plane images lie on
display Z=0: Slice intersects that plane, while Top holds it across the view's Z
range. Stack reference heights map to display Z=0; internal spacing is retained.
Neither mode changes the original OME-Zarr coordinates. Absolute-Z placement and
new 3D controls are not included. Existing legacy-volume behavior is unchanged.
On a mixed page, 3D displays legacy datasets only; returning to 2D restores the
selected named views. Top retains each aggregate's boundary plane even when
another acquisition extends the shared Z slider, using local sampling rather
than repeated volumes or publication on slider movements.

For sparse positions, "boundary" means the declared array boundary, not the
first/last acquired pixel. A missing declared boundary plane remains transparent
when held; Top does not fill holes or infer acquired depth from intensity.

## Publish from separate originals

```python
from pathlib import Path
from zmart_viewer.views import ViewSet

views = ViewSet(
    Path("run/view"),
    acquisition="targets",
    modes=("slice", "top"),
    projections=("min", "max", "sum"),
    projection_folder=Path("run/targets/projections"),
)
try:
    views.publish(
        Path("run/positions/targets"),
        {"p001.ome.zarr": 1, "p002.ome.zarr": 1},
        {"x_um": [0, 2000], "y_um": [0, 1000]},
        composition={
            "regions": "complete",
            "order": ["p001.ome.zarr", "p002.ome.zarr"],
            "z_references": {"p001.ome.zarr": 120.0, "p002.ome.zarr": 125.0},
        },
        bake=True,
    )
finally:
    views.close()
```

Bounds describe the full canvas in specimen micrometres, not the current acquired
bounding box. Later entries in `order` cover earlier entries wherever acquired,
including black pixels. Revisions identify completed, readable writes; increment
the affected position's revision after rewriting it. Re-announcing identical
revisions and composition does no work.
Original revision high-water marks survive removal and reopening: re-adding a
position cannot roll its projection back to an older revision. Region-list order
and duplicate identical regions do not constitute a change; source `order` does.

`z_references` is explicit when supplied. Otherwise the viewer uses the recorded
requested focus reference in acquisition provenance, or the source's first-plane
origin. It does not infer a focus plane from brightness. Inputs must satisfy the
existing unrotated, aligned positive-Z-spacing geometry contract. Normalize raw
instrument plane order upstream; this API is not a Leica raw-file importer.
References must produce offsets on the shared voxel lattice; off-lattice focus
references are rejected, never silently snapped.

Use `regions: "complete"` only when the entire declared position is acquired.
For sparse producers, supply a map from each position name to acquired regions:

```python
{"p001.ome.zarr": [
    {"frame": 0, "channel": 0,
     "origin": {"z": 0, "y": 16, "x": 32},
     "shape": {"z": 3, "y": 64, "x": 64}}
]}
```

These are source-voxel indices and authoritative acquisition information. Omitted
Zarr fill chunks and zero-valued pixels cannot establish coverage. A later revision
may add coverage to a gap. Both signal and coverage use the same sampling rule;
covered black is opaque even for Min and on an opaque page background.

## Files, bakes and numerical limits

```text
run/view/
  targets_slice.zmartview.zarr/
  targets_top.zmartview.zarr/
  targets_min.zmartview.zarr/
  targets_max.zmartview.zarr/
  targets_sum.zmartview.zarr/
run/targets/projections/
  min/p001_r<revision-digest>.ome.zarr/
  max/p001_r<revision-digest>.ome.zarr/
  sum/p001_r<revision-digest>.ome.zarr/
```

Every named view owns its own coarse bake. Fine chunks read the separate originals
or separate projected positions; no full-resolution stitched mosaic is copied.
`bake=False` changes coarse serving to on-demand composition, not source count or
coverage. Top stores canonical constant-Z runs and resolves their logical chunk
addresses through the viewer service. A `.zmartview.zarr` is a viewer-served virtual
image, not a fully materialized OME-Zarr to read directly with generic Zarr tools.
Originals and per-position projection OME-Zarrs remain independently readable.

Projections save numeric TCZYX arrays with singleton Z, XY pyramids, channel/time
calibration, contributing-Z provenance and coverage. Inputs support integer and
floating types up to 32 bits. Min/Max preserve integer type; float outputs are
float32. Sum uses uint32, int32 or float32 respectively, with 64-bit accumulation.
An out-of-range or nonfinite result is rejected; it never wraps or saturates.
Display contrast is measured from projection values, not capped at 65535.
Integer XY means round to nearest even; floating means retain fractional values.
Named views default to `pyramid_reduction: "mean-xy2-edge-from-originals"`:
coarse means are computed from original signal unless the producer explicitly
certifies a supported input-pyramid recipe. This avoids treating a rounded float
input pyramid as fractional data. Older unversioned compositions retain their
historical rounding; all baking and on-demand paths share that decision.

Revision-named projection products are immutable so a failed update cannot change
the old published image. Previous products are deliberately retained; automatic
garbage collection is not implemented. Do not delete products referenced by a
saved publication. Store paths are local references; moving the complete dataset
requires a separate relocation workflow, not editing just the view filename.

## Live HTTP use

`POST /api/stores/open` accepts the normal `path`, `source_revisions`, `canvas`,
`composition` and `bake` fields plus:

```json
{"views": {"path": "run/view", "acquisition": "targets",
           "modes": ["slice", "top"], "projections": ["min", "max", "sum"],
           "projection_path": "run/targets/projections"}}
```

Announce updates through the existing `POST /api/announce` publications list,
using the original folder's path, new revision map and composition. Do not proxy
image chunks through the producer: the renderer reads the viewer HTTP service.
Multiple acquisitions may publish into the same `run/view` folder. Each owns its
named outputs and options; announcing one original folder does not update another.
Named acquisitions have separate panel/close identities even at different
objectives or Z spacings. Geometry never determines their identity; legacy
datasets retain their existing geometry-based grouping. Within one destination,
an open original folder has one acquisition owner, so a misspelled second name
is refused. Independent destination folders remain allowed.

Publication performs work synchronously at this API boundary. Call it from the
producer's existing asynchronous/coalesced publication worker, not an acquisition
callback. No additional publisher queue was introduced. Config/status reads do
not start projection or baking work or wait for publication. Identical announcements
and idle checks do not invalidate image caches. A committed change follows one
whole-source refresh path; geometry changes also update metadata. Transient
metadata failures retry without needing another acquisition event.

Commit/recovery is per view, not atomic across every view in an acquisition.
Preflight/overflow failure preserves the old publication. An I/O failure during
later commits may leave earlier views advanced; the server announces those
successes, refuses reads of pending views and allows retry. Saved chunk geometry
and pending recovery state are authoritative when reopening.
Original revision history is read from committed publications under an
acquisition-scoped lock, including commits from another handle or an interrupted
update. Old previews without that history can still be opened for reading, but
further live publication requires a new view folder; no missing history is guessed.
Automatic external-folder publication runs in the existing folder watcher, not
in `/api/config`. A failing automatic publisher is logged without blocking others.

## Build and open

Build the frontend before making a wheel:

```sh
npm ci --prefix app/page
npm run build --prefix app/page
python -m pip wheel . --no-deps --wheel-dir dist
```

The successful frontend build records input/output hashes. Wheel creation rejects
missing, changed or incomplete build output, including changed public assets.
Each wheel uses fresh temporary staging for the whole package, so neither retired
Python modules nor hashed bundles can survive a subsequent build. Existing build
directories are not deleted.

The wheel includes the page and Neuroglancer workers. To serve saved views from an
installed package:

```python
from pathlib import Path
from zmart_viewer.server import make_server

server = make_server(port=8848, data_dir=Path("run/view"), live=False,
                     loads=[{"path": "run/view"}])
try:
    server.serve_forever()
finally:
    server.server_close()
```

Open `http://127.0.0.1:8848` in a browser. A folder containing any subset of named
views, or one individual named view, can be opened. The 0.3.0 feature branch does
not update operator pins, deploy to the rig, create a release tag or merge main.
