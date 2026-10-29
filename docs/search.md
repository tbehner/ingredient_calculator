# Fuzzy ingredient search — findings

Notes from evaluating fuzzy matching over `food.description` on 2026-10-06.
Written down because the gotchas below cost measurable time to find.

**Implemented in `search.py`:** SQLite generates candidates, rapidfuzz reranks
them in process — with `search.score()` rather than `fuzz.WRatio`, for the
reason in *WRatio's length bias* below. There is deliberately no HTTP endpoint
yet. Measured end to end against the real `bls.sqlite3`:

```
'haferflocken'   cand= 40 ( 6.7ms)  total  7.8ms  100.0  Hafer Flocken
'Hafr Flocken'   cand= 36 (12.1ms)  total 14.0ms   94.4  Hafer Flocken
'gesuesst'       cand=262 ( 6.6ms)  total 19.0ms   85.0  Popcorn gesüßt
'milch 3,5'      cand=280 (11.8ms)  total 33.0ms   95.0  Dickmilch/Sauermilch, mind. 3,5 % Fett
'Milch'          cand=261 ( 6.4ms)  total 17.0ms  100.0  Milch fettarm, frisch, 1,5 % Fett, pasteurisiert
'weizenmehl'     cand=137 ( 6.2ms)  total  9.5ms   95.2  Weizen Mehl, Type 812
'zwibl'          cand=  0 ( 6.1ms)  total  6.0ms   >>> NO HIT
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
| `fuzz.WRatio` over all 7,140              | 370–780 ms   | unusably slow, and length-biased — see below                     |
| `fuzz.token_set_ratio` over all 7,140     | ~56 ms       | over-matches: scores 100 for any long description containing the token |
| token-prefix prefilter, then rerank        | **6–33 ms**  | 30–100× faster, and the rerank is then cheap enough to be token-level |

The cost was WRatio's internal `partial_ratio` pass over long strings — it is
*not* the `processor=` keyword. Pre-normalizing the corpus does not speed it up
(666 ms vs 691 ms), so the fix is to shrink the candidate set, not to
pre-process it. Scoring token by token is cheaper per row (the `ratio` calls are
over tokens, not whole descriptions), but the prefilter still earns its place:
it is what keeps the rerank off 7,140 rows.

`search.py` keeps the shape but moves candidate generation into SQL, so the
`keys` list never exists: `search.candidates()` emits one `LIKE` per query-token
fragment, OR'ed, over a folded `description`, and `search.search()` reranks only
those rows.

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
the stored `Hafer Flocken`. `search.units()` handles it by also matching the
query against each pair of adjacent tokens joined up, so `hafer` + `flocken` is
compared as `haferflocken` and matches exactly. If this degrades, trigram
similarity (what Postgres `pg_trgm` does) is the right escalation.

## WRatio's length bias

**`fuzz.WRatio` cannot rank these descriptions and is no longer used.** Found on
2026-10-08 from a query of `Milch`, whose top hits all contained the word but
none of which were milk:

```
90.0  Ananasringe im Milchbackteig gebraten
90.0  Buttermilchpulver
90.0  Apfelreis mit Milch 3,5 % Fett
...
60.0  Milch fettarm, frisch, 1,5 % Fett, pasteurisiert     <- the actual milk
```

The cause is a bucket boundary inside WRatio. It computes
`len_ratio = max(len) / min(len)` and scales its `partial_ratio` by 0.9 — but by
**0.6 once `len_ratio ≥ 8`**. Against the 5-character `milch` that threshold
falls at 40 characters, and every real milk row carries the full BLS qualifier
tail (`fettarm, frisch, 1,5 % Fett, pasteurisiert`) that puts it past 40. A
substring match in a *short* description therefore beats a substring match in a
long one, whatever the long one is about. It is the same length bias that
disqualifies `levenshtein` below, just hidden in a step function, and no cutoff
or `scorer_kwargs` tuning moves it.

`search.score()` replaces it and compares no lengths at all:

- each query token is scored against each description token and against each
  adjacent pair joined (`units()`), and the best per query token are averaged —
  so a description is rated on how much of the query it covers;
- an exact token scores 100, a token the query merely sits inside
  `COMPOUND_SCORE` (90). Containment is checked anywhere in the token, not just
  at the start: German puts the head noun of a compound last, so the whole-milk
  row is `H-Vollmilch 3,5 % Fett` with `milch` as a *suffix*. Without that,
  `Milch` misses whole milk entirely;
- containment is **not** allowed to match a joined pair, which already spans two
  tokens. `gesuesst` sits inside the pair `popcornungesuesst`, and counting it
  would make `Popcorn ungesüßt` a head match for `gesuesst`;
- a match past the first token is scaled by `NON_HEAD_SCALE` (0.85), because BLS
  leads with the head noun. This is what separates a milk from a dish made with
  milk, and it is deliberately a bigger penalty than the 10 points between an
  exact and a compound token: `Vollmilch frisch, 3,5 % Fett` (90) has to beat
  `Apfelreis mit Milch 3,5 % Fett` (100 × 0.85 = 85), even though only the
  latter spells the query out as its own word.

Ties are broken on description length and then id, so the plain row comes before
the qualified one and the order never depends on what SQLite returned first.
WRatio left ties unbroken — all six `Weizen Mehl, Type …` rows and
`Buchweizen Mehl` scored 81 for `weizenmehl`, and which one came first was
whatever the scan order gave.

Pinned by `test_search.py::test_a_milk_outranks_a_dish_made_with_milk` and the
tests around it.

### Known limitation: compounds the substring rule reads wrongly

Containment cannot tell a modifier from a morpheme. `Buchweizenmehl` contains
`weizenmehl` but is buckwheat flour, not wheat flour — only a German
decompounder knows that `buchweizen` is one word. Joined-pair matching is what
saves the `weizenmehl` query in practice (`Weizen Mehl, Type 405` matches the
pair at 95.2, while `Buchweizen Mehl` only reaches 83 by `ratio`), but the rule
is a heuristic and a corpus that wrote `Buchweizenmehl` as one token would rank
it first. A decompounder or trigram similarity is the fix if this starts to
matter.

Inflection is the other gap, and it is a *recall* one: BLS stores the singular
`Apfel …`, so the query `aepfel` never generates those candidates — its `aepf`
fragment does not occur in `apfel`. Same root cause as `zwibl` below.

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
the main reason the rapidfuzz rerank stays (`search.score()` matches that pair
exactly, by joining adjacent tokens).

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
in 8–20 ms for a one-token query and up to 78 ms for a four-token one, most of it
the candidate `LIKE` scan — one scan per fragment, at 6–14 ms each. That scan is
the part that will degrade first, since the folded expression cannot use
`food_description_idx`. Options when it does, cheapest first: raise
`FRAGMENT_LENGTH` to cut the candidate set, add an expression index over the
folded column (needs a writable database — `bls.sqlite3` is opened `mode=ro`), or
move to Postgres `pg_trgm` as above. BK-trees and SymSpell stay unnecessary until
the table grows by an order of magnitude.

### `CANDIDATE_LIMIT` has to be shared per fragment

The cap is not just a memory guard, and truncating it carelessly loses hits.
Found alongside the WRatio bug on 2026-10-08: `candidates()` used to be one
OR'ed scan under a single `LIMIT 500`, which filled up with whatever the scan
met first — rowid order. The query `gekochte kartoffeln mit butter` matches
2,656 rows, spent the whole budget on `geko` and `mit`, and never reached
`kart`:

```
cap=500, one OR'ed scan     72.7  Artischocken gekocht, mit Butter      <- best surviving row
all 2,656 candidates        82.3  Kartoffeln gekocht, mit Kräuterquark und Butter
```

So `candidates()` now gives each fragment `limit // len(frags)` rows of its own,
as a `UNION` of one subquery per fragment, and orders each slice by
`instr(description, fragment)` so what survives truncation is the rows the
fragment *leads* — the same head signal `score()` ranks on.

Raising the cap instead is not an option: scoring all 2,656 candidates takes
**338 ms** against 35 ms for the four capped scans. The cap is what makes a
token-level scorer affordable, so it has to stay, and it has to be fair.
Pinned by `test_every_fragment_gets_a_share_of_the_candidate_limit`.

## Rejected

- `fuzzywuzzy` — slower, GPL.
- `difflib.SequenceMatcher` — stdlib, but much slower and `ratio()` is a poor
  relevance signal here.
- Soundex / Metaphone — English-centric. The German equivalent is Kölner
  Phonetik, if phonetic matching is ever needed.
- SQLite FTS5 — no fuzzy matching, mandatory `*` per term, no compound splitting.
- Postgres `levenshtein` — length-biased ranking and unindexable; see above.