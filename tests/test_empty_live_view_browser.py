from record_fixtures import a_live_run
from test_manifest_refresh_browser import _serving


def test_empty_manifest_remains_open_for_its_first_publication(browser, built_dist, tmp_path):
    run = a_live_run(tmp_path)
    with _serving(built_dist, run) as address:
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        try:
            page.goto(address)
            page.wait_for_function("window.zmartConfig?.liveState?.runs?.length > 0")
            actual = page.evaluate("window.zmartConfig")
            assert not errors
            assert actual["liveState"]["runs"][0]["revision"] == 0
        finally:
            page.close()
