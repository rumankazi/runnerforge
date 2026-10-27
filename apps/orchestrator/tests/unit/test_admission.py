import logging

import pytest

from runnerforge import main
from runnerforge.config import parse_installation_ids


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),  # unset → allow all (self-host default)
        ("135399152", frozenset({135399152})),
        (" 1 , 2 ,", frozenset({1, 2})),  # whitespace + trailing comma tolerated
        ("", frozenset()),  # explicit kill switch: nobody allowed
    ],
    ids=["unset", "single", "whitespace-trailing-comma", "empty"],
)
def test_parse_installation_ids(value, expected):
    assert parse_installation_ids(value) == expected


def test_parse_installation_ids_rejects_malformed_id():
    """Fails loudly at startup rather than silently allowing/denying everyone."""
    with pytest.raises(ValueError):
        parse_installation_ids("135399152,rumankazi")


def test_log_admission_mode_warns_when_allow_list_unset(monkeypatch, caplog):
    monkeypatch.setattr(main, "ALLOWED_INSTALLATION_IDS", None)

    with caplog.at_level(logging.INFO):
        main.log_admission_mode()

    log = next(r for r in caplog.records if "ALLOWED_INSTALLATION_IDS" in r.message)
    assert log.levelno == logging.WARNING


def test_log_admission_mode_reports_allow_list_size(monkeypatch, caplog):
    monkeypatch.setattr(main, "ALLOWED_INSTALLATION_IDS", frozenset({1, 2}))

    with caplog.at_level(logging.INFO):
        main.log_admission_mode()

    log = next(r for r in caplog.records if "allow-list active" in r.message)
    assert log.allowed_installation_count == 2
