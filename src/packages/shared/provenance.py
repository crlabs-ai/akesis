"""Fail-closed hold until authoritative repository provenance is implemented."""


class UnverifiedProvenanceError(RuntimeError):
    """Repository operations are unavailable without independently verified provenance."""


def require_verified_provenance() -> None:
    """Deny remediation: no current incident carries independently verified provenance."""
    raise UnverifiedProvenanceError("Repository provenance is not independently verified")
