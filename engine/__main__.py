"""Lets ``python -m zmart_viewer`` do the same as the ``zmart-viewer`` command."""

import sys

from zmart_viewer.command import main

sys.exit(main())
