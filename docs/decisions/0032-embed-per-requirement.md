# 0032 — Embed a posting per requirement, and store vectors apart from scores

**Status:** accepted (refines ADR-0027 §2)

ADR-0027 split the resume into bullets so that a posting could match the relevant part of a work
history instead of an average of all of it. The first matcher applied that to one side only: each
bullet was its own vector, and the posting was a single vector over its whole `relevant_text`.

**That comparison is lopsided in a way that biases the ranking.** A posting's vector is an average
of every topic it names, and it was compared with texts a tenth of its length. Taking the nearest
bullet then rewards a posting for resembling *one thing* the candidate did: a narrow role built
around a single bullet outranks a broad role that most of the resume answers moderately well. It
also cannot say what in the posting matched.

**Decision.**

1. **The posting is split the way the resume is.** Extraction lists its requirements, nice-to-haves
   and responsibilities one sentence each, plus one sentence on the employer
   (`int_jobs_structured`, unnested by `int_posting_units`). "Nice-to-have" is defined by how the
   posting qualifies an item, not by a heading, because that section has no standard name.
2. **`similarity` is a mean of per-sentence best matches.** Each sentence keeps its single most
   similar work bullet; the posting's score is the weighted mean of those, a nice-to-have counting
   for `embedding_nice_to_have_weight` of a requirement. This measures coverage: a sentence nothing
   in the resume answers lowers the score, where under nearest-bullet it was invisible.
3. **Industry is a separate number.** The employer sentence is compared with the industries the
   resume names and reported as `industry_similarity`. Blended into `similarity`, a high score
   could mean the work fits or only the sector does, and `make evaluate` could not tell which.
4. **Vectors are stored; scores are not.** `int_posting_unit_vectors` is the only incremental
   model — the only one that calls a model per posting. `int_jobs_matched` is a plain table
   rebuilt each run from those vectors and a freshly embedded resume.
5. **The task type is stated**, as `embedding_task_type`, and recorded beside every vector.

**Why 4 is the invalidation, rather than a version column.** A score depends on the posting text,
the resume, the embedding model and the task type. The earlier incremental model guarded on the
text alone, so every score computed before a resume edit survived it. Guarding on a resume version
would fix that by re-embedding every posting on every edit — paying a model call per posting for a
change that touched no posting. A posting's vector does not depend on the resume, so storing the
vector and recomputing the comparison removes the resume from what can go stale. What remains —
model and task type — is compared in the vector model's guard, and `int_jobs_matched` joins only
vectors that agree on both, so a vector left over from before either changed matches nothing
until it is re-embedded.

**Why 5.** An embedding model returns a different vector for the same text depending on the task
it is told the text is for. Left unset, that choice belongs to the provider's default, which can
change with no change in this repository. `SEMANTIC_SIMILARITY` is the symmetric type: both sides
are one sentence, and the question is whether they describe the same thing. The retrieval types
are for a short query against a long document.

**Consequences.**

- The extraction instruction changed, and `extraction_version` now sits in its guard, so the next
  prod run re-extracts every posting once (cost measured in ADR-0025). The same guard closes the
  gap where a reworded extraction instruction left two wordings in one column.
- Embedding calls grow from one per posting to one per sentence. They are made once per sentence
  and survive resume edits, weight changes and re-runs.
- `int_jobs_matched` is real SQL on both warehouses, so its arithmetic has dbt unit tests on the
  dev target. The embedding calls themselves have never been executed: `enable_embeddings` is
  still off, and the first run should be manual.
- Vectors for sentences a re-extraction replaced stay in the table unmatched. They are small and
  nothing reads them.
