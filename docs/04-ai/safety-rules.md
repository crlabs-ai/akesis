# AI Safety Rules & Boundaries: Akesis V1

---

### Rule 1: Zero Host Execution
Generated code is never executed on the host system. All compilation and testing occur inside sandboxed Docker containers.

### Rule 2: Schema Validation Gate
Model outputs must validate against strict Pydantic schemas before downstream processing.

### Rule 3: Secrets & Token Protection
Logs are scrubbed of secrets prior to being sent to the LLM interface.

### Rule 4: Mandatory Human Review Gate (V1)
Akesis V1 will never push code directly to production branches. All patches require human review via Pull Requests.

### Rule 5: Fail-Closed Design
If an agent encounters ambiguity or fails sandbox verification, it terminates cleanly and logs diagnostic context without opening a PR.
