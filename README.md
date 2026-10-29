# ingredients-calculator

Flask frontend that queries an existing SQLite ingredients database.

## Using it

Three steps:

1. Name the product and its manufacturer — both optional, both only printed
   back on the result page — and type the ingredients, one `amount; name` line
   per ingredient, amounts in grams. `total` and `container` are reserved
   labels — the gross weight of the filled container and the tare weight of
   the empty one. Without a `total` line the amounts are taken to add up to
   the whole product.

   ```
   50; Haferflocken
   125; Milch 3,5%
   180; total
   5; container
   ```

2. Pick the database entry for each ingredient from its top matches.
3. Read the nutrition declaration, laid out like the one on a German package:
   the LMIV rows and wording, per 100g and for the whole batch, energy
   computed from the macros. Saturated fat, sugar and salt are mandatory on a
   real label but have no column in the database, so they stay `k. A.`.

Each step posts the typed text and the two names forward in hidden fields
(`templates/carried.html`); the database is read-only, so nothing is stored in
between.

## Setup

```sh
direnv allow     # or: nix develop
setup            # uv sync
```

## Running

Point `INGREDIENTS_DB` at the database you want to query, then:

```sh
INGREDIENTS_DB=/path/to/your.sqlite3 serve
```

The devshell defaults `INGREDIENTS_DB` to `./bls.sqlite3`, the database in this
repository.

## Commands (devshell)

| Command | Purpose                              |
| ------- | ------------------------------------ |
| `setup` | Create `.venv` and install deps      |
| `serve` | Flask dev server with reload         |
| `db`    | `sqlite3` shell on `$INGREDIENTS_DB` |
| `check` | `pytest`                             |
| `lint`  | `ruff check` + `ruff format --check` |

`check` rather than `test`, since `test` is a shell builtin and would shadow it.

## Layout

```
src/ingredients_calculator/
  app.py        application factory
  db.py         read-only SQLite connection per request, query helpers (model)
  controllers.py  logic between routes and database, returns template context
  search.py     fuzzy description search: SQL candidates + rapidfuzz rerank
  views.py      routes, thin: URL -> controller -> template
  templates/    Jinja templates
  static/       CSS
tests/
docs/
```

`db.py` opens the database read-only (`mode=ro`), since the schema and data come
from elsewhere.

## Notes

- [`docs/search.md`](docs/search.md) — why `search.py` looks the way it does:
  benchmarks for rapidfuzz, SQLite FTS5 and Postgres `pg_trgm`, the German
  umlaut/ß/compound gotchas, and the known typo limitation.
