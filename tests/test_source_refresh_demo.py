"""The saved interactive demo uses the real publisher and viewer."""

from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import pytest
from pixels import image_middle
from show_source_refresh import Demo, picture
from test_manifest_refresh_browser import _open, _serving, _wait_for_picture, _wait_for_revision
from test_transparent_2d_browser import READ_ALPHA

CONTROLS = Path(__file__).resolve().parents[1] / "demos/show_source_refresh.js"


def test_demo_keeps_six_positions_and_can_rewrite_when_full(tmp_path):
    demo = Demo(tmp_path / "run")
    for _ in range(5):
        demo.publish()
    assert (demo.count, demo.revision) == (6, 6)
    assert "All 6" in demo.publish()
    assert (demo.count, demo.revision) == (6, 6)
    demo.publish(rewrite=True)
    assert (demo.count, demo.revision) == (6, 7)
    pixels = picture(5, 10000)
    assert pixels.min() == 0
    assert pixels.max() < 4095


@pytest.mark.parametrize("bake", [False, True])
def test_saved_demo_buttons_publish_rewrite_and_leave_idle_data_cached(
    browser, built_dist, tmp_path, bake
):
    demo = Demo(tmp_path / "run")
    with _serving(built_dist, demo._run, transparent_background=True, bake=bake) as address:
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        requested, errors = [], []
        page.on("request", lambda request: requested.append(urlsplit(request.url).path))
        page.on("pageerror", lambda error: errors.append(str(error)))
        # Playwright supplies only the native-window transport; publishing is real.
        page.expose_function("publishPosition", demo.publish)
        page.add_init_script("""
          window.pywebview = {api: {publish: rewrite => window.publishPosition(rewrite)}};
          window.zmartLiveCheckMs = 500;
        """)
        try:
            _open(page, address, 1)
            _wait_for_picture(page)
            page.evaluate(CONTROLS.read_text(encoding="utf-8"))
            before = image_middle(page)
            for label, revision in [("Add position", 2), ("Rewrite last", 3)]:
                page.get_by_role("button", name=label, exact=True).click()
                _wait_for_revision(page, revision)
                _wait_for_picture(page)
                assert f"revision {revision}" in page.get_by_role("status").last.inner_text()
                after = image_middle(page)
                assert np.count_nonzero(np.any(before != after, axis=2)) > 100
                alpha = page.evaluate(READ_ALPHA)
                assert alpha["clear"] > 1000 and alpha["black"] > 1000
                assert alpha["partial"] == 0
                before = after
            mark = len(requested)
            page.get_by_role("button", name="Repeat unchanged hint").click()
            page.wait_for_timeout(1800)
            assert not [path for path in requested[mark:] if path.startswith("/data/")]
            assert demo.revision == 3
            page.screenshot(path=str(tmp_path / "interactive-demo.png"))
            assert not errors, errors
        finally:
            page.close()
