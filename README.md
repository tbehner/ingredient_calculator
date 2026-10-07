# ingredients-calculator

Flask frontend that queries an existing SQLite ingredients database.

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

The devshell defaults `INGREDIENTS_DB` to `./ingredients.sqlite3`.

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
