# Prompt Engineering Standards: Akesis

---

## 1. Prompt Architecture Standards
All prompts utilized by the Akesis runtime must comply with these engineering rules:
*   **Version Controlled:** Prompts are stored as template files or Python constants under `src/packages/agent_runtime/prompts/` and tracked in Git.
*   **Clear Objective:** Every prompt begins with an unambiguous system instruction defining role, task, and constraints.
*   **Schema Enforcement:** Prompts must enforce structured JSON output using Pydantic / JSON Schema definitions.
*   **Delimited Context:** Log extracts and code ASTs must be enclosed in explicit XML tags (e.g., `<error_log>...</error_log>`, `<code_context>...</code_context>`).
*   **Negative Directives:** Explicitly instruct the model what NOT to do (e.g., *"Do not modify files outside the error trace. Do not reformat unrelated code."*).

---

## 2. Core Diagnostic Prompt Structure (Template Example)

```text
[SYSTEM]
You are the Akesis CI Diagnostic Engine. Your task is to analyze continuous integration failure logs, locate the failing code, and synthesize a minimal unified git diff that resolves the issue.

Constraints:
1. Ground your diagnosis strictly in the provided <error_log>.
2. Generate only the minimal diff required to fix the failure.
3. Emit output strictly matching the provided JSON schema.

[USER]
<error_log>
{sanitized_log_snippet}
</error_log>

<code_context file="{target_file}">
{ast_code_snippet}
</code_context>
```
