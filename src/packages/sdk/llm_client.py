import copy
import json
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel

from src.packages.shared.config import settings
from src.packages.shared.logging import get_logger

logger = get_logger("akesis.llm_client")
T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Base exception for LLM provider communication errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMAuthError(LLMError):
    """Raised when provider rejects credentials or API key is missing."""

    pass


class LLMRateLimitError(LLMError):
    """Raised when provider returns HTTP 429 rate limit exceeded."""

    pass


class LLMTimeoutError(LLMError):
    """Raised when provider call times out."""

    pass


class LLMResponseValidationError(LLMError):
    """Raised when model response fails schema validation."""

    pass


class LLMClientProtocol(Protocol):
    """Provider-agnostic interface for structured LLM generation."""

    async def generate_structured(
        self,
        prompt: str,
        response_model: type[T],
        system_instruction: str | None = None,
        temperature: float = 0.0,
    ) -> T:
        """Generates structured JSON conforming strictly to response_model."""
        ...


def dereference_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Recursively inlines $defs references into an OpenAPI 3.0-compatible schema for Gemini."""
    schema_copy = copy.deepcopy(schema)
    defs = schema_copy.pop("$defs", {})

    def _resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref_key = str(node["$ref"]).split("/")[-1]
                if ref_key in defs:
                    resolved = _resolve(copy.deepcopy(defs[ref_key]))
                    for k, v in node.items():
                        if k != "$ref":
                            resolved[k] = _resolve(v)
                    return resolved
            return {k: _resolve(v) for k, v in node.items()}
        elif isinstance(node, list):
            return [_resolve(item) for item in node]
        return node

    return _resolve(schema_copy)  # type: ignore[no-any-return]


class GeminiClient:
    """Google Gemini REST API adapter implementing LLMClientProtocol."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.gemini_api_key
        model = model_name or settings.gemini_model
        if model in ("gemini-1.5-flash", "gemini-1.5-pro"):
            model = "gemini-2.5-flash" if "flash" in model else "gemini-2.5-pro"
        self.model_name = model
        self.base_url = (base_url or settings.gemini_api_url).rstrip("/")
        self.timeout = timeout or settings.gemini_timeout_seconds

    async def generate_structured(
        self,
        prompt: str,
        response_model: type[T],
        system_instruction: str | None = None,
        temperature: float = 0.0,
    ) -> T:
        """Calls Gemini REST API with schema enforcement and parses response."""
        if not self.api_key:
            raise LLMAuthError("Gemini API key is not configured (GEMINI_API_KEY missing)")

        url = f"{self.base_url}/models/{self.model_name}:generateContent"
        raw_schema = response_model.model_json_schema()
        response_schema = dereference_json_schema(raw_schema)

        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
                "responseSchema": response_schema,
            },
        }

        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    url,
                    json=payload,
                    headers=headers,
                )

                if response.status_code in (401, 403):
                    raise LLMAuthError(
                        f"Gemini API authentication failed with status {response.status_code}",
                        status_code=response.status_code,
                    )
                if response.status_code == 429:
                    raise LLMRateLimitError(
                        "Gemini API rate limit exceeded (HTTP 429)",
                        status_code=429,
                    )
                response.raise_for_status()

            except httpx.TimeoutException as err:
                raise LLMTimeoutError(
                    f"Gemini API request timed out after {self.timeout}s"
                ) from err
            except httpx.HTTPStatusError as err:
                # Sanitize error message to prevent leaking any credentials
                sanitized_msg = str(err).replace(self.api_key, "[REDACTED]")
                raise LLMError(
                    f"Gemini API HTTP error {err.response.status_code}: {sanitized_msg}",
                    status_code=err.response.status_code,
                ) from err
            except httpx.RequestError as err:
                sanitized_msg = str(err).replace(self.api_key, "[REDACTED]")
                raise LLMError(f"Network error connecting to Gemini API: {sanitized_msg}") from err

        try:
            body = response.json()
            candidates = body.get("candidates", [])
            if not candidates:
                raise LLMResponseValidationError("Gemini returned no response candidates")

            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            if not parts or "text" not in parts[0]:
                raise LLMResponseValidationError("Gemini candidate contains no text parts")

            raw_text = parts[0]["text"]
            parsed_json = json.loads(raw_text)
            return response_model.model_validate(parsed_json)

        except json.JSONDecodeError as err:
            raise LLMResponseValidationError(f"Gemini returned invalid JSON: {err}") from err
        except Exception as err:
            if isinstance(err, LLMError):
                raise
            raise LLMResponseValidationError(
                f"Failed to validate response against schema: {err}"
            ) from err
