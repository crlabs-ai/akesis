# Security Policy & Architecture Standards: Akesis V1

---

## 1. Secrets Management
* **Zero Hardcoding:** No API tokens, webhook secrets, or credentials may be committed.
* **Static Scanning:** CI runs `gitleaks` on all pull requests.
* **Runtime Config:** Load credentials from environment variables (`.env` in local dev).

---

## 2. Docker Sandbox Security Policy
The sandbox executes untrusted AI-generated code under strict isolation:
1. **Non-Root Execution:** Runs as unprivileged user (`uid 1000`).
2. **Resource Limits:** Capped at 512MB RAM and 1.0 CPU core with a 60-second timeout.
3. **Temporary Workspace:** Code executed in ephemeral temporary directories.
4. **Network Policy:**
   * **Default:** Network access is **disabled** (`--network none`).
   * **Controlled Exception:** Dependency installation workflows may explicitly enable network access strictly for downloading packages. Once installation completes, validation commands execute with network disabled.
   * *(Note: Advanced proxy isolation is documented under future scaling).*

---

## 3. Prompt Injection Defense
* Untrusted CI logs are enclosed within explicit XML boundary tags in prompt templates.
* Prompts instruct the model to treat log contents strictly as raw data.
* Output is constrained to strict Pydantic JSON schemas.
