from pathlib import Path

import pytest
from pydantic import ValidationError

from ontology_proj.settings import Settings


def test_default_settings_need_no_credentials_and_resolve_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None)
    assert settings.data_root == tmp_path / "data"
    assert not settings.neo4j_password.get_secret_value()
    assert not settings.llm_api_key.get_secret_value()


def test_env_file_is_explicit_and_example_is_loadable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("APP_PORT=9001\n")
    assert Settings(_env_file=None).app_port == 8000
    assert Settings(_env_file=tmp_path / ".env").app_port == 9001
    example = Path(__file__).resolve().parents[2] / ".env.example"
    assert Settings(_env_file=example).embedding_dimension is None


@pytest.mark.parametrize("port", [0, 65536])
def test_invalid_ports_do_not_echo_inputs(port):
    with pytest.raises(ValidationError) as failure:
        Settings(app_port=port)
    assert "input_value" not in str(failure.value)


def test_credentials_are_masked():
    settings = Settings(llm_api_key="test-only-key", neo4j_password="test-only-password")
    assert "test-only-key" not in repr(settings)
    assert "test-only-password" not in settings.model_dump_json()
