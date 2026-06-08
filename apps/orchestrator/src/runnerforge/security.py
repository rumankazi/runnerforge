import hashlib
import hmac
import logging

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from opentelemetry import trace

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


def verify_github_signature(
    body: bytes, x_hub_signature_256: str, secret: bytes
) -> bool:
    """Returns True if body's HMAC-SHA256 matches header_signature"""
    with tracer.start_as_current_span("webhook.verify_github_signature") as span:
        span.set_attribute("signature_present", bool(x_hub_signature_256))

        expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, x_hub_signature_256)


def verify_oidc_token(
    authorization_header: str,
    expected_audience: str,
    expected_email: str,
) -> bool:
    """Verify a Google OIDC JWT from the Authorization header.

    Returns True iff:
    - Header is well-formed (Bearer prefix)
    - Token signature is valid (signed by Google)
    - Issuer is accounts.google.com
    - Audience matches expected_audience
    - Token is not expired
    - email claim matches expected_email
    - email_verified is True
    """
    if not authorization_header.startswith("Bearer "):
        logger.warning("OIDC verification failed: missing or malformed Bearer prefix")
        return False

    token = authorization_header.removeprefix("Bearer ")

    try:
        claims = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            audience=expected_audience,
        )
    except ValueError as e:
        logger.warning(
            "OIDC verification failed: token invalid",
            extra={"reason": str(e)},
        )
        return False

    actual_email = claims.get("email")
    if actual_email != expected_email:
        logger.warning(
            "OIDC verification failed: email mismatch",
            extra={"expected": expected_email, "actual": actual_email},
        )
        return False

    if not claims.get("email_verified"):
        logger.warning(
            "OIDC verification failed: email_verified is False",
            extra={"email": actual_email},
        )
        return False

    return True
