import time

import jwt
import jwt.exceptions
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from runnerforge.github_client import generate_app_jwt


def test_jwt_has_correct_claims_and_is_signed(rsa_keys):
    PRIVATE_KEY, PUBLIC_KEY = rsa_keys
    token = generate_app_jwt(app_id="123", private_key=PRIVATE_KEY, now=1_000_000)

    # Decode with signature verification - proves signing is real
    decoded = jwt.decode(
        token, PUBLIC_KEY, algorithms=["RS256"], options={"verify_exp": False}
    )

    assert decoded["iss"] == "123"
    assert decoded["iat"] == 1_000_000 - 60  # clock skew offset
    assert decoded["exp"] == decoded["iat"] + 600


def test_jwt_uses_current_time_by_default(rsa_keys):
    PRIVATE_KEY, PUBLIC_KEY = rsa_keys
    before = int(time.time())
    token = generate_app_jwt(app_id="456", private_key=PRIVATE_KEY)
    after = int(time.time())

    decoded = jwt.decode(token, PUBLIC_KEY, algorithms=["RS256"])

    assert "iat" in decoded
    assert "exp" in decoded
    # iat = (time at call) - 60. Time at call is between `before` and `after`.
    # So iat must be in [before-60, after-60]. Mathematically can't fail.
    assert before - 60 <= decoded["iat"] <= after - 60
    assert decoded["exp"] == decoded["iat"] + 600


# --- AC4 (spec: .ultra/20261022-orch-dep-audit/spec.md) ---
# GitHub's documented App JWT rules: alg RS256, iss = app id, iat backdated
# <= 60s for clock drift, exp no more than 10 min after iat. Expectations are
# derived from those rules, not from the implementation's constants.


def test_jwt_header_declares_rs256(rsa_keys):
    """AC4: token header must declare alg RS256 (and typ JWT)."""
    PRIVATE_KEY, _ = rsa_keys
    token = generate_app_jwt(app_id="123", private_key=PRIVATE_KEY, now=1_000_000)

    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256"
    assert header["typ"] == "JWT"


def test_jwt_iss_is_string_equal_to_app_id(rsa_keys):
    """AC4: iss must be the app id (as a string), per GitHub's App JWT rules."""
    PRIVATE_KEY, PUBLIC_KEY = rsa_keys
    token = generate_app_jwt(app_id="789", private_key=PRIVATE_KEY, now=1_000_000)

    decoded = jwt.decode(
        token, PUBLIC_KEY, algorithms=["RS256"], options={"verify_exp": False}
    )
    assert isinstance(decoded["iss"], str)
    assert decoded["iss"] == "789"


def test_jwt_claim_bounds_follow_github_rules(rsa_keys):
    """AC4: iat backdated <= 60s (iat <= now <= iat + 60), 0 < exp - iat <= 600."""
    PRIVATE_KEY, PUBLIC_KEY = rsa_keys
    now = 1_000_000
    token = generate_app_jwt(app_id="123", private_key=PRIVATE_KEY, now=now)

    decoded = jwt.decode(
        token, PUBLIC_KEY, algorithms=["RS256"], options={"verify_exp": False}
    )
    iat, exp = decoded["iat"], decoded["exp"]

    # iat is backdated for clock drift, but by no more than 60s
    assert iat <= now
    assert now - iat <= 60
    # exp is after iat but no more than 10 minutes later
    assert iat < exp
    assert exp - iat <= 600


def test_jwt_verification_fails_with_wrong_public_key(rsa_keys):
    """AC4: the token must not verify against a different RSA public key."""
    PRIVATE_KEY, _ = rsa_keys
    token = generate_app_jwt(app_id="123", private_key=PRIVATE_KEY, now=1_000_000)

    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_public_pem = other_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    try:
        jwt.decode(
            token, other_public_pem, algorithms=["RS256"], options={"verify_exp": False}
        )
    except jwt.exceptions.InvalidSignatureError:
        pass  # expected: signature does not match the signing key
    else:
        raise AssertionError("token verified with a public key that did not sign it")


def test_jwt_rejects_hs256_algorithm_confusion(rsa_keys):
    """AC4: verification with algorithms=['HS256'] must be rejected (no alg confusion)."""
    PRIVATE_KEY, PUBLIC_KEY = rsa_keys
    token = generate_app_jwt(app_id="123", private_key=PRIVATE_KEY, now=1_000_000)

    try:
        jwt.decode(token, PUBLIC_KEY, algorithms=["HS256"])
    except jwt.exceptions.InvalidAlgorithmError:
        pass  # expected: RS256 token must never be accepted as HS256
    else:
        raise AssertionError("RS256 token was accepted under algorithms=['HS256']")
