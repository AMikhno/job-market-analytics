"""Write the dbt seeds from private config before dbt runs (`make seeds`).

The seeds are filter rules, and filter rules are personal context: where the
candidate may work, which technologies rank a posting up, which rank it down.
CLAUDE.md names work eligibility and preferences as input to decisions rather
than content, so they stopped being tracked files (ADR-0028).

Each seed resolves the same three ways, in order:

1. `<NAME>_CSV_CONTENT` in the environment -- how CI gets them, from an Actions
   variable rather than a secret: these are preferences, not credentials.
2. the private file already at `dbt/seeds/<name>.csv`, left untouched -- the
   local working copy.
3. the committed `config/seeds/<name>.example.csv`, with a warning.

Examples live outside `dbt/seeds/` deliberately. dbt loads *every* CSV under
seed-paths, so an example sitting beside the real file would materialize a
second, junk seed table.

Fallback is a warning rather than an error because a clone and a fork PR must
still build: the rules are generic, and the resulting gold is merely someone
else's shortlist rather than a wrong one. A malformed seed *is* an error, and
fails here rather than as a confusing dbt compile error two steps later.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("seeds")

ROOT: Final = Path(__file__).resolve().parents[1]
SEED_DIR: Final = ROOT / "dbt" / "seeds"
EXAMPLE_DIR: Final = ROOT / "config" / "seeds"

# Header per seed, asserted after materializing. dbt would otherwise accept a
# renamed column and fail in whichever model refs it, one layer away from cause.
SEED_HEADERS: Final[dict[str, list[str]]] = {
    "allowed_locations": ["pattern"],
    "deal_breaker_tech": ["tech", "reason"],
    "desired_tech": ["tech", "note"],
    "desired_titles": ["pattern", "note"],
}


def env_var_for(seed: str) -> str:
    """Actions-variable name for a seed (`desired_tech` -> DESIRED_TECH_CSV_CONTENT).

    Suffixed `_CONTENT` like COMPANIES_CSV_CONTENT, and for the same reason: it
    holds a file's *contents*, while the unsuffixed form reads as a path.
    """
    return f"{seed.upper()}_CSV_CONTENT"


def _validate(seed: str, text: str, origin: str) -> None:
    """Reject a seed whose header is not the one the models were built against."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError(f"{seed}: empty seed from {origin}")
    expected = SEED_HEADERS[seed]
    header = [c.strip() for c in rows[0]]
    if header != expected:
        raise ValueError(f"{seed}: header from {origin} is {header}, expected {expected}")
    if len(rows) < 2:
        raise ValueError(f"{seed}: no data rows from {origin}")


def materialize_seed(seed: str, *, env: Mapping[str, str] | None = None) -> str:
    """Put one seed in place at dbt/seeds/. Returns which source was used."""
    source: Mapping[str, str] = os.environ if env is None else env
    target = SEED_DIR / f"{seed}.csv"
    example = EXAMPLE_DIR / f"{seed}.example.csv"

    content = source.get(env_var_for(seed), "").strip()
    if content:
        text = content + "\n"
        _validate(seed, text, env_var_for(seed))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return "environment"

    if target.exists():
        _validate(seed, target.read_text(), str(target))
        return "private file"

    if not example.exists():
        raise FileNotFoundError(f"{seed}: no {env_var_for(seed)}, no {target}, and no {example}")
    text = example.read_text()
    _validate(seed, text, str(example))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example, target)
    log.warning(
        "%s: using the committed example. Create %s with your own rules for a real run.",
        seed,
        target,
    )
    return "example"


def run(env: Mapping[str, str] | None = None) -> int:
    for seed in SEED_HEADERS:
        origin = materialize_seed(seed, env=env)
        log.info("%s <- %s", seed, origin)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
