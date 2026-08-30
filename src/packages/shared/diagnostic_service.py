import time

from src.packages.sdk.llm_client import GeminiClient, LLMClientProtocol, LLMError
from src.packages.shared.logging import get_logger
from src.packages.shared.models import (
    DiagnosisProposal,
    DiagnosticResult,
    EvidenceItem,
    FailureCategory,
    FailureContext,
    RemediationDirection,
)
from src.packages.shared.prompt_builder import DiagnosticPromptBuilder

logger = get_logger("akesis.diagnostic_service")


class DiagnosticService:
    """Orchestrates deterministic prompt construction, LLM generation, and schema validation."""

    def __init__(self, llm_client: LLMClientProtocol | None = None) -> None:
        self.llm_client = llm_client or GeminiClient()
        self.prompt_builder = DiagnosticPromptBuilder()

    async def diagnose_failure(self, context: FailureContext) -> DiagnosticResult:
        """Executes single-call diagnosis with schema validation and deterministic fallback."""
        start_time = time.perf_counter()
        logger.info(
            "diagnostic_started",
            incident_id=context.incident_id,
            repo=f"{context.repository_owner}/{context.repository_name}",
            run_id=context.run_id,
            preliminary_category=context.signal.category,
        )

        system_instruction = self.prompt_builder.build_system_instruction()
        user_prompt = self.prompt_builder.build_user_prompt(context)

        try:
            proposal = await self.llm_client.generate_structured(
                prompt=user_prompt,
                response_model=DiagnosisProposal,
                system_instruction=system_instruction,
                temperature=0.0,
            )

            # Enforce bounded confidence score
            clamped_confidence = max(0.0, min(1.0, float(proposal.confidence_score)))
            proposal.confidence_score = round(clamped_confidence, 2)

            duration_ms = (time.perf_counter() - start_time) * 1000.0

            result = DiagnosticResult(
                incident_id=context.incident_id,
                proposal=proposal,
                human_review_required=True,  # Mandatory safety invariant
                model_name=getattr(self.llm_client, "model_name", "llm-provider"),
                execution_time_ms=round(duration_ms, 2),
            )

            logger.info(
                "diagnostic_completed",
                incident_id=context.incident_id,
                category=proposal.category,
                confidence=proposal.confidence_score,
                evidence_sufficiency=proposal.evidence_sufficiency,
                duration_ms=result.execution_time_ms,
            )
            return result

        except LLMError as err:
            logger.error(
                "diagnostic_llm_error",
                incident_id=context.incident_id,
                error=str(err),
                status_code=err.status_code,
            )
            return self._build_fallback_result(
                context, start_time, reason=f"LLM Provider Error: {err}"
            )

        except Exception as err:
            logger.error(
                "diagnostic_unexpected_error",
                incident_id=context.incident_id,
                error=str(err),
            )
            return self._build_fallback_result(
                context, start_time, reason=f"Diagnostic failure: {err}"
            )

    def _build_fallback_result(
        self,
        context: FailureContext,
        start_time: float,
        reason: str,
    ) -> DiagnosticResult:
        """Constructs safe deterministic fallback when LLM fails or is unavailable."""
        duration_ms = (time.perf_counter() - start_time) * 1000.0
        fallback_proposal = DiagnosisProposal(
            category=context.signal.category or FailureCategory.UNKNOWN,
            root_cause=f"Automated diagnosis unavailable. Reason: {reason}",
            evidence=[
                EvidenceItem(
                    source="deterministic_signal",
                    observation=(
                        f"Preliminary error: {context.signal.error_type} - {context.signal.message}"
                    ),
                    file_path=context.signal.target_file,
                    line_number=context.signal.target_line,
                )
            ],
            target_file=context.signal.target_file,
            target_line=context.signal.target_line,
            remediation_direction=RemediationDirection(
                summary="Manual engineer inspection required",
                suggested_action="Review CI failure logs directly to identify the failure cause.",
                risk_assessment="Automated remediation cannot proceed without confident diagnosis.",
            ),
            is_fixable=False,
            confidence_score=0.0,
            evidence_sufficiency="insufficient",
            reasoning="Fallback activated due to provider unavailability or validation failure.",
        )

        return DiagnosticResult(
            incident_id=context.incident_id,
            proposal=fallback_proposal,
            human_review_required=True,
            model_name="deterministic-fallback",
            execution_time_ms=round(duration_ms, 2),
        )
