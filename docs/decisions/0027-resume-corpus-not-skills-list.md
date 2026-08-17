# 0027 — Score against a resume corpus, not a skills list

**Status:** accepted (refines ADR-0020 §5)

ADR-0020 made the candidate profile private config and a versioned prompt block, which stands. It
described that profile as lists of skills, and that part is replaced here.

**A skills list makes the model do fuzzy keyword matching**, which the dbt seeds already do more
cheaply (ADR-0015): nothing an LLM adds over `desired_tech_hits` is exercised by comparing one list
of technologies to another. The signal only appears when *described work* meets *described
requirements* — the same argument [relevance-signals](../research/relevance-signals.md) makes about
postings, applied to the other side of the comparison.

**Decision.**

1. **The profile becomes a resume corpus**: summary, seniority, constraints, grouped skills, work
   history as bullets, personal projects, education. `config/resume.yaml`, gitignored, with a
   committed `config/resume.example.yaml`. It replaces the profile files ADR-0020 named, which
   were retired before ever carrying real content.
2. **Matching happens per bullet, not per resume** (`evidence_units`). One vector over a whole
   resume averages unrelated work into something that matches everything weakly; short specific
   texts also embed more reliably. Each unit carries its origin, because a bullet read without
   knowing who it was built for loses the distinction between role families.
3. **Bullets may be tagged with role families**, from a closed set — seven work *shapes*,
   deliberately fewer than the titles they cover. The tag drives no matching: scoring and
   similarity both read `text` alone, and the tags are kept out of the prompt on purpose, since a
   rubric built to ignore job titles should not be handed title-shaped labels.
   **What the closed set buys is the coverage report** (`make land-resume`), which counts bullets
   per family and warns on any at zero. That is the only reading of the corpus-completeness
   problem this ADR ends on: a family at zero is why postings of that shape score low, and it
   cannot be reported for a family nobody declared.

   *Amended:* the set was originally three families, justified by a matcher-integrity argument
   for a matcher that was never built — nothing consumed the tag. It opened to seven when the
   target range turned out to be wider than three, and it earned its keep at the same time.
4. **`target_roles` is dropped entirely**, and the rubric instructs the model to ignore the
   posting's title outright rather than weighting it down. A list of wanted titles would
   reintroduce title matching through the back door — the exact failure
   [relevance-signals](../research/relevance-signals.md) documents. The V1 `desired_titles` seed is
   untouched and still feeds `match_score`; this governs V2 scoring only.

**The corpus is not a CV, and that inverts how it is written.** Nobody reads it, so there is no
page limit and no narrative to serve: every omitted bullet is a posting that cannot match. A
tailored CV is lossy by construction — each version drops whatever did not serve one application —
so assembling the corpus by merging existing versions yields the intersection of past edits rather
than the union of the work.

**Consequence for evaluation.** Scoring quality now depends on corpus completeness, which the
pipeline cannot observe: a posting missed because the corpus omitted the relevant work looks
identical to one correctly scored low. Only a human-labelled evaluation set distinguishes them,
which is why the eval harness is not optional and why the labels cannot come from an LLM
(`docs/research/relevance-signals.md`).
