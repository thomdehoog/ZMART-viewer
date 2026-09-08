"""Exact acquired regions, using the storage tile record's voxel coordinates.

These are acquisition facts, not inferred array occupancy. A tuple of no regions
means nothing acquired; absent region information is a different state.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AcquiredRegion:
    frame: int
    channel: int
    origin: tuple[int, int, int]
    shape: tuple[int, int, int]

    @classmethod
    def from_written(cls, record):
        try:
            frame, channel = record["frame"], record["channel"]
            origin = tuple(record["origin"][a] for a in "zyx")
            shape = tuple(record["shape"][a] for a in "zyx")
        except (KeyError, TypeError) as error:
            raise ValueError("Acquired regions require frame, channel, origin and shape") from error
        if any(type(n) is not int or n < 0 for n in (frame, channel, *origin)):
            raise ValueError("Acquired region coordinates must be nonnegative integers")
        if any(type(n) is not int or n <= 0 for n in shape):
            raise ValueError("Acquired region shapes must be positive integers")
        return cls(frame, channel, origin, shape)

    def as_written(self):
        return {
            "frame": self.frame,
            "channel": self.channel,
            "origin": dict(zip("zyx", self.origin, strict=True)),
            "shape": dict(zip("zyx", self.shape, strict=True)),
        }

    def bounds(self, at=(0, 0, 0)):
        low = tuple(a + b for a, b in zip(at, self.origin, strict=True))
        return low, tuple(a + b for a, b in zip(low, self.shape, strict=True))
