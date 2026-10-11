import pytest


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Keep the user's saved settings (e.g. default render style) out of the tests."""
    from autokaraoke import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "config")
