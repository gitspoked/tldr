"""Tests for lazy dependency loaders."""

from tldreadme import _shared, lazy


def test_load_module_is_cached():
    first = lazy.load_module("json")
    second = lazy.load_module("json")

    assert first is second


def test_load_attr_is_cached():
    first = lazy.load_attr("json", "loads")
    second = lazy.load_attr("json", "loads")

    assert first is second


def test_shared_backends_load_on_demand(monkeypatch):
    created = []

    class FakeEmbedder:
        def __init__(self):
            created.append("embedder")

    class FakeGrapher:
        def __init__(self):
            created.append("grapher")

    def fake_load_attr(module_name, attr_name):
        if module_name == "tldreadme.embedder" and attr_name == "CodeEmbedder":
            return FakeEmbedder
        if module_name == "tldreadme.grapher" and attr_name == "CodeGrapher":
            return FakeGrapher
        raise AssertionError(f"unexpected load {module_name}.{attr_name}")

    monkeypatch.setattr(_shared, "load_attr", fake_load_attr)
    monkeypatch.setattr(_shared, "_embedder", None)
    monkeypatch.setattr(_shared, "_grapher", None)

    assert created == []
    _shared.get_embedder()
    _shared.get_grapher()

    assert created == ["embedder", "grapher"]
