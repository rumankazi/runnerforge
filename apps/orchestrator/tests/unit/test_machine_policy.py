import pytest

from runnerforge.machine_policy import (
    MachinePolicy,
    MachinePolicyError,
    resolve_labels,
)


@pytest.mark.parametrize(
    "size_token,expected_machine_type",
    [
        ("small", "n2-standard-2"),
        ("medium", "n2-standard-4"),
        ("large", "n2-standard-8"),
        ("xlarge", "n2-standard-16"),
    ],
)
def test_tshirt_token_resolves_to_expected_n2_type(size_token, expected_machine_type):
    policy = resolve_labels(["runnerforge", size_token])
    assert policy == MachinePolicy(machine_type=expected_machine_type, spot=False)


def test_only_runnerforge_label_returns_default_machine_no_spot():
    policy = resolve_labels(["runnerforge"])
    assert policy == MachinePolicy(machine_type="n2-standard-4", spot=False)


def test_empty_label_list_returns_default_machine_no_spot():
    policy = resolve_labels([])
    assert policy == MachinePolicy(machine_type="n2-standard-4", spot=False)


@pytest.mark.parametrize(
    "literal_token",
    [
        "e2-small",
        "n2-highcpu-4",
        "n2-standard-16",
        "e2-standard-2",
        "c2d-standard-8",
    ],
)
def test_literal_gce_type_passes_through_verbatim(literal_token):
    policy = resolve_labels(["runnerforge", literal_token])
    assert policy == MachinePolicy(machine_type=literal_token, spot=False)


def test_spot_token_alone_uses_default_machine():
    policy = resolve_labels(["runnerforge", "spot"])
    assert policy == MachinePolicy(machine_type="n2-standard-4", spot=True)


def test_spot_with_tshirt_token_combines_both():
    policy = resolve_labels(["runnerforge", "large", "spot"])
    assert policy == MachinePolicy(machine_type="n2-standard-8", spot=True)


def test_spot_with_literal_combines_both():
    policy = resolve_labels(["runnerforge", "n2-standard-16", "spot"])
    assert policy == MachinePolicy(machine_type="n2-standard-16", spot=True)


def test_only_spot_no_runnerforge_returns_defaults_with_spot():
    # Mirrors the empty-list / runnerforge-only branches but exercises the
    # spot flag without any size token present.
    policy = resolve_labels(["spot"])
    assert policy == MachinePolicy(machine_type="n2-standard-4", spot=True)


def test_token_order_does_not_affect_resolution():
    a = resolve_labels(["runnerforge", "large", "spot"])
    b = resolve_labels(["spot", "large", "runnerforge"])
    c = resolve_labels(["large", "runnerforge", "spot"])
    assert a == b == c


def test_single_unknown_token_raises_with_reason_and_token():
    with pytest.raises(MachinePolicyError) as exc_info:
        resolve_labels(["runnerforge", "gigantic"])
    assert exc_info.value.reason == "unknown_token"
    assert exc_info.value.offending_tokens == ["gigantic"]


def test_multiple_unknown_tokens_collected_into_one_error():
    with pytest.raises(MachinePolicyError) as exc_info:
        resolve_labels(["runnerforge", "foo", "bar"])
    assert exc_info.value.reason == "unknown_token"
    assert exc_info.value.offending_tokens == ["foo", "bar"]


@pytest.mark.parametrize(
    "labels,expected_offending",
    [
        # two t-shirt sizes -> resolved GCE types end up in the error payload
        (
            ["runnerforge", "small", "large"],
            ["n2-standard-2", "n2-standard-8"],
        ),
        # t-shirt + literal
        (
            ["runnerforge", "large", "n2-standard-16"],
            ["n2-standard-8", "n2-standard-16"],
        ),
        # two literals
        (
            ["runnerforge", "n2-standard-4", "c2d-standard-8"],
            ["n2-standard-4", "c2d-standard-8"],
        ),
    ],
)
def test_conflicting_machine_selectors_raise_with_resolved_pair(
    labels, expected_offending
):
    with pytest.raises(MachinePolicyError) as exc_info:
        resolve_labels(labels)
    assert exc_info.value.reason == "conflicting_machines"
    assert exc_info.value.offending_tokens == expected_offending


def test_conflict_short_circuits_before_unknown_is_raised():
    # Documents the ordering: conflict raises mid-loop on the second valid
    # selector; unknowns collected earlier in the same call are never raised.
    # Trade-off accepted in D3 — user fixes one error at a time.
    with pytest.raises(MachinePolicyError) as exc_info:
        resolve_labels(["runnerforge", "small", "large", "gigantic"])
    assert exc_info.value.reason == "conflicting_machines"


def test_machine_policy_error_str_includes_reason_and_tokens():
    # __post_init__ wires the formatted message into Exception.args, so str()
    # is suitable for fallback logging when only the exception is in hand.
    err = MachinePolicyError(reason="unknown_token", offending_tokens=["gigantic"])
    assert "unknown_token" in str(err)
    assert "gigantic" in str(err)
