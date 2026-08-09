import hashlib
import hmac
from unittest.mock import MagicMock

import pytest

from runnerforge.security import verify_github_signature, verify_oidc_token

SECRET = b"test-secret"
BODY = b'{"action":"queued"}'
VALID_SIG = "sha256=" + hmac.new(SECRET, BODY, hashlib.sha256).hexdigest()


@pytest.mark.parametrize(
    "body,signature,secret,expected",
    [
        # happy path
        (BODY, VALID_SIG, SECRET, True),
        # body modified after signing
        (BODY + b"extra", VALID_SIG, SECRET, False),
        # wrong secret
        (BODY, VALID_SIG, b"wrong-secret", False),
        # malformed-header - no sha256= prefix
        (BODY, "foo-bar", SECRET, False),
        # empty body still should work
        (
            b"",
            "sha256=" + hmac.new(SECRET, b"", hashlib.sha256).hexdigest(),
            SECRET,
            True,
        ),
    ],
)
def test_hmac_validation(body, signature, secret, expected):
    assert verify_github_signature(body, signature, secret) is expected


def test_verify_oidc_token_returns_true_on_valid_token(monkeypatch):
    verify_mock = MagicMock(
        return_value={
            "email": "scheduler@runnerforge.iam.gserviceaccount.com",
            "email_verified": True,
        }
    )
    monkeypatch.setattr(
        "runnerforge.security.id_token.verify_oauth2_token", verify_mock
    )
    result = verify_oidc_token(
        "Bearer valid.jwt.token",
        expected_audience="https://orchestrator.example",
        expected_email="scheduler@runnerforge.iam.gserviceaccount.com",
    )
    assert result is True


def test_verify_oidc_token_returns_false_on_missing_bearer_prefix(caplog):
    with caplog.at_level("WARNING"):
        result = verify_oidc_token("Token xyz", "aud", "email")

    assert result is False
    log = next(
        (
            r
            for r in caplog.records
            if "missing or malformed Bearer prefix" in r.message
        ),
        None,
    )
    assert log is not None


def test_verify_oidc_token_returns_false_when_library_raises_valueerror(
    monkeypatch, caplog
):
    verify_mock = MagicMock(side_effect=ValueError("Token expired"))
    monkeypatch.setattr(
        "runnerforge.security.id_token.verify_oauth2_token", verify_mock
    )
    with caplog.at_level("WARNING"):
        result = verify_oidc_token("Bearer x", "aud", "email")

    assert result is False
    log = next(
        (r for r in caplog.records if "token invalid" in r.message),
        None,
    )
    assert log is not None
    assert log.reason == "Token expired"


def test_verify_oidc_token_returns_false_on_email_mismatch(monkeypatch, caplog):
    verify_mock = MagicMock(
        return_value={
            "email": "wrong@example.com",
            "email_verified": True,
        }
    )
    monkeypatch.setattr(
        "runnerforge.security.id_token.verify_oauth2_token", verify_mock
    )
    with caplog.at_level("WARNING"):
        result = verify_oidc_token("Bearer x", "aud", "expected@example.com")

    assert result is False
    log = next(
        (r for r in caplog.records if "email mismatch" in r.message),
        None,
    )
    assert log is not None
    assert log.expected == "expected@example.com"
    assert log.actual == "wrong@example.com"


def test_verify_oidc_token_returns_false_when_email_not_verified(monkeypatch, caplog):
    verify_mock = MagicMock(
        return_value={
            "email": "scheduler@runnerforge.iam.gserviceaccount.com",
            "email_verified": False,
        }
    )
    monkeypatch.setattr(
        "runnerforge.security.id_token.verify_oauth2_token", verify_mock
    )
    with caplog.at_level("WARNING"):
        result = verify_oidc_token(
            "Bearer x",
            "aud",
            "scheduler@runnerforge.iam.gserviceaccount.com",
        )

    assert result is False
    log = next(
        (r for r in caplog.records if "email_verified is False" in r.message),
        None,
    )
    assert log is not None
    assert log.email == "scheduler@runnerforge.iam.gserviceaccount.com"


def test_verify_oidc_token_returns_false_on_empty_header():
    assert verify_oidc_token("", "aud", "email") is False


def test_verify_oidc_token_passes_expected_audience_to_library(monkeypatch):
    verify_mock = MagicMock(return_value={"email": "x", "email_verified": True})
    monkeypatch.setattr(
        "runnerforge.security.id_token.verify_oauth2_token", verify_mock
    )
    verify_oidc_token("Bearer x", "https://my-aud", "x")
    verify_mock.assert_called_once()
    _, kwargs = verify_mock.call_args
    assert kwargs["audience"] == "https://my-aud"
