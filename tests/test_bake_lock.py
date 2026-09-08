"""The cross-process bake lock waits for ownership, not a fixed bake duration."""

import errno
import multiprocessing
import os

import pytest

from zmart_viewer.building import _holding_the_bake_lock


def _wait_for_lock(store, attempting, finished, outcome):
    attempting.set()
    try:
        with _holding_the_bake_lock(store):
            outcome.put("acquired")
    except OSError as error:
        outcome.put((error.errno, str(error)))
    finally:
        finished.set()


def test_a_long_bake_does_not_expire_the_waiting_reader(tmp_path):
    context = multiprocessing.get_context("spawn")
    attempting, finished = context.Event(), context.Event()
    outcome = context.Queue()
    reader = context.Process(target=_wait_for_lock, args=(tmp_path, attempting, finished, outcome))
    try:
        with _holding_the_bake_lock(tmp_path):
            reader.start()
            assert attempting.wait(10), "reader process did not start"
            # Windows LK_LOCK gives up after ten one-second attempts. Hold the
            # real process lock beyond that limit, then let the reader proceed.
            assert not finished.wait(12), "reader stopped waiting while the writer held the lock"
        assert finished.wait(5), "reader did not acquire the released lock"
        assert outcome.get(timeout=2) == "acquired"
        reader.join(5)
        assert reader.exitcode == 0
    finally:
        if reader.pid is not None:
            if reader.is_alive():
                reader.terminate()
            reader.join(5)
        outcome.close()
        outcome.join_thread()


@pytest.mark.skipif(os.name != "nt", reason="Windows CRT locking errors")
def test_non_contention_lock_errors_propagate(tmp_path, monkeypatch):
    import msvcrt

    calls = []

    def invalid_descriptor(fd, mode, size):
        calls.append(mode)
        raise OSError(errno.EBADF, "invalid descriptor")

    monkeypatch.setattr(msvcrt, "locking", invalid_descriptor)
    with pytest.raises(OSError) as caught:
        with _holding_the_bake_lock(tmp_path):
            pytest.fail("entered without acquiring the lock")
    assert caught.value.errno == errno.EBADF
    assert calls == [msvcrt.LK_LOCK]


def test_an_exception_in_the_owner_releases_the_lock(tmp_path):
    with pytest.raises(ValueError, match="failed publication"):
        with _holding_the_bake_lock(tmp_path):
            raise ValueError("failed publication")
    with _holding_the_bake_lock(tmp_path):
        pass
