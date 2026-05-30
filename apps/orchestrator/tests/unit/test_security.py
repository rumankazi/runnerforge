import hashlib
import hmac

import pytest
from runnerforge.security import verify_github_signature

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
