from hoca.security import is_secret_like_path


def test_env_files_are_secret_like() -> None:
    assert is_secret_like_path(".env")
