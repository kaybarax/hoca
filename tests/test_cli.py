from hoca.cli import main


def test_cli_main_is_callable() -> None:
    assert callable(main)
