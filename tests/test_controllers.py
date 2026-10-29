import re
import sqlite3

import pytest

from ingredients_calculator import controllers
from ingredients_calculator.app import create_app
from ingredients_calculator.models import Ingredient, Macros

# id, description, fat, protein, carbs per 100g. `Salz` has NULL macros, which
# the BLS data does for anything unmeasured.
FOODS = [
    (1, "Hafer Flocken", 6.65, 13.22, 53.3),
    (2, "Milch 3,5 % Fett", 3.5, 3.4, 4.8),
    (3, "Hirse Flocken", 3.9, 10.6, 68.8),
    (4, "Salz", None, None, None),
]


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "test.sqlite3"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE food (id INTEGER PRIMARY KEY, bls_code TEXT, description TEXT NOT NULL,"
        " fat REAL, protein REAL, carbs REAL)"
    )
    con.executemany(
        "INSERT INTO food (id, bls_code, description, fat, protein, carbs)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [(food_id, f"X{food_id:05d}", *rest) for food_id, *rest in FOODS],
    )
    con.commit()
    con.close()
    return path


@pytest.fixture
def app_context(database):
    app = create_app({"DATABASE": str(database), "TESTING": True})
    with app.app_context():
        yield app


@pytest.fixture
def client(database):
    app = create_app({"DATABASE": str(database), "TESTING": True})
    return app.test_client()


def ingredient(name, amount, id=controllers.UNRESOLVED):
    return Ingredient(id=id, name=name, inp_text=f"{amount}; {name}", amount=amount)

def test_normalized_ingredients_get_normalized():
    ingredients = [
            Ingredient(id=1, name="Hafer", inp_text="Hafer", amount=100),
            ]
    assert ingredients == controllers.normalized_ingredients(ingredients, 100, 0)


def test_ingredients_get_normalized():
    ingredients = [
            Ingredient(id=1, name="Hafer", inp_text="Hafer", amount=50),
            Ingredient(id=2, name="Milch", inp_text="Milch", amount=50),
            ]
    assert ingredients == controllers.normalized_ingredients(ingredients, 100, 0)

def test_get_macros():
    normalized_ingredients = [
            Ingredient(id=1, name="Hafer", inp_text="Hafer", amount=50),
            ]
    macros = {
            1: Macros(fatt=10, prot=14, carbs=54),
            }
    expected_total_macros = Macros(fatt=5, prot=7, carbs=27)
    assert controllers.total_macros(normalized_ingredients, macros) == expected_total_macros


# -- parsing ----------------------------------------------------------------


def test_parse_reads_amount_and_name():
    recipe = controllers.parse_recipe("50; Hafer Flocken\n125; Milch")
    assert [(i.amount, i.name) for i in recipe.ingredients] == [
        (50.0, "Hafer Flocken"),
        (125.0, "Milch"),
    ]


def test_parse_keeps_the_input_line_and_leaves_the_id_unresolved():
    [oats] = controllers.parse_recipe("  50; Hafer  ").ingredients
    assert oats.inp_text == "50; Hafer"
    assert oats.id == controllers.UNRESOLVED


def test_parse_takes_total_and_container_from_their_labels():
    recipe = controllers.parse_recipe("50; Hafer\n125; total\n25; container")
    assert (recipe.total, recipe.container) == (125.0, 25.0)
    assert [i.name for i in recipe.ingredients] == ["Hafer"]


def test_total_label_is_case_insensitive():
    assert controllers.parse_recipe("50; Hafer\n125; Total").total == 125.0


def test_missing_total_falls_back_to_the_summed_amounts():
    recipe = controllers.parse_recipe("50; Hafer\n125; Milch\n25; container")
    assert recipe.total == 200.0
    assert recipe.total - recipe.container == 175.0


def test_parse_accepts_comma_decimals_and_a_gram_suffix():
    recipe = controllers.parse_recipe("1,5g; Salz\n50 g; Hafer")
    assert [i.amount for i in recipe.ingredients] == [1.5, 50.0]


def test_parse_skips_blank_lines_and_comments():
    assert len(controllers.parse_recipe("\n# a comment\n50; Hafer\n\n").ingredients) == 1


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   \n\n",
        "125; total",  # weights only, nothing to look up
        "50 Hafer",  # no separator
        "; Hafer",  # no amount
        "50;",  # no name
        "fifty; Hafer",
        "-5; Hafer",
        "50; Hafer\n10; total\n20; container",  # container heavier than the total
    ],
)
def test_parse_rejects(text):
    with pytest.raises(controllers.InputError):
        controllers.parse_recipe(text)


# -- normalizing and summing ------------------------------------------------


def test_normalized_amounts_are_grams_per_100g_of_net_product():
    ingredients = [ingredient("Hafer", 100.0), ingredient("Milch", 300.0)]
    normalized = controllers.normalized_ingredients(ingredients, 450.0, 50.0)
    assert [i.amount for i in normalized] == [25.0, 75.0]


def test_normalized_amounts_add_up_to_100g():
    ingredients = [ingredient("Hafer", 50.0), ingredient("Milch", 125.0)]
    normalized = controllers.normalized_ingredients(ingredients, 175.0, 0.0)
    assert sum(i.amount for i in normalized) == pytest.approx(100.0)


def test_normalizing_leaves_the_input_alone():
    oats = ingredient("Hafer", 100.0)
    controllers.normalized_ingredients([oats], 200.0, 0.0)
    assert oats.amount == 100.0


def test_normalizing_needs_a_positive_net_weight():
    with pytest.raises(ValueError):
        controllers.normalized_ingredients([ingredient("Hafer", 50.0)], 100.0, 100.0)


def test_total_macros_scales_by_the_hundred_grams_of_each_ingredient():
    ingredients = [ingredient("Hafer", 100.0, id=1), ingredient("Milch", 300.0, id=2)]
    macros = {
        1: Macros(fatt=7.0, prot=13.0, carbs=59.0),
        2: Macros(fatt=3.5, prot=3.4, carbs=4.8),
    }
    total = controllers.total_macros(ingredients, macros)
    assert total.fatt == pytest.approx(7.0 + 3.5 * 3)
    assert total.prot == pytest.approx(13.0 + 3.4 * 3)
    assert total.carbs == pytest.approx(59.0 + 4.8 * 3)


# -- database lookup --------------------------------------------------------


def test_foods_by_id_returns_descriptions_and_macros(app_context):
    foods = controllers.foods_by_id([1, 2])
    assert foods[1].description == "Hafer Flocken"
    assert foods[2].macros == Macros(fatt=3.5, prot=3.4, carbs=4.8)


def test_foods_by_id_counts_null_macros_as_zero(app_context):
    assert controllers.foods_by_id([4])[4].macros == Macros(0.0, 0.0, 0.0)


def test_foods_by_id_skips_unknown_ids(app_context):
    assert list(controllers.foods_by_id([1, 999])) == [1]


def test_foods_by_id_without_ids_does_not_query(app_context):
    assert controllers.foods_by_id([]) == {}


# -- the contexts the views render ------------------------------------------


def test_choice_context_offers_matches_per_ingredient(app_context):
    context = controllers.choice_context("50; Haferflocken\n125; Milch")
    assert [i.name for i, _ in context["matches"]] == ["Haferflocken", "Milch"]
    assert context["matches"][0][1][0].description == "Hafer Flocken"
    assert context["unmatched"] == []


def test_choice_context_caps_the_matches(app_context):
    [(_, hits)] = controllers.choice_context("50; Flocken")["matches"]
    assert len(hits) <= controllers.CHOICES_PER_INGREDIENT


def test_choice_context_reports_ingredients_without_a_match(app_context):
    assert [i.name for i in controllers.choice_context("50; zzzqqq")["unmatched"]] == ["zzzqqq"]


def test_result_context_computes_both_views_of_the_macros(app_context):
    context = controllers.result_context(
        "100; Hafer\n300; Milch\n450; total\n50; container", {0: "1", 1: "2"}
    )
    assert context["net_total"] == 400.0
    # 100g oats at 6.65g fat plus 300g milk at 3.5g fat, for the whole batch.
    assert context["batch"].fatt == pytest.approx(6.65 + 3.5 * 3)
    # The same fat spread over a 400g net product.
    assert context["per_100g"].fatt == pytest.approx((6.65 + 3.5 * 3) / 4)


def test_result_context_pairs_what_was_typed_with_what_was_matched(app_context):
    context = controllers.result_context("50; Hafer\n150; Milch", {0: "1", 1: "2"})
    assert [(row["name"], row["food"].description) for row in context["rows"]] == [
        ("Hafer", "Hafer Flocken"),
        ("Milch", "Milch 3,5 % Fett"),
    ]
    assert [row["share"] for row in context["rows"]] == [25.0, 75.0]


def test_result_context_needs_one_choice_per_ingredient(app_context):
    with pytest.raises(controllers.InputError, match="Milch"):
        controllers.result_context("50; Hafer\n150; Milch", {0: "1"})


def test_an_unpicked_ingredient_does_not_shift_the_others_ids(app_context):
    """The positions are keys, so a gap is a gap and not a silent renumbering."""
    with pytest.raises(controllers.InputError, match="Hafer"):
        controllers.result_context("50; Hafer\n150; Milch", {1: "2"})


def test_result_context_rejects_an_id_that_is_not_in_the_database(app_context):
    with pytest.raises(controllers.InputError):
        controllers.result_context("50; Hafer", {0: "999"})


# -- the three pages --------------------------------------------------------


def test_the_form_is_served(client):
    assert client.get("/").status_code == 200


def test_choose_lists_a_radio_per_match(client):
    response = client.post("/choose", data={"recipe": "50; Haferflocken"})
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'name="choice-0" value="1"' in body
    assert "Hafer Flocken" in body


def test_each_ingredient_gets_its_own_radio_group(client):
    """A shared group name would let the page hold one pick in total."""
    body = client.post("/choose", data={"recipe": "50; Haferflocken\n50; Milch"}).get_data(
        as_text=True
    )
    groups = set(re.findall(r'type="radio" name="([^"]+)"', body))
    assert groups == {"choice-0", "choice-1"}


def test_choose_preselects_the_best_match_of_every_ingredient(client):
    body = client.post("/choose", data={"recipe": "50; Haferflocken\n50; Milch"}).get_data(
        as_text=True
    )
    checked = re.findall(r'name="(choice-\d+)"[^>]*\n?\s*checked', body)
    assert checked == ["choice-0", "choice-1"]


def test_choose_carries_the_recipe_forward(client):
    body = client.post("/choose", data={"recipe": "50; Haferflocken"}).get_data(as_text=True)
    assert 'name="recipe" value="50; Haferflocken"' in body


def test_result_shows_the_macros(client):
    data = {
        "recipe": "100; Hafer\n300; Milch\n450; total\n50; container",
        "choice-0": "1",
        "choice-1": "2",
    }
    body = client.post("/result", data=data).get_data(as_text=True)
    # The label writes its grams the German way, with a decimal comma.
    assert "4,3" in body  # fat per 100g: 17.15 / 4
    assert "17,1" in body  # fat in the whole 400g: 6.65 + 3 * 3.5
    assert "400" in body  # the net weight


def test_energy_uses_the_lmiv_factors():
    """37 kJ/g of fat, 17 for carbs and protein; 9 kcal and 4."""
    assert Macros(fatt=1.0, prot=0.0, carbs=0.0).kj == 37.0
    assert Macros(fatt=0.0, prot=1.0, carbs=1.0).kj == 34.0
    assert Macros(fatt=1.0, prot=1.0, carbs=1.0).kcal == 17.0


def test_result_declares_the_energy(client):
    """Energy is not in the database; the label computes it from the macros."""
    data = {"recipe": "100; Hafer\n300; total", "choice-0": "1"}
    body = client.post("/result", data=data).get_data(as_text=True)
    # Per 100g of product the oats are a third of themselves: 2.22g fat at
    # 37 kJ/g, 4.41g protein and 17.77g carbs at 17 kJ/g.
    assert "459&thinsp;kJ" in body
    assert "109&thinsp;kcal" in body


def test_a_bad_recipe_comes_back_to_the_form(client):
    response = client.post("/choose", data={"recipe": "50 Hafer"})
    assert response.status_code == 400
    assert "is not `amount; name`" in response.get_data(as_text=True)


def test_a_missing_choice_comes_back_to_the_selection_page(client):
    data = {"recipe": "50; Haferflocken\n50; Milch", "choice-0": "1"}
    response = client.post("/result", data=data)
    assert response.status_code == 400
    body = response.get_data(as_text=True)
    assert "Pick a database entry for: Milch" in body
    assert 'name="choice-1"' in body  # the radios are there to fix it with


def test_the_form_is_prefilled_when_going_back(client):
    body = client.post("/", data={"recipe": "50; Hafer"}).get_data(as_text=True)
    assert "50; Hafer" in body


def test_choose_carries_the_product_labels_forward(client):
    data = {"recipe": "50; Haferflocken", "product": "Müsli", "manufacturer": "Eigenbau"}
    body = client.post("/choose", data=data).get_data(as_text=True)
    assert 'name="product" value="Müsli"' in body
    assert 'name="manufacturer" value="Eigenbau"' in body


def test_result_prints_the_product_labels(client):
    data = {
        "recipe": "100; Hafer",
        "choice-0": "1",
        "product": "Müsli",
        "manufacturer": "Eigenbau",
    }
    body = client.post("/result", data=data).get_data(as_text=True)
    maker = body.index('<p class="manufacturer">Eigenbau</p>')
    assert maker < body.index('<p class="product">Müsli</p>')  # the maker on top


def test_result_prints_a_product_without_a_manufacturer(client):
    """Either label on its own, with nothing left standing in for the other."""
    data = {"recipe": "100; Hafer", "choice-0": "1", "product": "Müsli", "manufacturer": ""}
    body = client.post("/result", data=data).get_data(as_text=True)
    assert '<p class="product">Müsli</p>' in body
    assert 'class="manufacturer"' not in body
