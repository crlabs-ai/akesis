# Coding Standards: Python Guidelines

---

## 1. Style & Formatting
*   **Language Version:** Python 3.12+
*   **Formatter:** Black (line length 100)
*   **Linter:** Ruff (enforcing flake8, isort, and bandit security rules)
*   **Type Checker:** `mypy --strict`

---

## 2. Code Structure Conventions

### Explicit Imports
Always use absolute imports from the repository root:
```python
# CORRECT
from src.packages.shared.models import IncidentEvent
from src.packages.agent_runtime.coordinator import AgentCoordinator

# INCORRECT
from ..models import *
```

### Exception Handling
Never use bare `except:`. Always catch specific exception classes and re-raise as domain-specific errors:
```python
# CORRECT
try:
    result = await sandbox.execute(command)
except SandboxTimeoutError as err:
    logger.error("Sandbox execution timed out", extra={"error": str(err)})
    raise RemediationFailureError("Validation timed out") from err

# INCORRECT
try:
    result = sandbox.execute(command)
except:
    pass
```

### Pydantic Models for Data Validation
All boundary data crossing HTTP, gRPC, database, or LLM interfaces must be defined as immutable Pydantic V2 models:
```python
from pydantic import BaseModel, Field

class PatchDiagnosis(BaseModel):
    category: str = Field(..., description="Failure category")
    root_cause: str = Field(..., description="Detailed explanation of error")
    patch_diff: str = Field(..., description="Unified git diff")
    confidence_score: float = Field(..., ge=0.0, le=1.0)
```
