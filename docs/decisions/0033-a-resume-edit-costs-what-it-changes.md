# 0033 — A resume edit costs what it changes

**Status:** accepted (refines ADR-0027 on how the corpus is edited, and the invalidation rule of
ADR-0031; ends the property that a score column holds a single version)

ADR-0027 made the corpus something written by addition, so it is edited often. Every stored model
result is versioned by a digest of the text the model read, which is what makes an edit take effect
without a manual refresh. It also made every edit cost the same: one added bullet, a reworded one
and a new resume each re-ran every stored posting through every model that reads the resume.

Most of that spend bought nothing:

- **Closed postings.** The result tables only grow, and nothing delivers a closed posting.
- **Postings in the `drop` tier.** Delivery collapses them to a line of titles (ADR-0031).
- **Rewordings.** A model reading a bullet reads its meaning, so a second wording of the same work
  re-ran everything to tell it nothing.

## Decision

**1. A rewording is a `variant`, and no model reads it.** A bullet may carry other wordings of the
same work. They are embedded as units of their own, because cosine similarity is sensitive to
vocabulary and a second wording is another chance at a posting's phrasing. They are never rendered
into the scoring prompt or the assessment profile. Those two texts are what version every stored
result, so adding a variant moves neither and re-runs nothing — and the embedding side recomputes
from stored posting vectors at no model cost (ADR-0032). A wording that states something the bullet
does not is new evidence, belongs in the bullet, and is re-read.

**2. A re-run replaces a stored result only where the posting is still actionable**: open on at
least one board, and outside the `drop` tier. That set is `int_jobs_repass_scope`. Everything
outside it keeps the result it has, under the version recorded beside it.

**3. A first result is never limited.** Every posting is scored and assessed once, whatever it is.
The tier is unknown until a posting is assessed, so the assessment cannot be gated on it. The score
could be, and is not: `fit_score` and the tier are graded against each other, and a score withheld
from what the tier dropped would make the two rankings agree there by construction.

**4. No tier is not a drop.** A posting no tier has been assigned to is in scope. The assessment
can be switched off, behind, or have failed on a posting; reading that as "dropped" would leave a
resume edit with no effect at all.

**5. `repass_dropped` widens the scope for one run.** What puts a posting in `drop` is a different
profession or a far-off level, which one more bullet rarely changes. The exception is new work in a
discipline the corpus did not cover, and for that edit the var re-judges dropped postings too —
still open ones only — without a full refresh re-billing the closed ones.

**6. The edit says what it will cost before it costs it.** `make land-resume` reports bullets and
variants added and removed against the last landing, and which model passes the edit re-runs.
With `ARGS=--check` it also counts the postings and lands nothing. Bullets are compared by text:
a bullet's id is its position in its role, so an id comparison would report every bullet after an
inserted one as changed.

## Why the scope is written twice

`int_jobs_scored` joins the scope view. `int_jobs_assessed` cannot: the tier is computed from the
assessment, so the view is downstream of the model that would read it. It applies the same two
conditions to its own stored rows instead. What makes a posting a drop is therefore a macro
(`macros/tier.sql`) that both `int_jobs_tiered` and that guard call, so the tier and the guard
cannot disagree about it.

## Consequences

- **A score column now holds more than one version, on purpose.** Earlier decisions treated that as
  a defect, because nothing recorded which rows were which. Here the version columns do, and the
  rows that share the newest version are exactly the ones delivery lists. Scores are comparable
  across postings within a version, not across the whole column.
- **Evaluation grades a closed posting on the result it last received.** `make evaluate` reads
  silver, because a label outlives its posting. A label on a posting that closed before an edit
  measures the corpus as it stood then. That is the right reading of a label made at that time, and
  the wrong one for a question about the current corpus.
- **`drop` is sticky under a resume edit** unless `repass_dropped` is set. A posting the tier
  dropped wrongly is caught the way it already was — its title is still in the digest — and is
  re-judged by that var, not by the next edit.
- **The rule is tested and the two guards are not.** The scope view and the drop macro are real SQL
  on both warehouses and have dbt unit tests. The guards that apply them sit in the BigQuery-only
  branches of two models, which have been parsed and never executed.

## Alternatives

- **Re-run only the postings a new bullet resembles**, selected by embedding similarity. Cost would
  track the edit most closely. It makes the two LLM rankings depend on the embedding ranking they
  are being graded against, and it mixes versions inside the delivered set, which this decision
  does not.
- **Send each posting only its nearest bullets**, and key invalidation on that set. Handles every
  kind of edit uniformly and shortens every call. But `work_match` and `stack_gap` judge what the
  candidate has *not* done, and with retrieval "not retrieved" reads as "not done".
- **Gate the first score on the tier as well.** Saves a call per dropped posting, at the price in
  decision 3.
- **Never re-run; apply an edit to new postings only.** Free. The open postings are the ones still
  worth applying to, and they would never be read against the new evidence.
