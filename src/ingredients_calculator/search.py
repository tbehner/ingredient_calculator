"""Fuzzy search over food descriptions.

Candidate generation happens in SQLite, so the process never holds the whole
corpus; only the small candidate set is reranked in Python with rapidfuzz, which
is what provides the typo tolerance SQLite has no answer for.

See `docs/search.md` for the measurements behind these choices.
"""

from itertools import pairwise
from typing import NamedTuple

from rapidfuzz import fuzz

from . import db

#: Leading characters of each query token used to recall candidates. Four is
#: enough to let `haferflocken` reach the stored `Hafer Flocken`.
FRAGMENT_LENGTH = 4

#: Upper bound on rows pulled out of SQLite for reranking.
CANDIDATE_LIMIT = 500

#: Score below which a hit is not worth showing.
SCORE_CUTOFF = 70

#: Score for a query token found inside a longer description token, kept under
#: the 100 of an exact token so `Milch …` outranks `H-Vollmilch …`.
COMPOUND_SCORE = 90.0

#: Score multiplier for a query matched past the head of the description, which
#: is what separates a milk from a dish made with milk.
NON_HEAD_SCALE = 0.85

# German folding. `unicodedata` NFKD would strip the umlauts but leaves ß alone,
# so the mapping is explicit.
_FOLD = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"}

UMLAUT = str.maketrans(_FOLD)


class Hit(NamedTuple):
    id: int
    description: str
    score: float


def norm(text: str) -> str:
    """Fold case and German umlauts, so `gesuesst` can match `gesüßt`."""
    return text.lower().translate(UMLAUT)


def _sql_norm(column: str) -> str:
    """`norm()` expressed in SQL.

    SQLite's `lower()` is ASCII-only, so the uppercase umlauts have to be folded
    by `replace()` too rather than relying on `lower()` to reach them.
    """
    expr = column
    for char, replacement in _FOLD.items():
        expr = f"replace({expr}, '{char}', '{replacement}')"
    return f"lower({expr})"


def fragments(query: str) -> list[str]:
    """Leading fragment of each query token, used for candidate recall."""
    return [token[:FRAGMENT_LENGTH] for token in norm(query).split() if token]


def candidates(query: str, limit: int = CANDIDATE_LIMIT) -> list:
    """Rows whose description contains any query token's leading fragment."""
    frags = fragments(query)
    if not frags:
        return []

    # One LIKE per fragment, each with its own slice of the budget: `limit`
    # rows over a single OR'ed scan would be filled by whichever fragment the
    # scan met first, so a query of `gekochte kartoffeln mit butter` spent all
    # 500 rows before `kart` and dropped its own best match. Recall stays
    # deliberately loose because `search()` reranks afterwards.
    #
    # `instr` orders each slice by where the fragment sits in the description,
    # so what survives truncation is the rows the fragment leads — the same head
    # signal `score()` ranks on. The fragments are bound parameters; only the
    # folding expression is interpolated, and it contains no user input.
    folded = _sql_norm("description")
    subquery = (
        "SELECT id, description FROM ("
        f"  SELECT id, description FROM food WHERE {folded} LIKE '%' || ? || '%'"
        f"  ORDER BY instr({folded}, ?), id LIMIT ?"
        ")"
    )
    per_fragment = max(1, limit // len(frags))
    params = [param for frag in frags for param in (frag, frag, per_fragment)]

    return db.query(
        " UNION ".join([subquery] * len(frags)) + " LIMIT ?",
        (*params, limit),
    )


def _unit_score(query_token: str, unit: str, *, joined: bool) -> float:
    """How well one query token matches one unit of a description.

    An exact match scores 100, and a compound the query sits inside scores
    `COMPOUND_SCORE` — just below it, so the plain `Milch …` rows stay above
    `H-Vollmilch …` and `Milchmischgetränk …`. Containment is looked for
    anywhere in the token, not just at its start: German puts the head noun of a
    compound last, so `milch` is a *suffix* of the row that is actually whole
    milk. Anything else falls back to `ratio`, which is where the typo tolerance
    comes from.

    Containment does not apply to a `joined` unit, which already spans two
    tokens: `gesuesst` sits inside the pair `popcornungesuesst`, and counting
    that would make `Popcorn ungesüßt` a head match for `gesuesst`. A pair has
    to match as a whole to count at all.
    """
    if query_token == unit:
        return 100.0
    if query_token in unit and not joined:
        return COMPOUND_SCORE
    return fuzz.ratio(query_token, unit)


def units(description: str) -> list[tuple[str, int, bool]]:
    """The description's tokens and its adjacent pairs, with position and origin.

    The pairs are what bridges a German compound the data writes apart: the
    query `haferflocken` matches no token of `Hafer Flocken`, but matches the
    pair `haferflocken` exactly. BLS splits at most one compound per head noun
    (`Hafer Flocken`, `Weizen Mehl`), so pairs are enough and triples would only
    cost time. A pair takes the position of its first token.
    """
    tokens = description.split()
    pairs = pairwise(tokens)
    return [(token, position, False) for position, token in enumerate(tokens)] + [
        (first + second, position, True) for position, (first, second) in enumerate(pairs)
    ]


def _best_unit(query_token: str, desc_units: list[tuple[str, int, bool]]) -> tuple[float, int]:
    """Best scoring description unit for `query_token`, with its position.

    Ties go to the leftmost unit, so the position is usable as a relevance
    signal: BLS descriptions lead with the head noun and trail the qualifiers.
    """
    score, negated_position = max(
        (_unit_score(query_token, unit, joined=joined), -position)
        for unit, position, joined in desc_units
    )
    return score, -negated_position


def score(query: str, description: str) -> float:
    """Score of a normalized `description` against a normalized `query`.

    Every query token is matched against the description's tokens and the scores
    averaged, so a description is rated on how much of the query it covers
    rather than on how long it is. That length bias is the bug this replaced:
    WRatio ranked `Ananasringe im Milchbackteig gebraten` (90) above `Milch
    fettarm, frisch, 1,5 % Fett, pasteurisiert` (60) for a query of `milch`,
    purely because the latter is more than 8× the query's length and so fell
    into WRatio's harsher `partial_ratio` bucket. Nothing here compares the two
    lengths; `search()` uses the length only to break an exact tie.

    What is left is telling a milk from a dish made with milk, and position does
    that: BLS leads with the head noun, so a query matched anywhere else is
    scaled by `NON_HEAD_SCALE`. The scale is below the 10 points between an
    exact and a compound token on purpose — `Vollmilch frisch, 3,5 % Fett` is a
    better answer to `Milch` than `Apfelreis mit Milch 3,5 % Fett`, even though
    only the latter spells the query out as its own word.
    """
    query_tokens = query.split()
    desc_units = units(description)
    if not query_tokens or not desc_units:
        return 0.0

    best = [_best_unit(token, desc_units) for token in query_tokens]
    covered = sum(unit_score for unit_score, _ in best) / len(best)

    head = best[0][1] == 0
    return covered if head else covered * NON_HEAD_SCALE


def search(query: str, limit: int = 10, score_cutoff: float = SCORE_CUTOFF) -> list[Hit]:
    """Best matches for `query`, ranked by `score()`.

    Equal scores are ordered by description length, which prefers the plain
    `Vollmilch 3,5 % Fett, pasteurisiert` to a row that qualifies it further.
    The id is in the sort key only to keep the order of two equally good
    descriptions from depending on what SQLite returned first.

    Returns an empty list for a blank query, and for a typo inside the first
    `FRAGMENT_LENGTH` characters — candidate generation cannot recover from that.
    """
    rows = candidates(query)
    if not rows:
        return []

    normalized = norm(query)
    ranked = []
    for row in rows:
        description = row["description"]
        value = score(normalized, norm(description))
        if value >= score_cutoff:
            ranked.append((-value, len(description), row["id"], description))

    ranked.sort()
    return [
        Hit(row_id, description, -negated) for negated, _, row_id, description in ranked[:limit]
    ]
