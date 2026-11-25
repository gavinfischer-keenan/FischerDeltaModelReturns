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
