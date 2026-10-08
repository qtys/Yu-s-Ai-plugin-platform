import logging

from app import logging_config as logs


def test_plugin_logs_are_isolated_rotated_redacted_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logs, "LOG_FILE", tmp_path / "yus-ai.log")
    root = logging.getLogger()
    previous = list(root.handlers)
    try:
        logs.configure_logging()
        count = len(root.handlers)
        logs.configure_logging()
        assert len(root.handlers) == count
        logs.get_plugin_logger("image_generation").warning(
            "image_failed https://user:password@cdn.example/pic?signature=secret Bearer secret-key")
        token = logs.PLUGIN_CONTEXT.set("speech")
        try:
            logging.getLogger("yus_ai.api").warning("speech request failed")
        finally:
            logs.PLUGIN_CONTEXT.reset(token)
        logging.getLogger("yus_ai.api").warning("proactive_model_failed error_type=TimeoutError")
        try:
            raise ValueError("private exception with secret-key")
        except ValueError:
            logs.get_plugin_logger("image_generation").exception("decode_failed")
        for handler in root.handlers:
            handler.flush()
        image = (tmp_path / "plugins/image_generation.log").read_text(encoding="utf-8")
        speech = (tmp_path / "plugins/speech.log").read_text(encoding="utf-8")
        proactive = (tmp_path / "plugins/proactive.log").read_text(encoding="utf-8")
        total = (tmp_path / "yus-ai.log").read_text(encoding="utf-8")
        assert "image_failed" in image and "speech request failed" not in image
        assert "exception_type=ValueError" in image and "private exception" not in image
        assert "speech request failed" in speech and "image_failed" not in speech
        assert "proactive_model_failed" in proactive
        assert "image_failed" in total and "speech request failed" in total
        for value in (image, total):
            assert "password" not in value and "signature=secret" not in value and "secret-key" not in value
        plugin_handlers = [h for h in root.handlers if h not in previous and h.filters]
        assert all(h.maxBytes == 2 * 1024 * 1024 and h.backupCount == 2 for h in plugin_handlers)
    finally:
        for handler in list(root.handlers):
            if handler not in previous:
                root.removeHandler(handler)
                handler.close()


def test_plugin_route_mapping():
    assert logs.plugin_for_path("/api/images/generate") == "image_generation"
    assert logs.plugin_for_path("/api/images-malicious") is None
    assert logs.plugin_for_path("/api/speech/transcribe") == "speech"
    assert logs.plugin_for_path("/api/translation") == "translation"
    assert logs.plugin_for_path("/api/plugins/proactive/generate") == "proactive"
    assert logs.plugin_for_path("/api/plugins/../state") is None
    assert logs.plugin_for_path("/api/conversations/1/chat") is None
