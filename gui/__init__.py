"""The viewer's own window: what ``zmart-viewer`` opens for looking at data.

The JavaScript beside this file is the window's contents (panels, sliders, the
load window). ``window.py`` opens it as a desktop window with pywebview and
runs the ``zmart-viewer`` command. A smart-microscopy interface uses none of
this; it starts the engine with :func:`zmart_viewer.make_server` and brings
its own window.
"""
