import sqlite3

import pytest

from ingredients_calculator import search
from ingredients_calculator.app import create_app

# Real descriptions from bls.sqlite3, chosen to cover the German cases that
# tripped up every candidate approach in docs/search.md.
FOODS = [
    "Hafer Flocken",
    "Hafer Flocken, gekocht",
    "Haferflocken-Nussplätzchen",
    "Haferflockenauflauf mit Kakao",
    "Hirse Flocken",
    "Gerste Flocken",
    "Haferbrot",
    "Apfelmus gesüßt",
    "Cornflakes gesüßt",
    "Äpfel getrocknet",
    "Zwiebeln gebraten",
    "Bechamelsauce mit Milch 3,5 % Fett",
]


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "test.sqlite3"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE food (id INTEGER PRIMARY KEY, bls_code TEXT, description TEXT NOT NULL)"
    )
    con.executemany(
        "INSERT INTO food (bls_code, description) VALUES (?, ?)",
        [(f"X{n:05d}", d) for n, d in enumerate(FOODS)],
    )
    con.commit()
    con.close()
    return path


@pytest.fixture
def app_context(database):
    app = create_app({"DATABASE": str(database), "TESTING": True})
    with app.app_context():
        yield app


def descriptions(hits):
    return [hit.description for hit in hits]


def test_norm_folds_umlauts_and_eszett():
    assert search.norm("Apfelmus gesüßt") == "apfelmus gesuesst"
    assert search.norm("Äpfel") == "aepfel"


def test_fragments_are_leading_token_slices():
    assert search.fragments("Hafer Flocken") == ["hafe", "floc"]
    assert search.fragments("") == []


def test_compound_query_finds_spaced_description(app_context):
    """`Haferflocken` has to reach the stored `Hafer Flocken` — the case FTS5 missed."""
    assert descriptions(search.search("haferflocken"))[0] == "Hafer Flocken"


def test_typo_is_tolerated(app_context):
    assert descriptions(search.search("Hafr Flocken"))[0] == "Hafer Flocken"


def test_transliterated_query_matches_eszett(app_context):
    assert "Apfelmus gesüßt" in descriptions(search.search("gesuesst"))


def test_transliterated_query_matches_uppercase_umlaut(app_context):
    """SQLite's lower() is ASCII-only, so `Ä` must be folded by replace()."""
    assert "Äpfel getrocknet" in descriptions(search.search("aepfel"))


def test_hits_carry_id_and_score(app_context):
    hit = search.search("haferflocken")[0]
    assert hit.id > 0
    assert 70 <= hit.score <= 100


def test_blank_query_returns_nothing(app_context):
    assert search.search("") == []
    assert search.search("   ") == []


def test_candidates_are_a_subset_not_the_whole_corpus(app_context):
    """The point of DB-side generation: never pull the full table into memory."""
    assert 0 < len(search.candidates("haferflocken")) < len(FOODS)


def test_candidate_limit_is_honoured(app_context):
    assert len(search.candidates("hafe", limit=2)) == 2


def test_known_limitation_typo_inside_the_fragment(app_context):
    """`zwibl` cannot reach `Zwiebeln`: the typo is inside the recall fragment.

    Documented in docs/search.md. Change this test when that is addressed with a
    trigram index, spellfix1 or SymSpell.
    """
    assert search.search("zwibl") == []
