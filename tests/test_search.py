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
    # The milk rows, which WRatio ranked below anything that merely contained
    # "Milch": the qualifier tail makes them more than 8× the query's length.
    "Milch fettarm, frisch, 1,5 % Fett, pasteurisiert",
    "Milch entrahmt, frisch, höchstens 0,1 % Fett, pasteurisiert",
    "Vollmilch frisch, 3,5 % Fett, pasteurisiert",
    "Ananasringe im Milchbackteig gebraten",
    "Apfelreis mit Milch 3,5 % Fett",
    "Buttermilchpulver",
    "Weizen Mehl, Type 405",
    "Buchweizen Mehl",
    "Popcorn ungesüßt",
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
    """`Haferflocken` has to reach the stored `Hafer Flocken` — the case FTS5 missed.

    It has to come *first*, too: the real corpus has longer rows that spell
    `Haferflocken` out as one token, and they match a whole token where this
    only matches a joined pair.
    """
    assert descriptions(search.search("haferflocken"))[0] == "Hafer Flocken"


def test_typo_is_tolerated(app_context):
    assert descriptions(search.search("Hafr Flocken"))[0] == "Hafer Flocken"


def test_transliterated_query_matches_eszett(app_context):
    assert "Apfelmus gesüßt" in descriptions(search.search("gesuesst"))


def test_transliterated_query_matches_uppercase_umlaut(app_context):
    """SQLite's lower() is ASCII-only, so `Ä` must be folded by replace()."""
    assert "Äpfel getrocknet" in descriptions(search.search("aepfel"))


def test_a_milk_outranks_a_dish_made_with_milk(app_context):
    """The regression: WRatio put anything containing `Milch` above the milks.

    Its `partial_ratio` scale drops from 0.9 to 0.6 once the description is 8×
    the query, so `Ananasringe im Milchbackteig gebraten` scored 90 and
    `Milch fettarm, frisch, 1,5 % Fett, pasteurisiert` scored 60.
    """
    assert descriptions(search.search("Milch"))[:2] == [
        "Milch fettarm, frisch, 1,5 % Fett, pasteurisiert",
        "Milch entrahmt, frisch, höchstens 0,1 % Fett, pasteurisiert",
    ]


def test_description_length_does_not_lower_the_score(app_context):
    """A qualifier tail must not cost a row its rank — the WRatio length bias."""
    assert search.score(
        search.norm("Milch"),
        search.norm("Milch entrahmt, frisch, höchstens 0,1 % Fett, pasteurisiert"),
    ) == search.score(search.norm("Milch"), search.norm("Milch"))


def test_compound_head_outranks_a_mere_mention(app_context):
    """`Vollmilch …` is a milk; `Apfelreis mit Milch …` is a rice pudding.

    The compound only matches inside a token (90) where the dish matches a whole
    one (100), so the head position has to be what decides it.
    """
    hits = descriptions(search.search("Milch"))
    assert hits.index("Vollmilch frisch, 3,5 % Fett, pasteurisiert") < hits.index(
        "Apfelreis mit Milch 3,5 % Fett"
    )


def test_query_matches_the_head_of_a_german_compound(app_context):
    """`milch` is a *suffix* of `Vollmilch`: German puts the head noun last."""
    assert "Vollmilch frisch, 3,5 % Fett, pasteurisiert" in descriptions(search.search("Milch"))


def test_split_compound_is_matched_across_the_space(app_context):
    """`weizenmehl` has to reach `Weizen Mehl, Type 405`, not `Buchweizen Mehl`."""
    assert descriptions(search.search("weizenmehl"))[0] == "Weizen Mehl, Type 405"


def test_negation_does_not_count_as_a_head_match(app_context):
    """`gesuesst` sits inside `ungesuesst`, but only past the head of the row.

    Joining `popcorn` and `ungesuesst` into one unit would hide that, which is
    why a joined pair has to match whole rather than by containment.
    """
    hits = descriptions(search.search("gesuesst"))
    assert hits.index("Apfelmus gesüßt") < hits.index("Popcorn ungesüßt")


def test_ranking_does_not_depend_on_the_row_order(app_context):
    """Ties are broken on length and id, not on what SQLite happened to return."""
    assert descriptions(search.search("Milch")) == descriptions(search.search("milch"))


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


def test_every_fragment_gets_a_share_of_the_candidate_limit(app_context):
    """A common fragment must not spend the whole budget before the rare one.

    One OR'ed scan with a single `LIMIT` filled up with whatever the scan met
    first, which is rowid order: `gekochte kartoffeln mit butter` used all 500
    rows on `geko` and `mit` and never reached `kart`, dropping the one row that
    scored best. Here `floc` matches several low-id rows and `kaka` only a
    later one, so the plain `LIMIT 2` would return no `kaka` row at all.
    """
    found = [row["description"] for row in search.candidates("flocken kakao", limit=2)]
    assert "Haferflockenauflauf mit Kakao" in found


def test_known_limitation_typo_inside_the_fragment(app_context):
    """`zwibl` cannot reach `Zwiebeln`: the typo is inside the recall fragment.

    Documented in docs/search.md. Change this test when that is addressed with a
    trigram index, spellfix1 or SymSpell.
    """
    assert search.search("zwibl") == []
