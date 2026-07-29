"""Tests for FalkorDB connection configuration."""

from tldreadme import grapher


def test_grapher_parses_configured_redis_url(monkeypatch):
    captured = {}

    class FakeGraph:
        def query(self, *_args, **_kwargs):
            return None

    class FakeFalkorDB:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def select_graph(self, name):
            assert name == "tldreadme"
            return FakeGraph()

    monkeypatch.setattr(grapher, "load_attr", lambda *_args: FakeFalkorDB)

    grapher.CodeGrapher("rediss://user:secret@graph.example:6397")

    assert captured == {
        "host": "graph.example",
        "port": 6397,
        "ssl": True,
        "username": "user",
        "password": "secret",
    }
