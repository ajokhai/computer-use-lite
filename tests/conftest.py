import pytest


@pytest.fixture(autouse=True, scope="session")
def _no_keychain():
    """Tests never read the real Keychain: an unanswered access prompt would stall the run."""
    from cu import config
    config.get_key = lambda name: ""
    yield


@pytest.fixture(autouse=True)
def _no_real_usage(tmp_path, monkeypatch):
    """Tests count usage in a scratch folder, never in the user's real usage file."""
    from cu import usage
    monkeypatch.setattr(usage, "DATA", tmp_path)
    monkeypatch.setattr(usage, "FILE", tmp_path / "usage.json")
