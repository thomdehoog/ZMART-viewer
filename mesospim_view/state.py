"""Turning a few placed stores into a neuroglancer state.

Everything here is a pure function from plain data to the JSON neuroglancer
already understands: a layer is a neuroglancer layer, a source is a neuroglancer
source with a ``transform``, and the channels of a store mix inside one shader
through neuroglancer's own channel dimension (``c^``) and ``invlerp(channel=...)``
controls. Nothing is invented that the engine does not have a word for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .omezarr import Channel, Store

# False colours for channels the store does not colour itself. One channel is
# white; several take turns around a palette that reads well when overlaid.
PALETTE = ("#00ff66", "#ff33ff", "#33ccff", "#ffbf1a", "#ff4d4d", "#a0a0ff", "#ffffff")

LAYOUTS = ("xy", "yz", "xz", "4panel", "3d", "xy-3d", "yz-3d", "xz-3d")


@dataclass(frozen=True)
class Placement:
    """One store inside a layer, and where it goes.

    ``offset`` shifts the store from where its own metadata puts it; ``origin``
    places the store's first voxel at an absolute coordinate instead, ignoring
    the metadata's translation. Both are keyed by axis name and measured in the
    axis's own unit as written in the store (micrometres for a mesoSPIM tile).
    """

    store: Store
    url: str
    offset: dict[str, float] = field(default_factory=dict)
    origin: dict[str, float] | None = None

    def shift_voxels(self, index: int) -> float:
        axis = self.store.axes[index]
        shift = self.offset.get(axis.name, 0.0)
        if self.origin is not None and axis.name in self.origin:
            shift += self.origin[axis.name] - self.store.translation[index]
        return shift / self.store.scale[index]


def source_json(placement: Placement) -> dict:
    """A neuroglancer data source: the address, and a transform when one is needed.

    The transform does two native things at once. It renames the store's channel
    axis to a *channel dimension* (``c^``), which is what lets one shader read
    every channel, and it carries any shift as the translation column of the
    matrix, in voxels of the output space. The output space repeats the store's
    own scales in SI, so nothing is stretched.
    """
    store = placement.store
    channel = store.channel_axis
    shifts = [placement.shift_voxels(i) for i in range(len(store.axes))]
    rank = len(store.axes)
    output: dict[str, list] = {}
    for i, axis in enumerate(store.axes):
        if i == channel:
            output[f"{axis.name}^"] = [1, ""]
            continue
        unit, factor = axis.si
        output[axis.name] = [store.scale[i] * factor if unit else 1, unit]
    matrix = [[1.0 if r == c else 0.0 for c in range(rank)] + [shifts[r]] for r in range(rank)]
    return {"url": placement.url, "transform": {"outputDimensions": output, "matrix": matrix}}


def channels_for(store: Store, override: list[Channel] | None = None) -> list[Channel]:
    """The channels a layer shows, one per index along the store's channel axis.

    What the store declares wins; what it leaves out is filled in with a label,
    a colour from the palette and no window, so the engine's own default range
    applies until somebody sets one.
    """
    count = store.channel_count
    declared = list(override if override is not None else store.channels)[:count]
    filled = []
    for index in range(count):
        given = declared[index] if index < len(declared) else Channel(label=f"channel {index}")
        color = given.color or (PALETTE[-1] if count == 1 else PALETTE[index % (len(PALETTE) - 1)])
        filled.append(
            Channel(
                label=given.label,
                color=color,
                window=given.window,
                limits=given.limits,
                active=given.active,
            )
        )
    return filled


def _glsl_number(value: float) -> str:
    text = repr(float(value))
    return text if "e" not in text and "." in text else f"{value:.6g}"


def mixing_shader(channels: list[Channel], *, multichannel: bool) -> str:
    """One shader that adds the store's channels together like light.

    Each channel gets the engine's own controls -- a brightness window, a colour
    and a switch -- so the native layer panel edits them, and the sum clips the
    way every microscopy viewer clips. In three dimensions the brightest channel
    drives the opacity, as neuroglancer's own multichannel program does.
    """
    lines = []
    for index, channel in enumerate(channels):
        parameters = [f"channel=[{index}]"] if multichannel else []
        if channel.window:
            lo, hi = channel.window
            parameters.append(f"range=[{_glsl_number(lo)}, {_glsl_number(hi)}]")
        if channel.limits:
            lo, hi = channel.limits
            parameters.append(f"window=[{_glsl_number(lo)}, {_glsl_number(hi)}]")
        lines.append(f"#uicontrol invlerp c{index}({', '.join(parameters)})")
        lines.append(f'#uicontrol vec3 col{index} color(default="{channel.color}")')
        lines.append(
            f"#uicontrol bool show{index} checkbox(default={'true' if channel.active else 'false'})"
        )
    lines.append("void main() {")
    lines.append("  vec3 rgb = vec3(0.0);")
    lines.append("  float a = 0.0;")
    lines.append("  float v;")
    for index in range(len(channels)):
        lines.append(f"  v = show{index} ? c{index}() : 0.0; rgb += col{index} * v; a = max(a, v);")
    lines.append("  if (VOLUME_RENDERING) { emitRGBA(vec4(rgb, a)); } else { emitRGB(rgb); }")
    lines.append("}")
    return "\n".join(lines) + "\n"


@dataclass
class Layer:
    """One neuroglancer image layer: a name, its placed stores and its channels."""

    name: str
    placements: list[Placement] = field(default_factory=list)
    channels: list[Channel] | None = None
    visible: bool = True
    opacity: float = 1.0
    blend: str = "default"
    volume_rendering: str = "off"

    def to_json(self) -> dict:
        if not self.placements:
            raise ValueError(f"layer {self.name!r} has no stores")
        first = self.placements[0].store
        channels = channels_for(first, self.channels)
        return {
            "type": "image",
            "name": self.name,
            "source": [source_json(placement) for placement in self.placements],
            "shader": mixing_shader(channels, multichannel=first.channel_axis is not None),
            "visible": self.visible,
            "opacity": self.opacity,
            "blend": self.blend,
            "volumeRendering": self.volume_rendering,
        }


def state_json(layers: list[Layer], *, layout: str = "xy") -> dict:
    """The whole scene as neuroglancer state, ready for ``viewer.state.restoreState``."""
    if layout not in LAYOUTS:
        raise ValueError(f"layout must be one of {LAYOUTS}, not {layout!r}")
    return {
        "layers": [layer.to_json() for layer in layers],
        "layout": layout,
        # Left to itself the engine draws the first three axes it meets, which
        # with ``t`` in front is time against depth. The picture is x, y, z.
        "displayDimensions": ["x", "y", "z"],
    }
