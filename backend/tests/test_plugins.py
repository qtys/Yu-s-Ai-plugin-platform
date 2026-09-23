import tempfile

from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.plugins import PluginManifest, PluginRegistry


def test_builtin_plugin_registry_rejects_duplicate_ids():
    registry = PluginRegistry()
    manifest = PluginManifest("demo", "演示", "说明", "1.0.0", (), ())
    registry.register(manifest)
    try:
        registry.register(manifest)
    except ValueError as error:
        assert "Duplicate" in str(error)
    else:
        raise AssertionError("Duplicate plugin ID was accepted")


def test_plugin_enablement_persists_and_gates_runtime(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.DATA_DIR / "plugins.db")
        with TestClient(app) as client:
            installed = {item["id"]: item for item in client.get("/api/plugins").json()}
            assert set(installed) == {"translation", "message_display", "proactive"}
            assert all(item["enabled"] and item["source"] == "builtin" for item in installed.values())
            assert installed["translation"]["permissions"] == ["local_storage", "network_download"]
            assert client.put("/api/plugins/missing/state", json={"enabled": False}).status_code == 404

            response = client.put("/api/plugins/translation/state", json={"enabled": False})
            assert response.json() == {"id": "translation", "enabled": False}
            assert client.get("/api/translation/packages").status_code == 409
            assert client.post("/api/translation", json={"text": "hello", "source": "en", "target": "en"}).status_code == 409
            assert client.post("/api/translation/packages/zh/en/stream").status_code == 409
            assert client.put("/api/plugins/message_display/state", json={"enabled": False}).status_code == 200
            assert client.put("/api/plugins/proactive/state", json={"enabled": False}).status_code == 200
            assert client.get("/api/plugins/proactive").json()["plugin_enabled"] is False
            assert client.post("/api/plugins/proactive/generate", json={"character_id": 1}).json() == {"skipped": True, "reason": "plugin_disabled"}

        # The startup migration must not reset existing choices.
        with TestClient(app) as client:
            persisted = {item["id"]: item["enabled"] for item in client.get("/api/plugins").json()}
            assert persisted == {"translation": False, "message_display": False, "proactive": False}
            assert client.put("/api/plugins/translation/state", json={"enabled": True}).status_code == 200
            assert client.post("/api/translation", json={"text": "hello", "source": "en", "target": "en"}).json() == {"translation": "hello"}


def test_plugin_table_is_added_to_existing_database(monkeypatch):
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.DATA_DIR / "older.db")
        database.init_db()
        with database.connect() as db:
            db.execute("DROP TABLE plugin_states")
        database.init_db()
        with database.connect() as db:
            assert db.execute("SELECT name FROM sqlite_master WHERE name='plugin_states'").fetchone() is not None
