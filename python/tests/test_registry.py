import pytest

from fdmr_plugins import registry


def test_every_kind_has_flow_and_every_part_a_source():
    for kind, parts in registry.REGISTER.items():
        if kind != "all":
            assert "flow" in parts, kind
        for impls in parts.values():
            assert impls and all(m.source and m.code and m.what for m in impls)


def test_default_is_the_first_registered_and_shared_parts_are_included():
    got = registry.record("river-routing")
    assert got["temperature"]["name"] == "surface-heat-budget" and got["particles"]["name"] == "advection-random-walk"


def test_an_unknown_choice_is_refused():
    with pytest.raises(ValueError):
        registry.choices("river-routing", {"temperature": "something-else"})
    with pytest.raises(ValueError):
        registry.choices("river-routing", {"no-such-part": "x"})
    with pytest.raises(ValueError):
        registry.choices("no-such-kind")


def test_a_registered_choice_is_taken():
    assert registry.choices("river-routing", {"temperature": "surface-heat-budget"})["temperature"].name == "surface-heat-budget"
