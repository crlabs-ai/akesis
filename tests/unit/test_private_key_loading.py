"""Verifies the documented private-key configuration with the actual settings loader.

A throwaway RSA key is generated for each run. Nothing here reads a real credential.
"""

from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from src.packages.sdk.github_app_auth import GitHubAppAuth, GitHubAppAuthenticationError
from src.packages.shared.config import Settings


@pytest.fixture
def throwaway_pem() -> tuple[str, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return pem.decode(), public


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_ID"):
        monkeypatch.delenv(name, raising=False)


def test_quoted_multiline_pem_in_dotenv_loads_and_signs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_env: None,
    throwaway_pem: tuple[str, bytes],
) -> None:
    pem, public = throwaway_pem
    (tmp_path / ".env").write_text(f'GITHUB_APP_ID=123\nGITHUB_APP_PRIVATE_KEY="{pem.strip()}"\n')
    monkeypatch.chdir(tmp_path)

    loaded = Settings(environment="test")

    assert loaded.github_app_id == 123
    assert loaded.github_app_private_key.strip() == pem.strip()
    token = GitHubAppAuth(
        loaded.github_app_id,
        loaded.github_app_private_key,
        client=httpx.AsyncClient(base_url="https://api.github.com"),
    )._app_jwt()
    claims = jwt.decode(token, public, algorithms=["RS256"], options={"verify_aud": False})
    assert claims["iss"] == "123"


def test_double_quoted_single_line_with_escaped_newlines_also_loads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_env: None,
    throwaway_pem: tuple[str, bytes],
) -> None:
    # python-dotenv expands \n escapes inside double quotes, so the single-line form works too.
    pem, public = throwaway_pem
    escaped = pem.strip().replace("\n", "\\n")
    (tmp_path / ".env").write_text(f'GITHUB_APP_ID=123\nGITHUB_APP_PRIVATE_KEY="{escaped}"\n')
    monkeypatch.chdir(tmp_path)

    loaded = Settings(environment="test")
    assert loaded.github_app_private_key.strip() == pem.strip()
    token = GitHubAppAuth(
        loaded.github_app_id,
        loaded.github_app_private_key,
        client=httpx.AsyncClient(base_url="https://api.github.com"),
    )._app_jwt()
    jwt.decode(token, public, algorithms=["RS256"], options={"verify_aud": False})


def test_key_with_newlines_stripped_fails_closed_without_leaking_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clean_env: None,
    throwaway_pem: tuple[str, bytes],
) -> None:
    # A key that cannot be parsed must not sign, and its error must not echo key material.
    broken = "-----BEGIN PRIVATE KEY-----\nnot-base64-material\n-----END PRIVATE KEY-----"
    del throwaway_pem
    (tmp_path / ".env").write_text(f'GITHUB_APP_ID=123\nGITHUB_APP_PRIVATE_KEY="{broken}"\n')
    monkeypatch.chdir(tmp_path)

    loaded = Settings(environment="test")
    auth = GitHubAppAuth(
        loaded.github_app_id,
        loaded.github_app_private_key,
        client=httpx.AsyncClient(base_url="https://api.github.com"),
    )
    with pytest.raises(GitHubAppAuthenticationError) as caught:
        auth._app_jwt()
    assert "PRIVATE KEY" not in str(caught.value)
    assert "not-base64-material" not in str(caught.value)


def test_missing_dotenv_leaves_credentials_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    monkeypatch.chdir(tmp_path)
    loaded = Settings(environment="test")
    assert loaded.github_app_id == 0 and loaded.github_app_private_key == ""
