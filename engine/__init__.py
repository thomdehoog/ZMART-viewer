"""The ZMART viewing engine: reading, placing, following and serving OME-Zarr pictures.

This package is the engine. It reads OME-Zarr images, places each position
where it belongs, follows a folder while a microscope is still writing into
it, and serves the pieces of the picture to a window over HTTP. The viewer's
own window (``gui/``, imported as ``zmart_viewer.gui``) is
one such window; a smart-microscopy interface
is another. The engine holds no buttons or panels of its own.

Inside it:

- ``serving``: the address, the routes, and the pieces of the picture.
- ``opening``: what a path is, what is open, and how images are read.
- ``picture``: placing positions into one picture and building its pieces.
- ``views``: the named views, Slice, Top and Min/Max/Sum.
- ``live``: following a run while it is written, and the record of how a
  run writes itself.
- ``drawing``: the JavaScript that creates and drives neuroglancer, with its
  own controls switched off, and ``embedding.js`` for the named views. Any
  interface, this package's own window included, draws through it.

The names below are what other software may rely on. They stay stable
between versions; everything deeper inside may move.
"""

from zmart_viewer.serving.server import make_server
from zmart_viewer.views.projections import write_projection
from zmart_viewer.views.publishing import STORE, PublishedAcquisition

__all__ = [
    "PublishedAcquisition",
    "STORE",
    "make_server",
    "write_projection",
]
