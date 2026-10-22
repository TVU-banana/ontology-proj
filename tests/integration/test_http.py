from fastapi.testclient import TestClient

from ontology_proj.application.dto import CapabilityState, CapabilityStatus
from ontology_proj.application.readiness import CapabilityCheck, CheckReadiness
from ontology_proj.bootstrap import create_app
from ontology_proj.interfaces.http.app import create_http_app
from ontology_proj.settings import Settings


def test_no_external_configuration_still_serves_health(tmp_path):
    with TestClient(create_app(Settings(data_root=tmp_path))) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["trace_id"] == health.headers["X-Trace-ID"]
        ready = client.get("/ready")
        assert ready.status_code == 503
        status = {c["name"]: c["status"] for c in ready.json()["capabilities"]}
        assert status["data_root"]["state"] == "ready"
        assert status["graph"]["state"] == "not_configured"
        assert status["parser"]["state"] == "not_configured"
        assert ready.json()["trace_id"] != health.json()["trace_id"]
        assert client.post("/api/documents").status_code == 422


def test_configured_does_not_mean_connected_and_credentials_never_leave_http(tmp_path):
    settings = Settings(
        data_root=tmp_path, neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j", neo4j_password="test-graph-secret",
        llm_base_url="http://localhost:7863/v1", llm_model="test-model",
        llm_api_key="test-model-secret",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/ready")
        assert response.status_code == 503
        capabilities = {c["name"]: c["status"] for c in response.json()["capabilities"]}
        assert capabilities["graph"]["state"] == "unavailable"
        assert capabilities["model"]["configured"]
        assert capabilities["model"]["state"] == "unavailable"
        for secret in ("test-graph-secret", "test-model-secret", str(tmp_path)):
            assert secret not in response.text


def test_http_uses_shared_readiness_and_preserves_partial_availability():
    # Test double for application policy; not evidence of an actual graph connection.
    class MutableProbe:
        state = CapabilityState.READY
        def check(self):
            return CapabilityStatus(self.state, True)

    base = MutableProbe()
    model = MutableProbe()
    model.state = CapabilityState.UNAVAILABLE
    use_case = CheckReadiness((
        CapabilityCheck("graph", True, base),
        CapabilityCheck("model", False, model),
    ))
    with TestClient(create_http_app(use_case)) as client:
        assert client.get("/ready").status_code == 200
        base.state = CapabilityState.UNAVAILABLE
        assert client.get("/ready").status_code == 503


def test_unwritable_data_location_does_not_prevent_health(tmp_path):
    file = tmp_path / "file"
    file.write_text("existing")
    with TestClient(create_app(Settings(data_root=file))) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["capabilities"][0]["status"]["error_code"] == "DATA_ROOT_UNAVAILABLE"
    assert file.read_text() == "existing"


def test_missing_parser_models_are_reported_without_leaking_local_paths(tmp_path):
    model_root = tmp_path / "missing-models"
    with TestClient(create_app(Settings(data_root=tmp_path / "data", docling_artifacts_path=model_root))) as client:
        response = client.get("/ready")
        assert response.status_code == 503
        capabilities = {c["name"]: c["status"] for c in response.json()["capabilities"]}
        assert capabilities["parser"]["state"] == "unavailable"
        assert capabilities["parser"]["error_code"] == "MODEL_FILES_UNAVAILABLE"
        assert str(model_root) not in response.text
