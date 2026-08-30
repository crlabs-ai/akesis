# Coding Standards: Python Guidelines (Python 3.12+ / uv)

---

## 1. Tooling & Execution Standards
* **Python Version:** Python 3.12+
* **Environment Manager:** `uv`
* **Formatter:** `uv run ruff format .` (or `black`)
* **Linter:** `uv run ruff check .`
* **Type Checker:** `uv run mypy .`

---

## 2. Code Style Conventions

### Explicit Absolute Imports
```python
# CORRECT
from src.packages.shared.models import IncidentEvent
from src.packages.agent_runtime.coordinator import AgentCoordinator

# INCORRECT
from ..models import *
```

### Exception Handling
Never use bare `except:`. Catch specific exceptions and wrap in domain errors:
```python
# CORRECT
try:
    result = await sandbox.execute(command)
except SandboxTimeoutError as err:
    logger.error("Sandbox execution timed out", incident_id=incident_id, error=str(err))
    raise RemediationFailureError("Validation timed out") from err
```

### Pydantic Models for All Boundary Data
```python
from pydantic import BaseModel, Field

class PatchDiagnosis(BaseModel):
    category: str = Field(..., description="Failure category")
    root_cause: str = Field(..., description="Explanation of error")
    patch_diff: str = Field(..., description="Unified git diff")
    confidence_score: float = Field(..., ge=0.0, le=1.0)
```
