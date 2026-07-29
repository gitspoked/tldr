"""Integration tests for bounded Ollama and LiteLLM HTTP requests."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from tldreadme.model_client import (
    ModelClient,
    ModelUnavailableError,
    ProviderSettings,
)


@contextmanager
def provider_server(routes):
    """Serve deterministic provider responses on a local HTTP socket."""

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def do_GET(self):
            self._handle()

        def do_POST(self):
            self._handle()

        def _handle(self):
            size = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(size) if size else b""
            parsed = json.loads(body) if body else None
            requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "body": parsed,
                    "authorization": self.headers.get("Authorization"),
                }
            )
            status, payload, delay = routes[self.path]
            if delay:
                time.sleep(delay)
            encoded = json.dumps(payload).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", requests
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def ollama_settings(base_url: str) -> ProviderSettings:
    return ProviderSettings(
        ollama_url=base_url,
        litellm_url="",
        embed_model="ollama/nomic-embed-text",
        chat_model="ollama/qwen2.5-coder:3b-instruct",
        litellm_api_key="",
    )


def test_missing_ollama_model_fails_before_inference_request():
    routes = {
        "/api/tags": (200, {"models": []}, 0),
    }
    with provider_server(routes) as (base_url, requests):
        client = ModelClient(ollama_settings(base_url), timeout_seconds=0.5)

        with pytest.raises(ModelUnavailableError) as captured:
            client.embed(["hello"])

    assert captured.value.status["status"] == "missing"
    assert [request["path"] for request in requests] == ["/api/tags"]


def test_ollama_embedding_uses_native_api_without_a_pull():
    routes = {
        "/api/tags": (
            200,
            {"models": [{"name": "nomic-embed-text:latest"}]},
            0,
        ),
        "/api/embed": (200, {"embeddings": [[0.25, 0.75]]}, 0),
    }
    with provider_server(routes) as (base_url, requests):
        client = ModelClient(ollama_settings(base_url), timeout_seconds=0.5)
        vectors = client.embed(["hello"])

    assert vectors == [[0.25, 0.75]]
    assert requests[-1]["body"] == {
        "model": "nomic-embed-text",
        "input": ["hello"],
    }
    assert all("pull" not in request["path"] for request in requests)


def test_ollama_embedding_splits_large_requests_into_bounded_batches(monkeypatch):
    monkeypatch.setenv("TLDREADME_EMBED_BATCH_SIZE", "128")
    routes = {
        "/api/tags": (
            200,
            {"models": [{"name": "nomic-embed-text:latest"}]},
            0,
        ),
        "/api/embed": (200, {"embeddings": [[0.25, 0.75]] * 128}, 0),
    }
    texts = [f"chunk {index}" for index in range(256)]

    with provider_server(routes) as (base_url, requests):
        client = ModelClient(ollama_settings(base_url), timeout_seconds=0.5)
        vectors = client.embed(texts)

    embed_requests = [request for request in requests if request["path"] == "/api/embed"]
    assert len(vectors) == len(texts)
    assert len(embed_requests) == 2
    assert all(len(request["body"]["input"]) == 128 for request in embed_requests)


def test_slow_ollama_completion_returns_within_wall_clock_deadline():
    routes = {
        "/api/tags": (
            200,
            {"models": [{"name": "qwen2.5-coder:3b-instruct"}]},
            0,
        ),
        "/api/chat": (
            200,
            {"message": {"content": "too late"}},
            0.5,
        ),
    }
    with provider_server(routes) as (base_url, _requests):
        client = ModelClient(ollama_settings(base_url), timeout_seconds=0.1)
        started = time.monotonic()
        with pytest.raises(ModelUnavailableError) as captured:
            client.complete(
                [{"role": "user", "content": "hello"}],
                max_tokens=20,
            )
        elapsed = time.monotonic() - started

    assert elapsed < 0.4
    assert captured.value.status["status"] == "timeout"
    assert "did not start a model download" in str(captured.value)


def test_litellm_proxy_uses_openai_compatible_endpoint_and_auth():
    routes = {
        "/v1/embeddings": (
            200,
            {"data": [{"embedding": [0.1, 0.2]}]},
            0,
        ),
        "/v1/chat/completions": (
            200,
            {"choices": [{"message": {"content": "ready"}}]},
            0,
        ),
    }
    with provider_server(routes) as (base_url, requests):
        settings = ProviderSettings(
            ollama_url="http://127.0.0.1:11434",
            litellm_url=base_url,
            embed_model="embed",
            chat_model="chat",
            litellm_api_key="secret",
        )
        client = ModelClient(settings, timeout_seconds=0.5)

        vectors = client.embed(["hello"])
        completion = client.complete(
            [{"role": "user", "content": "hello"}],
            max_tokens=20,
        )

    assert vectors == [[0.1, 0.2]]
    assert completion == "ready"
    assert [request["path"] for request in requests] == [
        "/v1/embeddings",
        "/v1/chat/completions",
    ]
    assert all(request["authorization"] == "Bearer secret" for request in requests)
