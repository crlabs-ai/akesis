import hashlib
import hmac

from src.apps.api.routes import verify_github_signature

SECRET = "test_secret_key_123"
PAYLOAD = b'{"action": "completed", "id": 123}'


def test_valid_signature_accepted() -> None:
    valid_hmac = hmac.new(SECRET.encode(), PAYLOAD, hashlib.sha256).hexdigest()
    header = f"sha256={valid_hmac}"
    assert verify_github_signature(PAYLOAD, header, SECRET) is True


def test_invalid_signature_rejected() -> None:
    header = "sha256=invalid_hex_signature_value"
    assert verify_github_signature(PAYLOAD, header, SECRET) is False


def test_missing_signature_rejected() -> None:
    assert verify_github_signature(PAYLOAD, None, SECRET) is False


def test_malformed_signature_header_rejected() -> None:
    assert verify_github_signature(PAYLOAD, "md5=abcd", SECRET) is False
    assert verify_github_signature(PAYLOAD, "sha256", SECRET) is False
