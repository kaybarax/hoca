from hoca.security import is_secret_like_path


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
