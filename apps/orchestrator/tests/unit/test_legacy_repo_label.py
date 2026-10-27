import re

import pytest

from runnerforge.handlers import _legacy_repo_label

# GCE label value rules: lowercase letters, digits, '_' and '-', max 63 chars.
_GCE_LABEL_VALUE = re.compile(r"^[a-z0-9_-]{0,63}$")


@pytest.mark.parametrize(
    ("full_name", "expected"),
    [
        ("rumankazi/runnerforge", "rumankazi_runnerforge"),  # unchanged vs before
        ("RumanKazi/Foo.js", "rumankazi_foo-js"),
        ("acme/" + "r" * 100, ("acme_" + "r" * 100)[:63]),
    ],
    ids=["lowercase", "mixed-case-and-dot", "too-long"],
)
def test_legacy_repo_label_is_gce_safe(full_name, expected):
    """An invalid label value makes instances.insert fail, so the user gets no
    runner at all. Every repo name must map to a valid value."""
    label = _legacy_repo_label(full_name)
    assert label == expected
    assert _GCE_LABEL_VALUE.match(label)
