"""The engine serves pictures; the window's own settings and doors live with the window.

A smart-microscopy interface starts the engine with :func:`zmart_viewer.make_server`
and brings its own page. Before this split the engine also carried what only the
viewer's own window uses -- where its bar of controls sits, whether its selection
list is offered, the folder chooser, the load window's folder list and builder,
the file of marked places -- so an interface saw knobs that did nothing for it,
and the engine could not change without thinking of the window. Now the engine
offers two small, general openings instead: settings handed to the page as they
are (``page_settings``), and further routes answered by the caller (``routes``).
The window uses both, through :func:`zmart_viewer.gui.window_server.make_window_server`.
"""

from __future__ import annotations

import http.client
import json
import threading
from http import HTTPStatus

import pytest
from zmart_viewer.serving.server import make_server

WINDOW_ROUTES = [
    ("POST", "/api/browse"),
    ("POST", "/api/stores/list"),
    ("POST", "/api/stores/construct"),
    ("POST", "/api/stores/construct-status"),
    ("POST", "/api/stores/construct-cancel"),
    ("GET", "/api/annotations"),
    ("POST", "/api/annotations"),
]

WINDOW_SETTINGS = ("chrome", "panelSide", "canSelect", "depthSamples")


def started(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def stopped(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def site(tmp_path):
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "index.html").write_text("<!doctype html><title>page</title>", encoding="utf-8")
    return folder


def ask(port, method, path, payload=None):
    """One request, the way a page on this computer sends it; the answer as (status, JSON)."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    try:
        connection.request(method, path, body=body, headers=headers)
        answer = connection.getresponse()
        raw = answer.read()
        return answer.status, (json.loads(raw) if raw else None)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "setting",
    ["chrome", "panel_side", "allow_selection", "depth_samples", "browse", "open_from"],
)
def test_the_engine_takes_none_of_the_windows_settings(tmp_path, site, setting):
    with pytest.raises(TypeError):
        make_server(port=0, data_dir=tmp_path, site_dir=site, **{setting: None})


@pytest.mark.parametrize(("method", "route"), WINDOW_ROUTES)
def test_the_engine_does_not_answer_the_windows_doors(tmp_path, site, method, route):
    server, thread = started(make_server(port=0, data_dir=tmp_path, site_dir=site))
    try:
        status, _ = ask(server.server_address[1], method, route, {} if method == "POST" else None)
        assert status == HTTPStatus.NOT_FOUND
        assert not (tmp_path / "zmart-annotations.json").exists()
    finally:
        stopped(server, thread)


def test_the_engines_config_says_nothing_of_the_window(tmp_path, site):
    server, thread = started(make_server(port=0, data_dir=tmp_path, site_dir=site))
    try:
        status, config = ask(server.server_address[1], "GET", "/api/config")
        assert status == HTTPStatus.OK
        assert not set(WINDOW_SETTINGS) & set(config)
        assert config["canOpen"] is True
    finally:
        stopped(server, thread)


def test_the_window_only_helpers_left_the_engine():
    from zmart_viewer.picture import built_picture
    from zmart_viewer.serving import server

    assert not hasattr(server, "ask_this_machine_for_a_folder")
    assert not hasattr(server, "serve")
    assert not hasattr(built_picture, "main")


def test_page_settings_reach_the_page_as_they_are(tmp_path, site):
    server, thread = started(
        make_server(
            port=0,
            data_dir=tmp_path,
            site_dir=site,
            page_settings={"panelSide": "left", "anything": [1, 2]},
        )
    )
    try:
        _, config = ask(server.server_address[1], "GET", "/api/config")
        assert config["panelSide"] == "left"
        assert config["anything"] == [1, 2]
    finally:
        stopped(server, thread)


def test_page_settings_cannot_overwrite_what_the_engine_reports(tmp_path, site):
    with pytest.raises(ValueError, match="layers"):
        make_server(port=0, data_dir=tmp_path, site_dir=site, page_settings={"layers": []})


def test_a_route_of_the_callers_own_is_answered_with_what_it_returns(tmp_path, site):
    heard = []

    def echo(payload):
        heard.append(payload)
        return {"heard": payload}, HTTPStatus.CREATED

    def hello(payload):
        return {"hello": payload}, HTTPStatus.OK

    server, thread = started(
        make_server(
            port=0,
            data_dir=tmp_path,
            site_dir=site,
            routes={"POST /api/echo": echo, "GET /api/hello": hello},
        )
    )
    try:
        port = server.server_address[1]
        assert ask(port, "POST", "/api/echo", {"a": 1}) == (201, {"heard": {"a": 1}})
        assert ask(port, "GET", "/api/hello") == (200, {"hello": None})
        assert ask(port, "GET", "/api/echo")[0] == HTTPStatus.NOT_FOUND
        assert heard == [{"a": 1}]
    finally:
        stopped(server, thread)


def test_a_route_of_the_callers_own_keeps_the_engines_guards(tmp_path, site):
    """A plain-text body from elsewhere reaches no route, the caller's included."""
    heard = []
    server, thread = started(
        make_server(
            port=0,
            data_dir=tmp_path,
            site_dir=site,
            routes={"POST /api/echo": lambda payload: (heard.append(payload) or {}, 200)},
        )
    )
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        connection.request(
            "POST",
            "/api/echo",
            body=b'{"a": 1}',
            headers={"Content-Type": "text/plain", "Origin": "http://elsewhere.example"},
        )
        assert connection.getresponse().status == HTTPStatus.FORBIDDEN
        connection.close()
        assert heard == []
    finally:
        stopped(server, thread)


@pytest.mark.parametrize(
    "route",
    ["POST /api/stores/open", "GET /api/config", "PUT /api/thing", "GET /data/thing", "POST api/x"],
)
def test_a_route_may_not_replace_the_engines_or_leave_api(tmp_path, site, route):
    with pytest.raises(ValueError):
        make_server(
            port=0, data_dir=tmp_path, site_dir=site, routes={route: lambda payload: ({}, 200)}
        )


def test_the_window_server_carries_the_windows_settings_and_doors(tmp_path, site):
    from zmart_viewer.gui.window_server import make_window_server

    (tmp_path / "one folder").mkdir()
    server, thread = started(
        make_window_server(
            port=0,
            data_dir=tmp_path,
            site_dir=site,
            panel_side="left",
            allow_selection=True,
            depth_samples=512,
            chrome=True,
        )
    )
    try:
        port = server.server_address[1]
        _, config = ask(port, "GET", "/api/config")
        assert {key: config[key] for key in WINDOW_SETTINGS} == {
            "chrome": True,
            "panelSide": "left",
            "canSelect": True,
            "depthSamples": 512,
        }
        status, listed = ask(port, "POST", "/api/stores/list", {"path": str(tmp_path)})
        assert status == HTTPStatus.OK
        assert [folder["name"] for folder in listed["folders"]] == ["one folder", "site"]
        assert ask(port, "GET", "/api/annotations") == (200, {"version": 1, "annotations": []})
        assert ask(port, "POST", "/api/browse", {})[0] == HTTPStatus.NOT_IMPLEMENTED
        assert ask(port, "POST", "/api/stores/construct-status", {}) == (200, {"state": "idle"})
    finally:
        stopped(server, thread)


def test_the_window_server_has_the_windows_defaults(tmp_path, site):
    from zmart_viewer.gui.window_server import make_window_server

    server, thread = started(make_window_server(port=0, data_dir=tmp_path, site_dir=site))
    try:
        _, config = ask(server.server_address[1], "GET", "/api/config")
        assert {key: config[key] for key in WINDOW_SETTINGS} == {
            "chrome": False,
            "panelSide": "right",
            "canSelect": False,
            "depthSamples": 256,
        }
    finally:
        stopped(server, thread)


def test_a_window_that_may_not_open_offers_no_folder_list_or_builder(tmp_path, site):
    from zmart_viewer.gui.window_server import make_window_server

    server, thread = started(
        make_window_server(port=0, data_dir=tmp_path, site_dir=site, allow_open=False)
    )
    try:
        port = server.server_address[1]
        for route in ("/api/stores/list", "/api/stores/construct", "/api/stores/construct-status"):
            assert ask(port, "POST", route, {})[0] == HTTPStatus.NOT_FOUND
        assert ask(port, "GET", "/api/annotations")[0] == HTTPStatus.OK
    finally:
        stopped(server, thread)
