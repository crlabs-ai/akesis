# ADR-0002: Gemini AI Diagnostic Baseline & Provider-Agnostic Interface

## Status
Accepted (Phase 2)

## Context
Akesis requires a structured AI diagnosis layer to analyze CI/CD failure logs and context extracted in Phase 1. 
To avoid vendor lock-in while leveraging Gemini's structured output capabilities, the architecture must remain provider-agnostic at the domain layer.

## Decision
1. **Provider Abstraction (`LLMClientProtocol`):** Define a clean async protocol accepting prompts and Pydantic response models.
2. **Gemini Adapter (`GeminiClient`):** Implement the protocol using Google Gemini REST API with schema-constrained JSON generation.
3. **Evidence-First Prompt Architecture:** Require verifiable facts from `FailureContext` before generating root causes.
4. **Mandatory Human Control:** Set `human_review_required = True` on all diagnostic results in V1.
5. **Deterministic Fallback:** Return structured fallback diagnostics with 0.0 confidence when LLM communication fails.

## Consequences
* The domain layer has zero dependency on vendor-specific LLM SDKs.
* Diagnostics are strongly typed and guaranteed to conform to `DiagnosisProposal`.
* Tests execute deterministically without making live external API calls.
