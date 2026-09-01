import httpx
import pytest
import respx

from src.packages.sdk.llm_client import (
    GeminiClient,
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMResponseValidationError,
    LLMTimeoutError,
    dereference_json_schema,
)
from src.packages.shared.models import DiagnosisProposal, FailureCategory


@pytest.mark.asyncio
async def test_gemini_client_missing_api_key_raises_auth_error() -> None:
    client = GeminiClient(api_key="", base_url="https://generativelanguage.googleapis.com/v1beta")
    with pytest.raises(LLMAuthError) as exc_info:
        await client.generate_structured("prompt", DiagnosisProposal)
    assert "GEMINI_API_KEY missing" in str(exc_info.value)


@pytest.mark.asyncio
async def test_gemini_client_successful_structured_generation() -> None:
    client = GeminiClient(
        api_key="mock_key",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    mock_payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": (
                                '{"category": "test", "root_cause": "Assertion failed", '
                                '"evidence": [{"source": "log", "observation": "assert 1 == 2"}], '
                                '"target_file": "tests/test_foo.py", "target_line": 12, '
                                '"remediation_direction": {"summary": "fix", '
                                '"suggested_action": "fix", "risk_assessment": "low"}, '
                                '"is_fixable": true, "confidence_score": 0.9, '
                                '"evidence_sufficiency": "sufficient", '
                                '"reasoning": "log clearly indicates assert error"}'
                            )
                        }
                    ]
                }
            }
        ]
    }

    with respx.mock(base_url="https://generativelanguage.googleapis.com/v1beta") as respx_mock:
        respx_mock.post(
            f"/models/{client.model_name}:generateContent",
            headers={"x-goog-api-key": "mock_key"},
        ).respond(
            status_code=200,
            json=mock_payload,
        )

        proposal = await client.generate_structured(
            prompt="Diagnose this failure",
            response_model=DiagnosisProposal,
            system_instruction="You are a diagnostician",
        )
        assert proposal.category == FailureCategory.TEST
        assert proposal.confidence_score == 0.9
        assert proposal.target_file == "tests/test_foo.py"


@pytest.mark.asyncio
async def test_gemini_client_rate_limit_handled() -> None:
    client = GeminiClient(
        api_key="mock_key",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    with respx.mock(base_url="https://generativelanguage.googleapis.com/v1beta") as respx_mock:
        respx_mock.post(
            f"/models/{client.model_name}:generateContent",
            headers={"x-goog-api-key": "mock_key"},
        ).respond(
            status_code=429,
            json={"error": {"message": "Resource has been exhausted"}},
        )
        with pytest.raises(LLMRateLimitError):
            await client.generate_structured("prompt", DiagnosisProposal)


@pytest.mark.asyncio
async def test_gemini_client_auth_failure_handled() -> None:
    client = GeminiClient(
        api_key="invalid_key",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    with respx.mock(base_url="https://generativelanguage.googleapis.com/v1beta") as respx_mock:
        respx_mock.post(
            f"/models/{client.model_name}:generateContent",
            headers={"x-goog-api-key": "invalid_key"},
        ).respond(
            status_code=401,
            json={"error": {"message": "API key not valid"}},
        )
        with pytest.raises(LLMAuthError):
            await client.generate_structured("prompt", DiagnosisProposal)


@pytest.mark.asyncio
async def test_gemini_client_empty_candidates_handled() -> None:
    client = GeminiClient(
        api_key="mock_key",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    with respx.mock(base_url="https://generativelanguage.googleapis.com/v1beta") as respx_mock:
        respx_mock.post(
            f"/models/{client.model_name}:generateContent",
            headers={"x-goog-api-key": "mock_key"},
        ).respond(
            status_code=200,
            json={"candidates": []},
        )
        with pytest.raises(LLMResponseValidationError) as exc_info:
            await client.generate_structured("prompt", DiagnosisProposal)
        assert "no response candidates" in str(exc_info.value)


@pytest.mark.asyncio
async def test_gemini_client_empty_parts_handled() -> None:
    client = GeminiClient(
        api_key="mock_key",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    with respx.mock(base_url="https://generativelanguage.googleapis.com/v1beta") as respx_mock:
        respx_mock.post(
            f"/models/{client.model_name}:generateContent",
            headers={"x-goog-api-key": "mock_key"},
        ).respond(
            status_code=200,
            json={"candidates": [{"content": {"parts": []}}]},
        )
        with pytest.raises(LLMResponseValidationError) as exc_info:
            await client.generate_structured("prompt", DiagnosisProposal)
        assert "no text parts" in str(exc_info.value)


@pytest.mark.asyncio
async def test_gemini_client_malformed_json_raises_validation_error() -> None:
    client = GeminiClient(
        api_key="mock_key",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    with respx.mock(base_url="https://generativelanguage.googleapis.com/v1beta") as respx_mock:
        respx_mock.post(
            f"/models/{client.model_name}:generateContent",
            headers={"x-goog-api-key": "mock_key"},
        ).respond(
            status_code=200,
            json={"candidates": [{"content": {"parts": [{"text": "not valid json {"}]}}]},
        )
        with pytest.raises(LLMResponseValidationError):
            await client.generate_structured("prompt", DiagnosisProposal)


@pytest.mark.asyncio
async def test_gemini_client_timeout_error_handled() -> None:
    client = GeminiClient(
        api_key="mock_key",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        timeout=0.01,
    )
    with respx.mock(base_url="https://generativelanguage.googleapis.com/v1beta") as respx_mock:
        respx_mock.post(
            f"/models/{client.model_name}:generateContent",
            headers={"x-goog-api-key": "mock_key"},
        ).mock(side_effect=httpx.TimeoutException("Timeout"))
        with pytest.raises(LLMTimeoutError):
            await client.generate_structured("prompt", DiagnosisProposal)


@pytest.mark.asyncio
async def test_gemini_client_server_error_handled() -> None:
    client = GeminiClient(
        api_key="mock_key",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    with respx.mock(base_url="https://generativelanguage.googleapis.com/v1beta") as respx_mock:
        respx_mock.post(
            f"/models/{client.model_name}:generateContent",
            headers={"x-goog-api-key": "mock_key"},
        ).respond(
            status_code=503,
            json={"error": {"message": "Service unavailable"}},
        )
        with pytest.raises(LLMError) as exc_info:
            await client.generate_structured("prompt", DiagnosisProposal)
        assert exc_info.value.status_code == 503


def test_dereference_json_schema_inlines_defs() -> None:
    schema = {
        "$defs": {
            "StatusEnum": {
                "enum": ["pending", "active"],
                "type": "string",
            }
        },
        "properties": {"status": {"$ref": "#/$defs/StatusEnum"}},
        "type": "object",
    }
    resolved = dereference_json_schema(schema)
    assert "$defs" not in resolved
    assert "$ref" not in resolved["properties"]["status"]
    assert resolved["properties"]["status"]["enum"] == ["pending", "active"]
    assert resolved["properties"]["status"]["type"] == "string"
