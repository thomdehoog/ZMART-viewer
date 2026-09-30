"""Lets ``python -m zmart_viewer.gui`` do the same as ``zmart-viewer``."""

import sys

from zmart_viewer.gui.window import main

sys.exit(main())
