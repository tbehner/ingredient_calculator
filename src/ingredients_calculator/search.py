"""Fuzzy search over food descriptions.

Candidate generation happens in SQLite, so the process never holds the whole
corpus; only the small candidate set is reranked in Python with rapidfuzz, which
is what provides the typo tolerance SQLite has no answer for.

See `docs/search.md` for the measurements behind these choices.
"""

from typing import NamedTuple

from rapidfuzz import fuzz, process

from . import db

#: Leading characters of each query token used to recall candidates. Four is
#: enough to let `haferflocken` reach the stored `Hafer Flocken`.
FRAGMENT_LENGTH = 4

#: Upper bound on rows pulled out of SQLite for reranking.
CANDIDATE_LIMIT = 500

#: WRatio score below which a hit is not worth showing.
SCORE_CUTOFF = 70

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

    # One LIKE per fragment, OR'ed: recall is deliberately loose because
    # `search()` reranks afterwards. The fragments are bound parameters; only
    # the folding expression is interpolated, and it contains no user input.
    folded = _sql_norm("description")
    where = " OR ".join([f"{folded} LIKE '%' || ? || '%'"] * len(frags))

    return db.query(
        f"SELECT id, description FROM food WHERE {where} LIMIT ?",
        (*frags, limit),
    )


def search(query: str, limit: int = 10, score_cutoff: float = SCORE_CUTOFF) -> list[Hit]:
    """Best matches for `query`, ranked by rapidfuzz WRatio.

    Returns an empty list for a blank query, and for a typo inside the first
    `FRAGMENT_LENGTH` characters — candidate generation cannot recover from that.
    """
    rows = candidates(query)
    if not rows:
        return []

    descriptions = {row["id"]: row["description"] for row in rows}
    pool = {row_id: norm(text) for row_id, text in descriptions.items()}

    hits = process.extract(
        norm(query),
        pool,
        scorer=fuzz.WRatio,
        limit=limit,
        score_cutoff=score_cutoff,
    )
    return [Hit(row_id, descriptions[row_id], score) for _, score, row_id in hits]
