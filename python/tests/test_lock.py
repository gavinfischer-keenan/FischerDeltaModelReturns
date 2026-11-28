"""The runner's one lock (fdmr_runner.lock)."""

import fcntl

import pytest

from fdmr_runner import lock


def test_the_lock_is_held_and_given_back(tmp_path):
    p = tmp_path / "l"
    with lock.held(p) as waited:
        assert waited < 1
        with open(p, "a") as other, pytest.raises(BlockingIOError):
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with open(p, "a") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)  # free again


def test_a_lock_held_elsewhere_is_waited_for_only_so_long(tmp_path):
    p = tmp_path / "l"
    with open(p, "a") as other:
        fcntl.flock(other, fcntl.LOCK_EX)
        slept = []
        with pytest.raises(TimeoutError, match="waited"):
            with lock.held(p, wait_s=0.0, sleep=slept.append):
                pass
