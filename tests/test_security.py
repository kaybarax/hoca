import hashlib
import hmac
import time

from hoca.security import (
    is_allowed_repo,
    is_secret_like_path,
    verify_hmac_signature,
    verify_timestamp,
)


def test_env_files_are_secret_like() -> None:
    assert is_secret_like_path(".env")
    assert is_secret_like_path(".env.local")
    assert not is_secret_like_path(".env.example")


def test_key_material_is_secret_like() -> None:
    assert is_secret_like_path("certs/deploy.pem")
    assert is_secret_like_path("certs/service.key")
    assert is_secret_like_path("certs/signing.p12")
    assert is_secret_like_path("certs/signing.pfx")
    assert is_secret_like_path("id_rsa")
    assert is_secret_like_path("config/prod.kubeconfig")
    assert is_secret_like_path("nested/id_ed25519")


def test_package_registry_credentials_are_secret_like() -> None:
    assert is_secret_like_path(".npmrc")
    assert is_secret_like_path("packages/app/.npmrc")
    assert is_secret_like_path(".pypirc")


def test_service_and_local_credential_stores_are_secret_like() -> None:
    assert is_secret_like_path(".github/secrets")
    assert is_secret_like_path(".github/secrets/prod")
    assert is_secret_like_path(".docker/config.json")
    assert is_secret_like_path(".aws/credentials")
    assert is_secret_like_path(".config/gcloud/application_default_credentials.json")
    assert is_secret_like_path(".ssh/config")
    assert is_secret_like_path("Library/Keychains/login.keychain-db")


def test_browser_cookies_are_secret_like() -> None:
    assert is_secret_like_path("Library/Application Support/Google/Chrome/Default/Cookies")
    assert is_secret_like_path("Profiles/default-release/cookies.sqlite")


def _sign(secret: str, body: bytes) -> str:
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def test_verify_hmac_signature_valid() -> None:
    body = b'{"test": true}'
    assert verify_hmac_signature("secret", body, _sign("secret", body))


def test_verify_hmac_signature_wrong_secret() -> None:
    body = b'{"test": true}'
    assert not verify_hmac_signature("secret", body, _sign("wrong", body))


def test_verify_hmac_signature_missing() -> None:
    assert not verify_hmac_signature("secret", b"body", None)
    assert not verify_hmac_signature("secret", b"body", "")
    assert not verify_hmac_signature("secret", b"body", "bad-prefix=abc")
    assert not verify_hmac_signature("", b"body", "sha256=abc")


def test_verify_timestamp_valid() -> None:
    assert verify_timestamp(str(int(time.time())))


def test_verify_timestamp_stale() -> None:
    assert not verify_timestamp(str(int(time.time()) - 9999))


def test_verify_timestamp_missing() -> None:
    assert not verify_timestamp(None)
    assert not verify_timestamp("")
    assert not verify_timestamp("not-a-number")


def test_allowed_repo_empty_allows_all() -> None:
    assert is_allowed_repo("owner/repo", None)
    assert is_allowed_repo("owner/repo", "")
    assert is_allowed_repo("owner/repo", "  ")


def test_allowed_repo_matches() -> None:
    assert is_allowed_repo("owner/repo", "owner/repo,other/repo")
    assert is_allowed_repo("other/repo", "owner/repo, other/repo")


def test_allowed_repo_rejects() -> None:
    assert not is_allowed_repo("bad/repo", "owner/repo,other/repo")
