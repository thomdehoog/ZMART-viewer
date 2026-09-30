"""The ZMART Viewer engine: serving, building and following OME-Zarr pictures.

This package is the engine. It reads OME-Zarr images, follows a folder while a
microscope is still writing into it, and serves the pieces of the picture to
a page over HTTP. The viewer's own window (``app/page``) is one such page; a
smart-microscopy interface can be another.

What other software may rely on:

- ``zmart_viewer.launcher.main`` and ``open_window``: the ``zmart-viewer``
  command, and the same thing called from Python.
- ``zmart_viewer.server.make_server``: start the engine on a port, to build
  your own window around it.
- ``zmart_viewer.published``: publish acquisitions to a running engine.
- ``zmart_viewer.projections.write_projection``: write a Top, Min, Max or Sum
  view of an image beside it.
- ``embedding.js``, served at ``/embedding.js``: place the named views on
  your own canvas.

The file map and the reasoning behind it are in docs/how_it_works/ARCHITECTURE.md.
"""
