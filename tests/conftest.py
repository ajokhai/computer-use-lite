import pytest


@pytest.fixture(autouse=True, scope="session")
def _no_keychain():
    """Tests never read the real Keychain: an unanswered access prompt would stall the run."""
    from cu import config
    config.get_key = lambda name: ""
    yield


@pytest.fixture(autouse=True)
def _no_real_usage(tmp_path, monkeypatch):
    """Tests count usage and keep lessons and skills in a scratch folder, never in the user's real files."""
    from cu import memory, skills, usage
    monkeypatch.setattr(usage, "DATA", tmp_path)
    monkeypatch.setattr(usage, "FILE", tmp_path / "usage.json")
    monkeypatch.setattr(memory, "DATA", tmp_path)
    monkeypatch.setattr(memory, "FILE", tmp_path / "lessons.json")
    monkeypatch.setattr(skills, "DATA", tmp_path)
    monkeypatch.setattr(skills, "FILE", tmp_path / "skills.json")
    monkeypatch.setattr(skills, "_tally", {})
