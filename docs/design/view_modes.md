# Viewer modes: Top, Slice, Projection and 3D

Date: 2026-09-09
Target: **0.3.0** for Top, Slice and Projection; 3D rendering is deferred.
Status: Top, Slice and Min/Max/Sum implemented on the isolated 0.3.0 viewer branch.
See [usage and limitations](../view_modes.md). The 2026-09-10 clarification below
supersedes the original focus-relative placement. 3D remains deferred.

## The four modes

| Mode | Behavior |
| --- | --- |
| **Top view** | Select a relative-Z plane within each stack. Outside that stack's depth range, hold its nearest boundary plane. A single-plane image consequently stays visible at its X/Y position. |
| **Slice view** | Show only data intersecting the selected Z plane. Outside a source's depth range it contributes nothing, including a single-plane image. |
| **Projection** | Compute **Min, Max or Sum** over all acquired Z planes of each position independently, per channel and time point. Single-plane images remain unchanged. |
| **3D view** | Show image planes and volumes positioned in three-dimensional space. Detailed rendering controls remain to be designed. |

Top view is a hybrid top-down view, **not** a projection through the stack.
Slice view is the preferred name instead of "2D slicer".

## Availability and default

The standalone viewer defaults to **Slice** when it is available. Support any
nonempty subset of Top, Slice and Projection; opening a dataset must not require
all three. If Slice is absent, select an available view rather than showing an
empty Slice view. The dropdown offers only available views and projection
methods. Opening a dataset must not silently generate missing views.

This standalone default does not change the operator's agreed Top default.

## Top view: hold boundary planes

The final clarification replaces the earlier proposal that stacks disappear
outside their depth range while only single images stay visible. Use one rule
for both single images and stacks:

- Within a stack's depth range, show the plane selected by Z.
- Below its lowest plane, keep showing the bottom plane.
- Above its highest plane, keep showing the top plane.
- A single-plane image has identical lower and upper bounds, so it always shows
  its only plane.

Conceptually, sample at `clamp(selectedZ, stackLowestZ, stackHighestZ)`, expressed
in the chosen display coordinates. This describes boundary behavior, not a new
choice of interpolation or nearest-plane sampling inside a stack.

This must use **each contributing stack's range**, not just the overall bounds
of the combined acquisition. Merely moving every flat image to Z = 0 would not
keep it visible when the selected Z changes.

Holding a boundary plane does not fill unacquired holes within that plane or
invent data at missing interior planes. Authoritative coverage still applies.
This is opt-in Top sampling, not a change to ordinary Slice composition or its
plane-identity guarantees. Existing browser flat persistence is an implementation
detail to make mode-specific, not evidence that stack boundary holding exists.

## Z placement

Clarified 2026-09-10: **Top counts planes from a common floor**, not micrometres
from a focus reference. Plane 1 is each stack's lowest plane. Unequal plane counts
and unequal Z step sizes are allowed; the range is the largest plane count.
Shorter stacks hold their final plane and single images remain visible.

**Slice uses absolute specimen Z**, including single images. Its common grid uses
the smallest native Z step and nearest-plane sampling. Origin quantization is
bounded by half that step. Pixels and authoritative coverage share that sampler.
The operator switches the whole canvas between these coordinate meanings.

Display placement must not rewrite original acquisition coordinates. Preserve
the separate original OME-Zarr files and their provenance. Existing writer
conventions need to be inspected before implementing mode switching; this is
the desired separation of responsibilities, not a statement about every current
file's metadata.
Old products must be republished to adopt the versioned placement recipe; original
specimen coordinates are never edited to implement a display mode.

## Projection and coverage

Scope clarification, 2026-09-09: only **Min, Max and Sum**, per position over
all acquired Z planes. No Focus preset or focus-window projection. Earlier
selected-range controls are not part of this initial implementation.

- **Max:** largest acquired value along the position's Z stack.
- **Min:** smallest acquired value along that stack.
- **Sum:** accumulated acquired signal along that stack. Accumulate in a
  sufficiently wide numeric type to avoid overflow; apply display contrast to
  the result rather than clipping the sum to the original pixel type.
  Use one stable saved dtype per aggregate. If an update cannot be represented
  under the declared limits, report it and retain the last valid publication;
  never silently saturate or promote just one contributor.
- Keep channels separate, and project Z at the selected time point rather than
  combining time points.
- Project each original position first, then aggregate the projected positions
  for display. Do not project the stitched acquisition volume.
- Exclude missing data from all reductions. Genuinely acquired black pixels are
  valid zeros and must participate, especially in Min.
- Where there are no acquired contributors, output remains transparent. Covered
  black output remains opaque. Do not repeat held boundary planes as extra
  contributions to a projection.
- Acquired opacity does not depend on whether the page background is transparent.
  Test both settings, including black projected pixels over another image.

## Ownership and implementation constraints

These are shared viewer behaviors, not overview/focus/target workflow exceptions.
Keep aggregation mandatory and coarse baking optional, with bounded engine
source count rather than one Neuroglancer source per position. Fine reads retain
the separate originals; baking must not change viewing semantics or coverage.

Before implementing a toggle, inspect whether the aggregate retains the
per-position reference heights and depth ranges required for these behaviors.
Switching faithfully is not necessarily a simple engine flag, particularly if
physical heights have already been collapsed into a flat display aggregate.

Keep the existing asynchronous publication and change-driven refresh contract:
idle polling does not invalidate data; each actual change has one effective
refresh path. This note introduces no new polling or notification system.

## Future verification

Use a single image alongside stacks with unequal depth ranges, reference
heights, plane spacing and arrival order. Assert pixels below, inside and above
each range: Top holds boundary planes, Slice does not. Check single images,
transparent gaps, acquired black pixels, and per-channel projection results at
multiple time points. Verify bake on/off and fine/coarse transitions agree,
source count stays bounded, and originals stay unchanged. Automated scaling
remains capped at 100 positions.

Test all seven nonempty view combinations: Slice is initially selected whenever
present; otherwise the selection is an available view. Include projection-only
datasets with just one reduction method.

No implementation or test execution is included in this documentation change.
