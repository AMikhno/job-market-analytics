# 0027 — Score against a resume corpus, not a skills list

**Status:** accepted (refines ADR-0020 §5)

ADR-0020 made the candidate profile private config and a versioned prompt block, which stands. It
described that profile as lists of skills, and that part is replaced here.

**A skills list makes the model do fuzzy keyword matching**, which the dbt seeds already do for
free and more cheaply (ADR-0015). Nothing an LLM adds over `desired_tech_hits` is exercised by
comparing one list of technologies to another. The signal only appears when *described work* meets
*described requirements* — which is the same argument
[relevance-signals](../research/relevance-signals.md) makes about postings, applied to the other
side of the comparison: a responsibilities blurb is not a data role, and a skills list is not a
work history.

**Decision.**

1. **The profile becomes a resume corpus**: summary, seniority, constraints, grouped skills, work
   history as bullets, personal projects, education. `config/resume.yaml`, gitignored, with a
   committed `config/resume.example.yaml`. It replaces the profile files ADR-0020 named, which
   were retired before ever carrying real content.
2. **Matching happens per bullet, not per resume** (`evidence_units`). A single vector over a whole
   resume averages unrelated work into something that matches everything weakly and nothing
   strongly; short specific texts also embed more reliably than long mixed ones. Each unit carries
   its origin, because a bullet read without knowing who it was built for loses the distinction
   between role families.
3. **Bullets may be tagged with role families**, from a closed set. Closed because a typo'd tag in
   a hand-edited file would silently tag nothing, and a matcher quietly ignoring part of the corpus
   is indistinguishable from one that works.
4. **`target_roles` is dropped entirely**, and the rubric now instructs the model to ignore the
   posting's title outright rather than weighting it down. Retaining a list of wanted titles would
   have reintroduced title matching through the back door — the exact failure
   [relevance-signals](../research/relevance-signals.md) documents, where genuinely relevant work
   sat under *Forward Deployed Engineer* and *ERP Specialist*. The V1 `desired_titles` seed is
   untouched and still feeds `match_score`; this governs V2 scoring only.

**The corpus is not a CV, and that inverts how it is written.** Nobody reads it, so there is no
page limit and no narrative to serve: every omitted bullet is a posting that cannot match. It is
written by addition. A tailored CV is lossy by construction — each version drops whatever did not
serve one application — so assembling the corpus by merging existing versions yields the
intersection of past edits rather than the union of the work. This is a property of tailored
resumes generally, not a claim about any particular one.

**Consequence for evaluation.** Scoring quality now depends on corpus completeness, which is not
something the pipeline can observe: a posting missed because the corpus omitted the relevant work
looks identical to one correctly scored low. Only a human-labelled evaluation set distinguishes
them, which is why the eval harness is not optional and why the labels cannot come from an LLM
(`docs/research/relevance-signals.md`).
