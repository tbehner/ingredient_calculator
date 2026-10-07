# Fuzzy ingredient search — findings

Notes from evaluating fuzzy matching over `food.description` on 2026-10-06.
Written down because the gotchas below cost measurable time to find.

**Implemented in `search.py`:** SQLite generates candidates, rapidfuzz reranks
them in process. There is deliberately no HTTP endpoint yet. Measured end to end
against the real `bls.sqlite3`:

```
'haferflocken'   cand= 40 ( 7.6ms)  total  9.8ms   96.0  Hafer Flocken
'Hafr Flocken'   cand= 36 (13.9ms)  total 18.1ms   96.0  Hafer Flocken
'gesuesst'       cand=262 ( 7.4ms)  total 22.8ms   90.0  Amaranth gepufft, ungesüßt
'milch 3,5'      cand=280 (13.6ms)  total 33.8ms   90.0  Apfelreis mit Milch 3,5 % Fett
'zwibl'          cand=  0 ( 7.0ms)  total  6.9ms   >>> NO HIT
```

The candidate query never returns more than a few hundred of the 7,140 rows, so
the process never holds the corpus — that was the reason to let the database
generate candidates rather than scoring everything in Python. Postgres with
`pg_trgm` is the escalation path if this stops being enough; see below.

FTS5 was evaluated and **rejected**: it cannot do fuzzy matching at all (`Hafr`
never reaches `Hafer`), needs an explicit `*` on every term, and does not split
German compounds. Those findings are kept below because they also constrain any
future FTS5 use.

Measured against the real `bls.sqlite3`: 7,140 descriptions, average 35
characters, multi-word with qualifiers (`Hafer Flocken`, `Milch 3,5 % Fett`).

## Use rapidfuzz, and prefilter before scoring

Scoring the whole corpus on every query is too slow for a request:

| Approach                                  | Latency      | Notes                                                           |
| ----------------------------------------- | ------------ | --------------------------------------------------------------- |
| `fuzz.WRatio` over all 7,140              | 370–780 ms   | best ranking, unusably slow                                     |
| `fuzz.token_set_ratio` over all 7,140     | ~56 ms       | over-matches: scores 100 for any long description containing the token |
| token-prefix prefilter, then WRatio rerank | **5–22 ms**  | WRatio's ranking, 30–100× faster                                |

The cost is WRatio's internal `partial_ratio` pass over long strings — it is
*not* the `processor=` keyword. Pre-normalizing the corpus does not speed it up
(666 ms vs 691 ms), so the fix is to shrink the candidate set, not to
pre-process it.

`search.py` keeps the shape but moves candidate generation into SQL, so the
`keys` list never exists: `search.candidates()` emits one `LIKE` per query-token
fragment, OR'ed, over a folded `description`, and `search.search()` reranks only
those rows with `WRatio`.

### Memory, if candidate generation ever moves back into Python

Holding all 7,140 descriptions resident costs about **2 MB** (248 KB of UTF-8
text; 580 KB as `str` objects, plus 534 KB for the normalized copies; 1.95 MB by
`tracemalloc`; 5.2 MB of RSS including SQLite's page cache). `import rapidfuzz`
adds 0.9 MB, and the whole process sits at 21 MB. For comparison a Postgres
trigram GiST index over the same data is 1184 KB, so the data is in RAM either
way. Memory is not a reason to pick one approach over the other at this size.

## Transliterate umlauts and ß explicitly

`unicodedata` NFKD strips accents but will **not** turn `ß` into `ss`, so use an
explicit `str.maketrans` table. Without it, the query `gesuesst` scored 68
against the unrelated `gesäuert`; with it, `ungesüßt` scores 90.

German compounds are the related wrinkle: the query `Haferflocken` has to match
the stored `Hafer Flocken`. WRatio handles it (96), which is part of why it is
worth reranking with. If this degrades, trigram similarity (what Postgres
`pg_trgm` does) is the right escalation.

## Known limitation: typo before the prefix point

No candidate generator tried here recovers from a typo in the prefix itself.
`search.FRAGMENT_LENGTH`, an FTS5 `zwibl*`, and Postgres trigrams all fail the
same way, so this is not something a different database fixes:

- `search.search("zwibl")` returns `[]` — 0 candidates.
- FTS5 `zwibl*` → 0 hits.
- `pg_trgm` → 0 rows even at `similarity_threshold = 0.25`; max similarity 0.143.
  `show_trgm` shows why: `{"  z"," zw","bl ",ibl,wib,zwi}` against
  `{"  z"," zw",bel,ebe,eln,ieb,"ln ",wie,zwi}` shares only three trigrams.
  Postgres `word_similarity` does score it 0.500, so the `<%` operator with a
  lower threshold is the one tool that helps — a different operator, not a
  tuning knob.

Fixes, in increasing order of effort: a trigram index, SQLite's `spellfix1`
extension, or SymSpell. Pinned by
`test_search.py::test_known_limitation_typo_inside_the_fragment`, which should be
changed rather than deleted when this is addressed.

## SQLite's `lower()` is ASCII-only

`search._sql_norm()` folds `Ä`/`Ö`/`Ü` with `replace()` rather than leaning on
`lower()`, because SQLite's built-in `lower()` only handles ASCII. Without that,
a query of `aepfel` silently misses `Äpfel …`. The folding expression is
interpolated into the SQL while the fragments stay bound parameters — the
expression contains no user input.

## FTS5 + BM25

All of the below was measured on a copy of `bls.sqlite3`. FTS5 is compiled into
both the devshell `sqlite3` and Python's `sqlite3` (SQLite 3.53.3).

The index itself, as an *external content* table — FTS5 then stores only the
index and reads the originals back out of `food` by rowid, rather than keeping a
second copy of the text:

```sql
CREATE VIRTUAL TABLE food_fts USING fts5(
    description,
    content='food',
    content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);

INSERT INTO food_fts(food_fts) VALUES ('rebuild');   -- populate from `food`
```

Build cost: **83 ms**, **+283 KB** on a 1.04 MB database. Querying:

```sql
SELECT f.description, bm25(food_fts) AS rank
FROM food_fts JOIN food f ON f.id = food_fts.rowid
WHERE food_fts MATCH ?
ORDER BY rank          -- bm25() is negative; more negative = better, so ASC
LIMIT 20;
```

0.02–0.4 ms per query. Add `AFTER INSERT/DELETE/UPDATE ON food` triggers (writing
`'delete'` rows for the old values) only if `food` becomes mutable; for a static
import, re-running `'rebuild'` is enough.

### A bare token often misses — always append `*`

German inflection means the singular is a different token from what is stored:

```
'zwiebel'   ->     0 hits
'zwiebel*'  ->    44 hits
'zwiebeln'  ->    33 hits
```

### `remove_diacritics 2` folds umlauts but not ß

```
'gesüßt'   -> 165 hits
'gesußt'   -> 165 hits     <- ü folded to u
'gesusst'  ->   0 hits     <- ß NOT expanded to ss
```

FTS5 has no option for this, which is the second reason to own the
transliteration (see `norm` above).

### Compounds do not split, in either direction

`haferflocken` finds only descriptions literally containing `Haferflocken`, never
the stored `Hafer Flocken`; and `hafer` does not find `Haferflocken`. There is no
German decompounder in FTS5. This is the limitation that does not go away, and
the main reason the rapidfuzz rerank stays (WRatio scores that pair 96).

### Prefer a normalized in-memory index over writing to `bls.sqlite3`

`db.py` opens the database read-only on purpose, so the index should not be
written into it. Indexing a `norm()`-ed column in a throwaway in-memory database
at startup fixes the ß problem and keeps the source file pristine:

```python
idx = sqlite3.connect(":memory:")
idx.execute("CREATE VIRTUAL TABLE fts USING fts5(norm, id UNINDEXED, tokenize='unicode61')")
idx.executemany("INSERT INTO fts(norm, id) VALUES (?, ?)", [(norm(d), i) for i, d in rows])
```

**33 ms to build, 0.01–0.2 ms per query**, and the same `norm()` serves both FTS
and the rapidfuzz rerank. To share one index across worker processes instead,
build the same thing into a sidecar file and `ATTACH` it; the DDL is unchanged.

**Trap:** do not pass the raw query through `norm()`. It lowercases FTS5's
operators, and `milch and 3` makes FTS5 search for a literal term `and` — zero
hits. Normalize the user's *terms* and assemble the MATCH expression separately.

## Escalation path: Postgres with pg_trgm (not Levenshtein)

Measured on a throwaway Postgres 17.11 cluster loaded with all 7,140 rows. If
search moves into the database, this is the shape — but **`levenshtein` is the
wrong function**, despite being the obvious one.

Scoring the query `Haferflocken`:

| Target                  | `levenshtein` | `similarity` (trgm) |
| ----------------------- | ------------- | ------------------- |
| `Hafer Flocken`         | 2             | 0.688               |
| `Hafer Vollkornflocken` | **9**         | 0.458               |
| `Haferbrot`             | **6**         | 0.278               |
| `Gerste Flocken`        | **7**         | 0.273               |

Levenshtein ranks `Haferbrot` and `Gerste Flocken` *above*
`Hafer Vollkornflocken`: the inserted `Vollkorn` costs 8 edits and the metric has
no notion that everything else matched. On the real corpus its top 5 for
`Haferflocken` was `Hafer Flocken, Hirse Flocken, Haferbrötchen, Emmer Flocken,
Hafer Kleie` — not one actual Haferflocken product, because length difference
dominates. It is also unindexable: `ORDER BY levenshtein(...)` plans as a
`Seq Scan` over all 7,140 rows (8.9 ms) and no index type changes that.

(`Hafer Vollkornflocken` is not in `bls.sqlite3` — it is a literal in that table,
used because it is the case that distinguishes the two metrics. Do not read the
trigram column as "this row is retrieved".)

What works:

```sql
CREATE EXTENSION pg_trgm;
CREATE INDEX food_trgm_gist ON food USING gist (description gist_trgm_ops);
SET pg_trgm.similarity_threshold = 0.3;

SELECT description, similarity(description, :q) AS sim
FROM food
WHERE description % :q              -- threshold filter
ORDER BY description <-> :q         -- trigram distance
LIMIT 20;
```

Single `Index Scan` satisfying both filter and ordering, **2.2 ms**. Index build
43 ms; 1184 kB against a 576 kB table.

**Use GiST, not GIN.** GIN serves `%` but cannot accelerate `<->` ordering — it
falls back to `Seq Scan` at 22 ms. With both present the planner picked GiST.

Keep `fuzzystrmatch`'s `levenshtein` only as a reranker over the few candidate
rows the index returns, where being unindexable does not matter.

## Scale

At 7,140 rows nothing more is needed for *performance*: the implemented path runs
in 7–34 ms, of which 7–14 ms is the candidate `LIKE` scan. That scan is the part
that will degrade first, since the folded expression cannot use
`food_description_idx`. Options when it does, cheapest first: raise
`FRAGMENT_LENGTH` to cut the candidate set, add an expression index over the
folded column (needs a writable database — `bls.sqlite3` is opened `mode=ro`), or
move to Postgres `pg_trgm` as above. BK-trees and SymSpell stay unnecessary until
the table grows by an order of magnitude.

## Rejected

- `fuzzywuzzy` — slower, GPL.
- `difflib.SequenceMatcher` — stdlib, but much slower and `ratio()` is a poor
  relevance signal here.
- Soundex / Metaphone — English-centric. The German equivalent is Kölner
  Phonetik, if phonetic matching is ever needed.
- SQLite FTS5 — no fuzzy matching, mandatory `*` per term, no compound splitting.
- Postgres `levenshtein` — length-biased ranking and unindexable; see above.