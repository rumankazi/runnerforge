import time

import jwt
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
