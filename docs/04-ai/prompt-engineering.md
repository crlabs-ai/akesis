# Prompt Engineering Standards: Akesis V1

---

## 1. Prompt Architecture Standards
* **Version Controlled:** Prompts are stored as templates or constants under `src/packages/agent_runtime/prompts/`.
* **Clear Objective:** Prompt begins with an unambiguous system role and task constraints.
* **Schema Enforcement:** Strict Pydantic JSON schema validation on model responses.
* **Delimited Context:** Log snippets and code context enclosed in XML tags (`<error_log>`, `<code_context>`).
* **Negative Directives:** Explicit instructions prohibiting modification of unrelated lines.

---

## 2. Core Diagnostic Prompt Structure (Template Example)

```text
[SYSTEM]
You are the Akesis CI Diagnostic Engine. Analyze the failure log, locate the failing code, and synthesize a minimal unified git diff resolving the error.

Constraints:
1. Ground your diagnosis strictly in the <error_log>.
2. Generate only the minimal diff required to fix the failure.
3. Output strictly valid JSON matching the schema.

[USER]
<error_log>
{sanitized_log_snippet}
</error_log>

<code_context file="{target_file}">
{code_snippet}
</code_context>
```
