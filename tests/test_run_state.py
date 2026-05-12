from hoca.run_state import RUN_STATE_DIRNAME


def test_run_state_dirname() -> None:
    assert RUN_STATE_DIRNAME == ".hoca-runtime"
