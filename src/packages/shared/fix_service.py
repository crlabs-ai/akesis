import time
from pathlib import Path

from src.packages.sdk.llm_client import GeminiClient, LLMClientProtocol, LLMError
from src.packages.shared.config import settings
from src.packages.shared.fix_prompt_builder import FixPromptBuilder
from src.packages.shared.logging import get_logger
from src.packages.shared.models import (
    DiagnosticResult,
    EvidencePackage,
    FailureContext,
    FixProposal,
    RawFixProposal,
)
from src.packages.shared.patch_validator import PatchValidator

logger = get_logger("akesis.fix_service")


class FixProposalService:
    """Orchestrates deterministic fix generation, schema enforcement, and patch validation."""

    def __init__(
        self,
        llm_client: LLMClientProtocol | None = None,
        patch_validator: PatchValidator | None = None,
        min_confidence: float | None = None,
    ) -> None:
        self.llm_client = llm_client or GeminiClient()
        self.patch_validator = patch_validator or PatchValidator()
        self.min_confidence = min_confidence or settings.min_fix_confidence_threshold
        self.prompt_builder = FixPromptBuilder()

    async def generate_fix_proposal(
        self,
        context: FailureContext,
        diagnostic_result: DiagnosticResult,
        evidence_package: EvidencePackage | None = None,
        repo_root: Path | None = None,
    ) -> FixProposal:
        """Generates, parses, and deterministically validates a fix proposal."""
        start_time = time.perf_counter()
        incident_id = context.incident_id
        commit_sha = context.commit_sha
        proposal_id = f"fix_{incident_id}_{commit_sha[:8]}"

        logger.info(
            "fix_generation_started",
            incident_id=incident_id,
            commit_sha=commit_sha,
            diag_category=diagnostic_result.proposal.category,
            diag_confidence=diagnostic_result.proposal.confidence_score,
            is_fixable=diagnostic_result.proposal.is_fixable,
        )

        # 1. Eligibility Check: Verify diagnostic confidence & fixability
        diag_proposal = diagnostic_result.proposal
        if not diag_proposal.is_fixable or diag_proposal.confidence_score < self.min_confidence:
            reason = (
                f"Ineligible for automated fix: is_fixable={diag_proposal.is_fixable}, "
                f"confidence={diag_proposal.confidence_score:.2f} (min {self.min_confidence:.2f})"
            )
            logger.info("fix_generation_ineligible", incident_id=incident_id, reason=reason)
            return FixProposal(
                proposal_id=proposal_id,
                incident_id=incident_id,
                diagnosis_id=diagnostic_result.incident_id,
                commit_sha=commit_sha,
                status="ineligible",
                is_valid=False,
                rejection_reasons=[reason],
                unified_diff="",
                file_patches=[],
                target_files=[],
                rationale="Automated fix withheld due to low confidence or unfixable diagnosis.",
                assumptions=[],
                risk_level="low",
                has_dependency_changes=False,
                confidence_score=diag_proposal.confidence_score,
            )

        # 2. Build Fix Prompt
        system_instruction = self.prompt_builder.build_system_instruction()
        user_prompt = self.prompt_builder.build_user_prompt(
            context=context,
            diagnostic_result=diagnostic_result,
            evidence_package=evidence_package,
        )

        # 3. Call LLM for Structured Fix Proposal
        try:
            raw_proposal = await self.llm_client.generate_structured(
                prompt=user_prompt,
                response_model=RawFixProposal,
                system_instruction=system_instruction,
                temperature=0.0,
            )
        except LLMError as err:
            logger.error(
                "fix_generation_llm_error",
                incident_id=incident_id,
                error=str(err),
            )
            return self._build_rejected_proposal(
                proposal_id, incident_id, commit_sha, f"LLM Provider Error: {err}"
            )
        except Exception as err:
            logger.error(
                "fix_generation_unexpected_error",
                incident_id=incident_id,
                error=str(err),
            )
            return self._build_rejected_proposal(
                proposal_id, incident_id, commit_sha, f"Fix generation error: {err}"
            )

        # 4. Deterministic Patch Validation
        validation = self.patch_validator.validate_patch(
            raw_diff=raw_proposal.unified_diff,
            claimed_target_files=raw_proposal.target_files,
            evidence_package=evidence_package,
            diagnostic_result=diagnostic_result,
            repo_root=repo_root,
        )

        status: str = "proposed" if validation.is_valid else "rejected"
        clamped_confidence = max(0.0, min(1.0, float(raw_proposal.confidence_score)))
        duration_ms = (time.perf_counter() - start_time) * 1000.0

        fix_proposal = FixProposal(
            proposal_id=proposal_id,
            incident_id=incident_id,
            diagnosis_id=diagnostic_result.incident_id,
            commit_sha=commit_sha,
            status=status,  # type: ignore[arg-type]
            is_valid=validation.is_valid,
            rejection_reasons=validation.rejection_reasons,
            unified_diff=raw_proposal.unified_diff if validation.is_valid else "",
            file_patches=validation.file_patches,
            target_files=validation.target_files,
            rationale=raw_proposal.explanation,
            assumptions=raw_proposal.assumptions,
            risk_level=validation.risk_level,  # type: ignore[arg-type]
            has_dependency_changes=validation.has_dependency_changes,
            confidence_score=round(clamped_confidence, 2),
        )

        logger.info(
            "fix_proposal_created",
            proposal_id=proposal_id,
            status=status,
            is_valid=validation.is_valid,
            target_files=validation.target_files,
            risk_level=validation.risk_level,
            duration_ms=round(duration_ms, 2),
        )
        return fix_proposal

    def _build_rejected_proposal(
        self,
        proposal_id: str,
        incident_id: str,
        commit_sha: str,
        reason: str,
    ) -> FixProposal:
        """Constructs a safe rejected FixProposal on provider failure."""
        return FixProposal(
            proposal_id=proposal_id,
            incident_id=incident_id,
            diagnosis_id=incident_id,
            commit_sha=commit_sha,
            status="rejected",
            is_valid=False,
            rejection_reasons=[reason],
            unified_diff="",
            file_patches=[],
            target_files=[],
            rationale="Fix proposal generation failed due to service or provider error.",
            assumptions=[],
            risk_level="high",
            has_dependency_changes=False,
            confidence_score=0.0,
        )
