"""Writing what a run publishes (fdmr_runner.writer)."""

import pytest

from fdmr_runner import writer


def test_the_link_moves_to_the_new_folder_and_old_ones_go(tmp_path):
    for name in ("20261101T0000Z", "20261102T0000Z", "20261103T0000Z"):
        (tmp_path / name).mkdir()
    writer.swap_link(tmp_path, "20261103T0000Z")
    assert (tmp_path / "latest").resolve().name == "20261103T0000Z" and not (tmp_path / ".latest.tmp").exists()
    gone = writer.prune_folders(tmp_path, 2, "20261103T0000Z")
    assert gone == ["20261101T0000Z"] and (tmp_path / "20261102T0000Z").exists()
    writer.swap_link(tmp_path, "20261102T0000Z")  # a link already there is replaced
    assert (tmp_path / "latest").resolve().name == "20261102T0000Z"


def test_certainty_comes_from_region_toml_only():
    reaches = [{"id": "a", "certainty": {"solid_days": 2, "floor": 0.2}}, {"id": "b"}]
    assert writer.certainty_for(reaches, "a") == (2 * 86_400, 0.2)
    with pytest.raises(ValueError, match="certainty"):
        writer.certainty_for(reaches, "b")


def test_times_and_folders_are_written_one_way():
    assert writer.utc(0) == "1970-01-01T00:00Z" and writer.folder_name(0) == "19700101T0000Z"
