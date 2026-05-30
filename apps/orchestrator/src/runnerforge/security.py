import hashlib
import hmac


def verify_github_signature(body: bytes, header_signature: str, secret: bytes) -> bool:
    """Returns True if body's HMAC-SHA256 matches header_signature"""
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_signature)
