"""Only pages on this computer may ask the viewer for anything (review M3).

The server listens on 127.0.0.1, so other computers cannot reach it. A web
page open in a browser on the microscope computer can, though: a browser sends
a page's request to any address the page names, and for a request dressed as
plain text it does so without asking first. Three things the browser always
says tell such a request apart -- the name it was sent to (``Host``), the page
it came from (``Origin``), and, for a plain read such as a picture, whether it
crossed from another site (``Sec-Fetch-Site``). The viewer's own page, and an
interface's page served on another port of this computer, are on this
computer by all three; a script run here (the tests, curl) sends no ``Origin``
and is let through.

Before this, a page from anywhere could open folders, close what was on
screen, overwrite the file of marked places and read the images, and a
DNS-rebinding page could read them under a foreign ``Host``.
"""

from __future__ import annotations

import http.client
import json
import threading

import pytest
from zmart_viewer.serving.server import make_server


@pytest.fixture
def served(tmp_path):
    """A server over one tiny store, and the folder its marked places go to."""
    site = tmp_path / "site"
    data = tmp_path / "data"
    site.mkdir()
    data.mkdir()
    (site / "index.html").write_text("<!doctype html><title>page</title>", encoding="utf-8")
    (data / "demo.zarr").mkdir()
    (data / "demo.zarr" / ".zattrs").write_text('{"multiscales": []}', encoding="utf-8")
    (data / "demo.zarr" / "chunk").write_bytes(b"\x01\x02\x03\x04")
    server = make_server(port=0, data_dir=data, site_dir=site, store="demo.zarr")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], data
    finally:
        server.shutdown()
        thread.join(timeout=5)


def ask(port, path, *, method="GET", body=None, headers=None):
    """One request with exactly the headers given, and the answer as (status, headers, body)."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        sent = {"Host": f"127.0.0.1:{port}", **(headers or {})}
        if body is not None:
            sent.setdefault("Content-Length", str(len(body)))
        for name, value in sent.items():
            if value is not None:
                connection.putheader(name, value)
        connection.endheaders(body)
        answer = connection.getresponse()
        return answer.status, dict(answer.getheaders()), answer.read()
    finally:
        connection.close()


MARKED = json.dumps(
    {"version": 1, "annotations": [{"id": "a", "type": "point", "point": [1, 2, 3]}]}
).encode()


@pytest.mark.parametrize("path", ["/", "/api/config", "/data/0/demo.zarr/chunk"])
def test_a_request_addressed_to_another_name_is_refused(served, path):
    """The reading half of DNS rebinding: a foreign Host reads nothing."""
    port, _ = served
    status, _, body = ask(port, path, headers={"Host": f"attacker.example:{port}"})
    assert status == 403
    assert b"\x01\x02\x03\x04" not in body


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]", "LOCALHOST"])
def test_this_computer_by_any_of_its_names_is_answered(served, host):
    port, _ = served
    assert ask(port, "/data/0/demo.zarr/chunk", headers={"Host": f"{host}:{port}"})[0] == 200


@pytest.mark.parametrize(
    "route, body",
    [
        ("/api/annotations", MARKED),
        ("/api/stores/close", b'{"group": "demo"}'),
        ("/api/stores/open", b'{"path": "C:/"}'),
        ("/api/browse", b""),
    ],
)
def test_a_page_from_another_site_cannot_change_anything(served, route, body):
    """Dressed as plain text, the request needs no preflight; it is refused all the same."""
    port, data = served
    status, _, answer = ask(
        port,
        route,
        method="POST",
        body=body,
        headers={"Origin": "http://attacker.example", "Content-Type": "text/plain"},
    )
    assert status == 403, answer
    assert not (data / "zmart-annotations.json").exists()


def test_a_page_with_no_origin_of_its_own_is_refused(served):
    """``Origin: null`` is what a sandboxed frame or a local file sends."""
    port, data = served
    status, _, _ = ask(
        port,
        "/api/annotations",
        method="POST",
        body=MARKED,
        headers={"Origin": "null", "Content-Type": "application/json"},
    )
    assert status == 403
    assert not (data / "zmart-annotations.json").exists()


def test_a_picture_pulled_in_by_another_site_is_refused(served):
    """An ``<img>`` on a foreign page sends no Origin, but says it crossed sites."""
    port, _ = served
    status, _, body = ask(port, "/data/0/demo.zarr/chunk", headers={"Sec-Fetch-Site": "cross-site"})
    assert status == 403
    assert body != b"\x01\x02\x03\x04"


def test_a_body_that_is_not_declared_json_is_refused(served):
    """A plain-text body is what a page sends to avoid asking first; JSON must say so."""
    port, data = served
    status, _, answer = ask(
        port, "/api/annotations", method="POST", body=MARKED, headers={"Content-Type": "text/plain"}
    )
    assert status == 415
    assert "application/json" in json.loads(answer)["error"]
    assert not (data / "zmart-annotations.json").exists()


def test_json_from_this_computer_is_accepted(served):
    port, data = served
    status, _, _ = ask(
        port,
        "/api/annotations",
        method="POST",
        body=MARKED,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    assert status == 200
    assert (
        json.loads((data / "zmart-annotations.json").read_text("utf-8"))["annotations"][0]["id"]
        == "a"
    )


def test_a_page_on_another_port_of_this_computer_may_read_the_answer(served):
    """An interface's page, served by its own server, reads the viewer's answers."""
    port, _ = served
    status, headers, body = ask(
        port, "/data/0/demo.zarr/chunk", headers={"Origin": "http://127.0.0.1:5174"}
    )
    assert status == 200
    assert body == b"\x01\x02\x03\x04"
    assert headers["Access-Control-Allow-Origin"] == "http://127.0.0.1:5174"
    assert headers["Vary"] == "Origin"


def test_an_answer_names_only_the_page_that_asked_never_everyone(served):
    port, _ = served
    _, headers, _ = ask(port, "/api/config")
    assert "Access-Control-Allow-Origin" not in headers


def test_a_page_on_this_computer_is_told_it_may_send_json(served):
    """The browser asks first before it sends JSON across ports; that ask is answered."""
    port, _ = served
    status, headers, _ = ask(
        port,
        "/api/measure",
        method="OPTIONS",
        headers={
            "Origin": "http://localhost:5174",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert status == 204
    assert headers["Access-Control-Allow-Origin"] == "http://localhost:5174"
    assert "POST" in headers["Access-Control-Allow-Methods"]
    assert "Content-Type" in headers["Access-Control-Allow-Headers"]


def test_a_page_from_another_site_is_not_told_it_may(served):
    port, _ = served
    status, headers, _ = ask(
        port,
        "/api/measure",
        method="OPTIONS",
        headers={"Origin": "http://attacker.example", "Access-Control-Request-Method": "POST"},
    )
    assert status == 403
    assert "Access-Control-Allow-Origin" not in headers


def test_a_named_origin_elsewhere_may_ask(tmp_path):
    """``allowed_origins`` names pages beyond this computer that may ask."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("page", encoding="utf-8")
    server = make_server(port=0, site_dir=site, allowed_origins=["http://lab-pc:8000"])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        status, headers, _ = ask(port, "/api/config", headers={"Origin": "http://lab-pc:8000"})
        assert status == 200
        assert headers["Access-Control-Allow-Origin"] == "http://lab-pc:8000"
        assert ask(port, "/api/config", headers={"Origin": "http://other-pc:8000"})[0] == 403
    finally:
        server.shutdown()
        thread.join(timeout=5)
