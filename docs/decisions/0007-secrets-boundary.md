# 0007 — Secrets boundary independent of agent goodwill

**Status:** accepted

Secret values never live in the working tree. Prod secrets live only in GitHub Actions
Encrypted Secrets; local creds live in the OS keychain / gcloud ADC. BigQuery auth uses
Workload Identity Federation (no key file). gitleaks pre-commit is a backstop. Agents
don't push; a human authenticates. CLAUDE.md states the rule, but enforcement is structural.
