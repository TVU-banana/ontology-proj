import pytest

from ontology_proj.settings import Settings


@pytest.fixture(autouse=True)
def isolated_settings_environment(monkeypatch):
    # Tests never use the operator's model or database credentials.
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.upper(), raising=False)
