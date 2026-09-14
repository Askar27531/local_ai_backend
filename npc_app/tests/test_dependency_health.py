from npc_app import api


def test_dependency_health_reports_all_dependencies(monkeypatch):
    monkeypatch.setattr(api, "_check_database", lambda: {})
    monkeypatch.setattr(api, "_check_ollama", lambda: {"model": "test-model"})
    monkeypatch.setattr(api, "_check_milvus", lambda: {"collection": "test-chunks"})
    monkeypatch.setattr(
        api,
        "_check_memory_milvus",
        lambda: {"collection": "test-memory", "vector_dim": 384},
    )

    result = api.check_runtime_dependencies()

    assert result == {
        "database": {"status": "ok"},
        "ollama": {"status": "ok", "model": "test-model"},
        "milvus": {"status": "ok", "collection": "test-chunks"},
        "memory_milvus": {"status": "ok", "collection": "test-memory", "vector_dim": 384},
    }


def test_dependency_health_returns_actionable_hint(monkeypatch):
    def fail():
        raise ConnectionError("internal connection detail")

    monkeypatch.setattr(api, "_check_database", lambda: {})
    monkeypatch.setattr(api, "_check_ollama", fail)
    monkeypatch.setattr(api, "_check_milvus", lambda: {"collection": "test-chunks"})
    monkeypatch.setattr(
        api,
        "_check_memory_milvus",
        lambda: {"collection": "test-memory", "vector_dim": 384},
    )

    result = api.check_runtime_dependencies()

    assert result["ollama"]["status"] == "error"
    assert result["ollama"]["error_type"] == "ConnectionError"
    assert "Ollama" in result["ollama"]["hint"]
    assert "internal connection detail" not in str(result)


def test_milvus_probe_fails_before_client_creation(monkeypatch):
    def fail_fast(_uri):
        raise ConnectionRefusedError

    monkeypatch.setattr(api, "_check_tcp_endpoint", fail_fast)
    monkeypatch.setattr(
        api,
        "get_milvus_client",
        lambda: (_ for _ in ()).throw(AssertionError("client should not be created")),
    )

    try:
        api._check_milvus()
    except ConnectionRefusedError:
        pass
    else:
        raise AssertionError("expected the TCP preflight to fail")
