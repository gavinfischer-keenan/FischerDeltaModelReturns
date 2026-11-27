"""Trials (#26): --maths parsing and labelled publication."""

import pytest

pytest.importorskip("fdmr_py")

from fdmr_runner.trial import parse_maths  # noqa: E402


def test_maths_items_become_per_reach_choices():
    assert parse_maths(["*.dispersion=fischer-longitudinal", "river.channel=wide-channel-uniform-flow"]) == {
        "*": {"dispersion": "fischer-longitudinal"}, "river": {"channel": "wide-channel-uniform-flow"}}


def test_a_malformed_item_is_refused():
    with pytest.raises(ValueError):
        parse_maths(["dispersion=fischer-longitudinal"])
