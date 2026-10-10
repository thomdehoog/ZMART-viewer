"""Files the engine puts in place can be read like any other file the user writes.

The engine writes its descriptions (``zarr.json``, ``signed.json`` and the
rest) to a temporary file and renames it into place, so that no reader ever
sees half a file. A temporary file is normally private to its owner, and the
rename keeps that, so on a shared Linux or macOS computer another account
could not read a run at all. These tests compare with a file written the
ordinary way: whatever the computer's settings give that file, the engine's
files get too. On Windows, which has no such permissions, both sides are the
same anyway.
"""

import os
import stat

from zmart_viewer.filesystem import put_json_in_place, put_text_in_place


def permissions_of(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_a_new_file_gets_the_permissions_of_an_ordinary_one(tmp_path):
    ordinary = tmp_path / "ordinary.txt"
    ordinary.write_text("written the ordinary way")
    put_in_place = tmp_path / "zarr.json"
    put_json_in_place(put_in_place, {"zarr_format": 3})
    assert permissions_of(put_in_place) == permissions_of(ordinary)


def test_a_replaced_file_keeps_its_permissions(tmp_path):
    destination = tmp_path / "signed.json"
    destination.write_text("{}")
    os.chmod(destination, 0o644 if os.name != "nt" else stat.S_IREAD | stat.S_IWRITE)
    before = permissions_of(destination)
    put_text_in_place(destination, '{"revision": 2}')
    assert destination.read_text() == '{"revision": 2}'
    assert permissions_of(destination) == before


def test_the_temporary_file_is_created_open_to_the_usual_settings(tmp_path, monkeypatch):
    """The part that decides the outcome on Linux and macOS, checked on any computer.

    ``os.open`` with 0o666 lets the computer's usual setting (the umask) decide,
    exactly as an ordinary ``open`` does; a private temporary file asks for 0o600.
    """
    asked = []
    real_open = os.open

    def recording_open(path, flags, mode=0o777, *args, **kwargs):
        asked.append(mode)
        return real_open(path, flags, mode, *args, **kwargs)

    monkeypatch.setattr(os, "open", recording_open)
    put_text_in_place(tmp_path / "publication.json", "{}")
    assert asked and all(mode == 0o666 for mode in asked)
