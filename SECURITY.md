# Security Policy

This document defines the security parameters, reporting protocols, and secrets compliance guidelines for Akesis.

---

## Reporting Vulnerabilities

If you discover a security vulnerability in this repository, do not open a public issue. Instead, report it privately to our security team:

*   **Email:** security@crlabs.ai
*   **Expectation:** We will acknowledge receipt of your report within 24 hours and provide status updates throughout the remediation process.

---

## Secrets Compliance

*   **Zero Local Storage:** No API keys, credentials, database passwords, or certificates may be committed to this repository.
*   **Static Scanning:** Automated checks will block commits containing signature keys or credentials.
*   **Runtime Config:** Use secure environment variables managed by vaults or container variables.

---
*Return to [README](README.md)*
