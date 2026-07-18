# 0020 — V2 scope: AI relevance only; score orders, never filters; embeddings deferred

**Status:** accepted

V2 candidates were: AI relevance (extraction + scoring), embeddings, more ATS adapters
(BambooHR/Workday + a generalized POST/pagination contract), and the openjobdata evaluation.
Building them together couples an LLM feature to source-expansion work that ADR-0017 may make
redundant.

**Decision.**

1. **V2 is AI relevance only**: `int_jobs_structured` (typed extraction) + `int_jobs_scored`
   (fit score) + a score-aware digest. Implementation contract: `docs/v2-plan.md`.
2. **The fit score orders the digest, it does not filter it.** Every new posting is still
   delivered, best-fit first with the score shown. Same posture as ADR-0015: annotate, never
   drop — until the score has earned trust against real deliveries, a threshold would silently
   hide mis-scored postings exactly when the model is least calibrated. Revisit a threshold in
   V3's feedback loop.
3. **Embeddings are deferred out of V2.** Their two stated jobs have no current payoff: as a
   cost pre-filter they save pennies (scoring is already ~$0.002/day, §5.5), and cross-source
   near-duplicate collapse is moot while each company lives on exactly one ATS. Reconsider when
   either premise changes (openjobdata adoption would change both).
4. **Adapters and openjobdata stay parked** behind their ADR-0013/0017 gates, unchanged.
5. **The candidate profile is private config like the company list** (ADR-0011):
   `config/profile.yaml` (gitignored) with a committed `config/profile.example.yaml`; a typed
   loader renders the deterministic, versioned prompt block the scoring model consumes.
