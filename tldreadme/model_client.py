"""Bounded HTTP client for Ollama and LiteLLM proxy inference."""

from __future__ import annotations

import os
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Thread
from typing import Callable, TypeVar, cast

from .config import get_setting
from .lazy import load_module

T = TypeVar("T")


class ModelUnavailableError(RuntimeError):
    """Report a model readiness or request failure to callers."""

    def __init__(self, message: str, *, status: dict[str, object]):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ProviderSettings:
    """Resolved model provider settings."""

    ollama_url: str
    litellm_url: str
    embed_model: str
    chat_model: str
    litellm_api_key: str

    @classmethod
    def load(cls) -> "ProviderSettings":
        """Resolve the current environment and durable configuration."""

        return cls(
            ollama_url=get_setting("OLLAMA_URL").rstrip("/"),
            litellm_url=get_setting("LITELLM_URL").rstrip("/"),
            embed_model=get_setting("TLDREADME_EMBED_MODEL"),
            chat_model=get_setting("TLDREADME_CHAT_MODEL"),
            litellm_api_key=(
                os.getenv("LITELLM_API_KEY", "").strip()
                or os.getenv("LITELLM_MASTER_KEY", "").strip()
            ),
        )

    @property
    def provider(self) -> str:
        """Return the active provider name."""

        return "litellm" if self.litellm_url else "ollama"


def model_timeout_seconds() -> float:
    """Return the wall-clock limit for a provider request."""

    raw = os.getenv("TLDREADME_MODEL_TIMEOUT_SECONDS", "15")
    try:
        return max(0.1, float(raw))
    except ValueError:
        return 15.0


def ollama_embed_batch_size() -> int:
    """Return a conservative Ollama batch size that avoids tokenizer crashes."""

    raw = os.getenv("TLDREADME_EMBED_BATCH_SIZE", "32")
    try:
        return max(1, min(128, int(raw)))
    except ValueError:
        return 32


def _run_with_deadline(operation: Callable[[], T], timeout_seconds: float) -> T:
    """Run a blocking provider request with a hard wall-clock deadline."""

    result: Queue[tuple[bool, object]] = Queue(maxsize=1)

    def run() -> None:
        try:
            result.put((True, operation()))
        except Exception as exc:
            result.put((False, exc))

    Thread(target=run, name="tldreadme-model-request", daemon=True).start()
    try:
        succeeded, value = result.get(timeout=timeout_seconds)
    except Empty as exc:
        raise TimeoutError(f"provider request exceeded {timeout_seconds:g} seconds") from exc
    if succeeded:
        return cast(T, value)
    if isinstance(value, BaseException):
        raise value
    raise RuntimeError("provider request failed without an exception")


def _normalized_model_name(name: str) -> str:
    """Normalize Ollama's implicit latest tag for comparison."""

    return name[:-7] if name.endswith(":latest") else name


def _ollama_model_name(model: str) -> str:
    """Return the model name expected by Ollama's native API."""

    return model.removeprefix("ollama/").strip()


class ModelClient:
    """Make non-streaming model requests without implicit downloads."""

    def __init__(
        self,
        settings: ProviderSettings | None = None,
        *,
        timeout_seconds: float | None = None,
    ):
        self.settings = settings or ProviderSettings.load()
        self.timeout_seconds = (
            model_timeout_seconds() if timeout_seconds is None else max(0.1, timeout_seconds)
        )

    def _endpoint(self, path: str) -> str:
        """Build a provider endpoint."""

        if self.settings.provider == "ollama":
            return self.settings.ollama_url + path
        base = self.settings.litellm_url
        if base.endswith("/v1"):
            return base + path.removeprefix("/v1")
        return base + path

    def _headers(self) -> dict[str, str]:
        """Return proxy authentication headers when configured."""

        if not self.settings.litellm_api_key:
            return {}
        return {"Authorization": f"Bearer {self.settings.litellm_api_key}"}

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        """Run one JSON request with transport and wall-clock timeouts."""

        timeout = timeout_seconds or self.timeout_seconds
        httpx = load_module("httpx")

        def request() -> dict[str, object]:
            with httpx.Client(
                headers=self._headers(),
                timeout=httpx.Timeout(
                    timeout,
                    connect=min(timeout, 2.0),
                    pool=min(timeout, 2.0),
                ),
                trust_env=False,
            ) as client:
                response = client.request(
                    method,
                    self._endpoint(path),
                    json=payload,
                )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("provider returned a non-object JSON response")
            return data

        return _run_with_deadline(request, timeout)

    def _error(
        self,
        model: str,
        exc: Exception,
        *,
        operation: str,
        timeout_seconds: float | None = None,
    ) -> ModelUnavailableError:
        """Normalize provider failures into a router-readable error."""

        deadline = timeout_seconds or self.timeout_seconds
        error_name = type(exc).__name__
        error_text = str(exc).strip().replace("\n", " ")[:300]
        timed_out = isinstance(exc, TimeoutError) or "timeout" in error_name.lower()
        status_name = "timeout" if timed_out else "request_failed"
        if timed_out:
            reason = (
                f"Model `{model}` did not respond within "
                f"{deadline:g} seconds during {operation}. "
                "The provider may still be loading or downloading it. "
                "TLDREADME did not start a model download."
            )
        else:
            reason = f"Model `{model}` {operation} failed ({error_name})"
            if error_text:
                reason += f": {error_text}"
        return ModelUnavailableError(
            reason,
            status={
                "status": status_name,
                "available": False,
                "provider": self.settings.provider,
                "model": model,
                "operation": operation,
                "timeout_seconds": deadline,
                "reason": reason,
            },
        )

    def model_status(self, model: str) -> dict[str, object]:
        """Check exact Ollama readiness without triggering a pull."""

        if self.settings.provider == "litellm":
            return {
                "status": "remote",
                "available": None,
                "provider": "litellm",
                "model": model,
                "reason": "LiteLLM model availability is checked at request time.",
            }

        ollama_name = _ollama_model_name(model)
        catalog_timeout = min(self.timeout_seconds, 1.0)
        try:
            payload = self._request_json(
                "GET",
                "/api/tags",
                timeout_seconds=catalog_timeout,
            )
        except Exception as exc:
            error = self._error(
                model,
                exc,
                operation="catalog check",
                timeout_seconds=catalog_timeout,
            )
            return error.status

        models = payload.get("models", [])
        if not isinstance(models, list):
            return {
                "status": "invalid_response",
                "available": False,
                "provider": "ollama",
                "model": ollama_name,
                "reason": "Ollama returned an invalid model catalog.",
            }
        installed = {
            _normalized_model_name(str(item.get("name") or item.get("model") or ""))
            for item in models
            if isinstance(item, dict)
        }
        available = _normalized_model_name(ollama_name) in installed
        if available:
            reason = f"Ollama model `{ollama_name}` is installed."
            status = "ready"
        else:
            reason = (
                f"Ollama model `{ollama_name}` is not installed. "
                f"Run `ollama pull {ollama_name}` explicitly to enable this capability."
            )
            status = "missing"
        return {
            "status": status,
            "available": available,
            "provider": "ollama",
            "model": ollama_name,
            "reason": reason,
        }

    def ensure_model_available(self, model: str) -> dict[str, object]:
        """Reject a missing or unreachable local model before inference."""

        status = self.model_status(model)
        if status["status"] in {"ready", "remote"}:
            return status
        raise ModelUnavailableError(str(status["reason"]), status=status)

    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        """Embed one or more texts with the configured provider."""

        if not texts:
            return []
        selected_model = model or self.settings.embed_model
        self.ensure_model_available(selected_model)
        try:
            if self.settings.provider == "ollama":
                vectors: list[list[float]] = []
                batch_size = ollama_embed_batch_size()
                for start in range(0, len(texts), batch_size):
                    batch = texts[start : start + batch_size]
                    payload = self._request_json(
                        "POST",
                        "/api/embed",
                        payload={
                            "model": _ollama_model_name(selected_model),
                            "input": batch,
                        },
                    )
                    batch_vectors = payload["embeddings"]
                    if not isinstance(batch_vectors, list) or len(batch_vectors) != len(batch):
                        raise ValueError("provider returned an unexpected embedding count")
                    vectors.extend(batch_vectors)
            else:
                payload = self._request_json(
                    "POST",
                    "/v1/embeddings",
                    payload={"model": selected_model, "input": texts},
                )
                data = payload["data"]
                if not isinstance(data, list):
                    raise ValueError("LiteLLM returned invalid embedding data")
                vectors = [item["embedding"] for item in data]
            if not isinstance(vectors, list) or len(vectors) != len(texts):
                raise ValueError("provider returned an unexpected embedding count")
            return [[float(value) for value in vector] for vector in vectors]
        except ModelUnavailableError:
            raise
        except Exception as exc:
            raise self._error(selected_model, exc, operation="embedding") from exc

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        model: str | None = None,
    ) -> str:
        """Return a non-streaming chat completion."""

        selected_model = model or self.settings.chat_model
        self.ensure_model_available(selected_model)
        if self.settings.provider == "ollama":
            path = "/api/chat"
            request = {
                "model": _ollama_model_name(selected_model),
                "messages": messages,
                "stream": False,
                "options": {"num_predict": max_tokens},
            }
        else:
            path = "/v1/chat/completions"
            request = {
                "model": selected_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "stream": False,
            }
        try:
            payload = self._request_json("POST", path, payload=request)
            if self.settings.provider == "ollama":
                message = payload["message"]
            else:
                choices = payload["choices"]
                if not isinstance(choices, list) or not choices:
                    raise ValueError("LiteLLM returned no completion choices")
                message = choices[0]["message"]
            content = message["content"]
            if not isinstance(content, str):
                raise ValueError("provider returned a non-text completion")
            return content
        except ModelUnavailableError:
            raise
        except Exception as exc:
            raise self._error(selected_model, exc, operation="completion") from exc
