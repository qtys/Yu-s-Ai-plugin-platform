from fastapi.testclient import TestClient
from app import database
from app.main import app


def test_model_capabilities_preserve_legacy_state_and_configuration(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "models.db")
    with TestClient(app) as client:
        assert client.get("/api/model-capabilities").json() == {"speech": False, "image_generation": False}
        client.patch("/api/speech/settings", json={"stt_model": "recognition", "stt_api_key": "test-secret"})
        client.patch("/api/images/settings", json={"model": "drawing", "api_key": "test-secret"})
        # Older versions' state is still readable after upgrading.
        client.put("/api/plugins/speech/state", json={"enabled": True})
        assert client.get("/api/model-capabilities").json()["speech"] is True
        assert client.put("/api/model-capabilities/image_generation/state", json={"enabled": True}).status_code == 200
        assert client.put("/api/model-capabilities/speech/state", json={"enabled": False}).status_code == 200
        assert client.put("/api/model-capabilities/unknown/state", json={"enabled": True}).status_code == 422
        assert client.put("/api/model-capabilities/speech/state", json={"enabled": True, "platform": "android", "device_id": "test-device"}).status_code == 409
        assert not {"speech", "image_generation"} & {item["id"] for item in client.get("/api/plugins").json()}
    with TestClient(app) as client:
        assert client.get("/api/model-capabilities").json() == {"speech": False, "image_generation": True}
        assert client.get("/api/speech/settings").json()["stt_model"] == "recognition"
        assert client.get("/api/images/settings").json()["model"] == "drawing"
        with database.connect() as db:
            assert db.execute("SELECT stt_api_key FROM speech_settings").fetchone()[0] == "test-secret"
            assert db.execute("SELECT api_key FROM image_settings").fetchone()[0] == "test-secret"
