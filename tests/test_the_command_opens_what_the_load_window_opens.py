"""``zmart-viewer <folder>`` opens every kind of folder the load window opens.

The command used to look for images on its own, and it only knew one shape:
an image, or a folder of images. An HCS plate, or a run a microscope had only
just started writing, was refused with "No OME-Zarr image was found", although
the load window opened both. Now the command asks the running engine to open
the folder through the same door the load window uses, so the two agree.

Each test starts the command without a window, asks the engine what it is
showing, and stops it, the way a person pressing Ctrl+C would.
"""

from __future__ import annotations

import json
import urllib.request

import pytest
from demo_data import write_demo_zarr
from grid_scans import _a_grid_scan
from record_fixtures import a_live_run
from test_a_plate_lays_itself_out import a_small_plate
from zmart_viewer.gui import window


@pytest.fixture
def what_the_command_showed(monkeypatch):
    """Run the command once and report what the engine had open."""
    shown = {}

    def look_then_stop(server, url):
        with urllib.request.urlopen(f"{url}/api/config", timeout=30) as answer:
            shown["config"] = json.load(answer)
        server.shutdown()
        server.server_close()

    monkeypatch.setattr(window, "_serve_until_interrupt", look_then_stop)

    def run(*arguments):
        shown.clear()
        code = window.main([*map(str, arguments), "--no-window", "--port", "0"])
        return code, shown.get("config")

    return run


def _image_layers(config) -> int:
    return len([layer for layer in config.get("layers", []) if layer.get("kind") != "segmentation"])


def test_one_image(tmp_path, what_the_command_showed):
    image = write_demo_zarr(tmp_path / "one.ome.zarr")
    code, config = what_the_command_showed(image)
    assert code == 0
    assert _image_layers(config) >= 1


def test_an_hcs_plate(tmp_path, what_the_command_showed):
    a_small_plate(tmp_path)
    plate = tmp_path / "plate.ome.zarr"
    code, config = what_the_command_showed(plate)
    assert code == 0, "a plate opens from the command, as it does from the load window"
    assert _image_layers(config) >= 1


def test_raw_positions_from_a_microscope(tmp_path, what_the_command_showed):
    positions = _a_grid_scan(tmp_path / "scan")
    code, config = what_the_command_showed(positions)
    assert code == 0, "raw positions open from the command, as they do from the load window"
    assert _image_layers(config) >= 1


def test_a_run_that_has_only_just_started(tmp_path, what_the_command_showed):
    """A run declared but not yet written opens, and waits for its first image."""
    run = tmp_path / "run"
    a_live_run(run)
    code, config = what_the_command_showed(run)
    assert code == 0, "a run still being written opens from the command"
    assert config is not None


def test_a_missing_folder_is_said_plainly(tmp_path, what_the_command_showed, capsys):
    code, config = what_the_command_showed(tmp_path / "nowhere")
    assert code == 1
    assert config is None, "nothing is left running after a refusal"
    assert "There is no folder at" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("option", "value"),
    [("--range", "bright"), ("--range", "100"), ("--tiles", "a,b")],
)
def test_a_mistyped_option_is_explained(option, value, capsys):
    """A typo in an option is a sentence about the option, not a traceback."""
    with pytest.raises(SystemExit) as stopped:
        window.main([option, value, "--no-window", "--port", "0"])
    assert stopped.value.code == 2
    said = capsys.readouterr().err
    assert option in said and "Traceback" not in said


def test_without_a_window_toolkit_it_carries_on_in_a_browser(
    tmp_path, what_the_command_showed, monkeypatch, capsys
):
    """pywebview installs everywhere, but on Linux it also needs GTK or Qt.

    Without them the native window fails to start. The viewer must then say
    so, print an address for a browser, and keep serving, rather than end
    with a traceback and take the engine down with it.
    """
    import sys
    import types

    class NoToolkit(Exception):
        pass

    fake = types.ModuleType("webview")
    fake.FOLDER_DIALOG = 20
    fake.create_window = lambda *a, **k: types.SimpleNamespace(create_file_dialog=None)

    def start():
        raise NoToolkit("You must have either QT or GTK with Python extensions installed")

    fake.start = start
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(window, "_webview2_present", lambda: True)

    image = write_demo_zarr(tmp_path / "one.ome.zarr")
    shown = {}

    def look_then_stop(server, url):
        with urllib.request.urlopen(f"{url}/api/config", timeout=30) as answer:
            shown["config"] = json.load(answer)
        server.shutdown()
        server.server_close()

    monkeypatch.setattr(window, "_serve_until_interrupt", look_then_stop)
    code = window.main([str(image), "--port", "0"])  # a native window is asked for
    said = capsys.readouterr().out

    assert code == 0
    assert "could not be opened" in said and "http://127.0.0.1:" in said
    assert _image_layers(shown["config"]) >= 1, "the engine kept serving the image"
