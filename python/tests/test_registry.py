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


def test_every_maths_names_standard_problems_that_exist():
    """The harness (#10): each registered maths passes named tests, which exist."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    names = {m.name for parts in registry.REGISTER.values() for impls in parts.values() for m in impls}
    assert names == set(registry.CHECKS), names ^ set(registry.CHECKS)
    for name, checks in registry.CHECKS.items():
        assert checks, name
        for c in checks:
            if c.startswith("check: "):
                continue
            path, test = c.split("::")
            text = (root / path).read_text(encoding="utf-8")
            pattern = rf"def {test}\(" if path.endswith(".py") else rf"fn {test}\("
            assert re.search(pattern, text), f"{name}: {c} not found"

