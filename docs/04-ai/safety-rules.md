# AI Safety Rules & Boundaries: Akesis

---

### Rule 1: Zero Host Execution
Generated code is never executed on the host system or control plane. All compilation, execution, and testing occur strictly inside sandboxed Docker/gVisor containers with dropped privileges.

### Rule 2: Schema Validation Gate
Raw model outputs are parsed through strict Pydantic validators before downstream processing. If an LLM returns malformed JSON or unparseable diffs, the response is discarded.

### Rule 3: Secrets & Token Protection
Prompts must never include raw environment variables, API tokens, or user credentials. Automated token sanitizers scrub logs prior to model invocation.

### Rule 4: Mandatory Human Review Gate (V1)
Akesis V1 will never push code directly to user production branches. All patches require human sign-off via Pull Request reviews.

### Rule 5: Fail-Closed Design
If an AI agent encounters unresolvable ambiguity or fails sandbox verification twice, it must terminate execution cleanly, log the diagnostic context, and refrain from speculative PR generation.
