from fdmr_runner.publish import prune


def test_prune_keeps_the_newest_and_the_current(tmp_path):
    names = ["20261124T2200Z", "20261124T2300Z", "20261125T0000Z", "20261125T0100Z"]
    for n in names:
        (tmp_path / n).mkdir()
    (tmp_path / "notes").mkdir()  # not a cycle: left alone
    (tmp_path / "current").symlink_to(names[0])  # a reader still pointed at the oldest
    gone = prune(tmp_path, keep=2)
    assert gone == [names[1]]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([names[0], *names[2:], "notes", "current"])


def test_a_revision_is_newer_than_its_cycle_and_half_built_folders_go(tmp_path):
    import os
    import time

    names = ["20261125T0000Z", "20261125T0000Z.r2", "20261125T0000Z.r3"]
    for n in names:
        (tmp_path / n).mkdir()
    old = tmp_path / ".building-20261124T2300Z-99"
    old.mkdir()
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    (tmp_path / ".building-20261125T0100Z-98").mkdir()  # still being built
    (tmp_path / "current").symlink_to(names[2])
    gone = prune(tmp_path, keep=1)
    assert sorted(gone) == sorted([names[0], names[1], old.name])
    assert (tmp_path / ".building-20261125T0100Z-98").exists()
