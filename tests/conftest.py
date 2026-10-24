import pytest


@pytest.fixture(autouse=True, scope="session")
def _no_keychain():
    """Tests never read the real Keychain: an unanswered access prompt would stall the run."""
    from cu import config
    config.get_key = lambda name: ""
    yield
