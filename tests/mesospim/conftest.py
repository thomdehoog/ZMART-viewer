"""Fixtures for the mesoSPIM view tests.

The browser is the repository's own session-wide Chromium fixture (``tests/
conftest.py``); ``pages`` opens the built page in it and waits for a picture.
The tile fixtures are pretend mesoSPIM tiles written with numpy alone.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mesospim_view import PAGE_DIR
from mesospim_view.demo import write_tile, write_tiles


@pytest.fixture(scope="session")
def tiles(tmp_path_factory) -> list[Path]:
    """Four two-channel tiles in a two-by-two grid, one time point each."""
    return write_tiles(tmp_path_factory.mktemp("tiles"))


@pytest.fixture(scope="session")
def stacks(tmp_path_factory) -> list[Path]:
    """Two tiles side by side with three time points each, for the sliders."""
    folder = tmp_path_factory.mktemp("stacks")
    return [
        write_tile(folder / f"tile_{i}.ome.zarr", origin_um=(0, 0, i * 144), seed=i, timepoints=3)
        for i in range(2)
    ]


# What the engine holds: every layer's sources and errors, and how many of the
# chunks the picture needs have arrived.
DESCRIBE = """() => {
  const v = window.viewer; if (!v?.layerManager) return null;
  let needed = 0, available = 0;
  const layers = v.layerManager.managedLayers.map((m) => {
    for (const rl of m.layer?.renderLayers ?? []) {
      const p = rl.layerChunkProgressInfo;
      if (p) { needed += p.numVisibleChunksNeeded; available += p.numVisibleChunksAvailable; }
    }
    return {
      name: m.name,
      loaded: (m.layer?.dataSources ?? []).every((s) => s.loadState !== undefined),
      errors: (m.layer?.dataSources ?? []).map((s) => s.loadState?.error?.message).filter(Boolean),
      channelRank: m.layer?.channelCoordinateSpace?.value?.rank ?? null,
      sources: (m.layer?.dataSources ?? []).length,
    };
  });
  const space = v.navigationState.position.coordinateSpace.value;
  return { layers, needed, available, names: Array.from(space?.names ?? []),
           shown: Array.from(v.navigationState.pose.displayDimensionRenderInfo.value.displayDimensionIndices) };
}"""


class Pages:
    """The built page, opened in the session's browser."""

    def __init__(self, browser) -> None:
        self.browser = browser
        self.opened = []

    def open(self, url: str, *, width: int = 900, height: int = 700):
        """The page at ``url`` and the list its script errors are collected in."""
        page = self.browser.new_page(viewport={"width": width, "height": height})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        self.opened.append(page)
        return page, errors

    @staticmethod
    def describe(page) -> dict | None:
        return page.evaluate(DESCRIBE)

    def drawn(self, page, *, layers: int, timeout_s: float = 40.0) -> dict:
        """Wait until ``layers`` engine layers have loaded and every visible chunk is in."""
        deadline = time.time() + timeout_s
        seen = None
        while time.time() < deadline:
            seen = self.describe(page)
            if (
                seen
                and len(seen["layers"]) == layers
                and all(layer["loaded"] for layer in seen["layers"])
                and seen["needed"] > 0
                and seen["available"] == seen["needed"]
            ):
                return seen
            time.sleep(0.25)
        raise AssertionError(f"the picture never settled: {seen}")

    def close(self) -> None:
        for page in self.opened:
            if not page.is_closed():
                page.close()


@pytest.fixture
def pages(browser) -> Pages:
    if not (PAGE_DIR / "index.html").is_file():
        pytest.skip("the mesoSPIM page is not built: npm ci && npm run build in app/mesospim")
    held = Pages(browser)
    yield held
    held.close()
