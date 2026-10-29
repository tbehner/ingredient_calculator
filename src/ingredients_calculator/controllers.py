"""Controllers: the logic between the routes and the database.

Routes in `views.py` stay thin — they map URLs to a controller call and hand the
result to a template. Anything that decides *what* to show lives here.
"""

from collections.abc import Iterable, Mapping
from dataclasses import replace

from . import db
from . import models
from . import search

#: Line labels that are weights rather than ingredients: the gross weight of
#: the filled container and the tare weight of the empty one.
TOTAL_LABEL = "total"
CONTAINER_LABEL = "container"

#: Database matches offered per ingredient on the selection page.
CHOICES_PER_INGREDIENT = 5

#: `Ingredient.id` of an ingredient whose database match is not chosen yet.
UNRESOLVED = -1


class InputError(ValueError):
    """Something in the user's input needs fixing, with a message saying what."""


def parse_recipe(text: str) -> models.Recipe:
    """Parses the `amount; name` lines of the input form.

    Amounts are grams, with either `.` or `,` as the decimal mark and an
    optional `g`. Blank lines and `#` comments are skipped. `total` and
    `container` are reserved labels; without a `total` line the ingredients are
    taken to add up to the whole product.
    """
    ingredients: list[models.Ingredient] = []
    total: float | None = None
    container = 0.0

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        amount, label = _parse_line(line, lineno)
        if label.casefold() == TOTAL_LABEL:
            total = amount
        elif label.casefold() == CONTAINER_LABEL:
            container = amount
        else:
            ingredients.append(
                models.Ingredient(
                    id=UNRESOLVED, name=label, inp_text=line, amount=amount
                )
            )

    if not ingredients:
        raise InputError("Add at least one `amount; ingredient` line.")

    if total is None:
        total = sum(ingredient.amount for ingredient in ingredients) + container
    if total - container <= 0:
        raise InputError(
            f"The total of {total:g}g has to be more than the container's {container:g}g."
        )

    return models.Recipe(ingredients=ingredients, total=total, container=container)


def _parse_line(line: str, lineno: int) -> tuple[float, str]:
    """Splits one `amount; name` line into its grams and its label."""
    amount, separator, label = line.partition(";")
    if not separator or not label.strip():
        raise InputError(f"Line {lineno} is not `amount; name`: {line!r}")

    number = amount.strip().removesuffix("g").removesuffix("G").strip()
    try:
        grams = float(number.replace(",", "."))
    except ValueError:
        raise InputError(
            f"Line {lineno}: {amount.strip()!r} is not an amount in grams."
        ) from None
    if grams < 0:
        raise InputError(f"Line {lineno}: {grams:g}g is negative.")

    return grams, label.strip()


def foods_by_id(ids: Iterable[int]) -> dict[int, models.Food]:
    """Looks up the chosen `food` rows, keyed by id.

    Ids with no row are absent from the result, and a NULL macro counts as zero
    — the BLS data leaves some of them unmeasured.
    """
    unique = list(dict.fromkeys(ids))
    if not unique:
        return {}

    rows = db.query(
        "SELECT id, description, fat, protein, carbs FROM food "
        f"WHERE id IN ({','.join('?' * len(unique))})",
        tuple(unique),
    )
    return {
        row["id"]: models.Food(
            id=row["id"],
            description=row["description"],
            macros=models.Macros(
                fatt=row["fat"] or 0.0,
                prot=row["protein"] or 0.0,
                carbs=row["carbs"] or 0.0,
            ),
        )
        for row in rows
    }

def normalized_ingredients(
    ingredients: list[models.Ingredient], total: float, container: float
) -> list[models.Ingredient]:
    """Returns the ingredients normalized to 100g.

    `total` is the gross weight in grams, `container` the tare weight of the
    container; each returned amount is the grams of that ingredient per 100g of
    net product.
    """
    net_total = total - container
    if net_total <= 0:
        raise ValueError(f"net weight must be positive, got {net_total}g")

    return [
        replace(ingredient, amount=ingredient.amount / net_total * 100)
        for ingredient in ingredients
    ]

def total_macros(
    ingredients: list[models.Ingredient], macros: dict[int, models.Macros]
) -> models.Macros:
    """Macros of the whole product, summed over its ingredients.

    `macros` maps an ingredient id to its macros per 100g. The result is in the
    same terms as the amounts passed in: normalized ingredients give macros per
    100g of product, raw amounts give macros for the whole batch.
    """
    total = models.Macros(fatt=0.0, prot=0.0, carbs=0.0)
    for ingredient in ingredients:
        per_100g = macros[ingredient.id]
        factor = ingredient.amount / 100
        total.fatt += per_100g.fatt * factor
        total.prot += per_100g.prot * factor
        total.carbs += per_100g.carbs * factor
    return total


def choice_context(text: str) -> dict:
    """Context for the selection page: each ingredient with its best matches."""
    recipe = parse_recipe(text)
    matches = [
        (ingredient, search.search(ingredient.name, limit=CHOICES_PER_INGREDIENT))
        for ingredient in recipe.ingredients
    ]
    return {
        "recipe_text": text,
        "recipe": recipe,
        "matches": matches,
        "unmatched": [ingredient for ingredient, hits in matches if not hits],
    }


def result_context(text: str, chosen_ids: Mapping[int, str]) -> dict:
    """Context for the result page, from the input text and one id per ingredient.

    `chosen_ids` maps the position of an ingredient in `text` to the id of the
    `food` row picked for it.
    """
    recipe = parse_recipe(text)
    resolved = _resolve(recipe.ingredients, chosen_ids)

    foods = foods_by_id(ingredient.id for ingredient in resolved)
    unknown = [ingredient for ingredient in resolved if ingredient.id not in foods]
    if unknown:
        raise InputError(
            "No database entry for: "
            + ", ".join(ingredient.name for ingredient in unknown)
        )

    macros = {food_id: food.macros for food_id, food in foods.items()}
    per_100g = normalized_ingredients(resolved, recipe.total, recipe.container)

    return {
        "recipe_text": text,
        "recipe": recipe,
        "net_total": recipe.total - recipe.container,
        "rows": [
            {
                "name": ingredient.name,
                "food": foods[ingredient.id],
                "grams": ingredient.amount,
                "share": normalized.amount,
            }
            for ingredient, normalized in zip(resolved, per_100g)
        ],
        "per_100g": total_macros(per_100g, macros),
        "batch": total_macros(resolved, macros),
    }


def _resolve(
    ingredients: list[models.Ingredient], chosen_ids: Mapping[int, str]
) -> list[models.Ingredient]:
    """Attaches the chosen `food` id to each ingredient, by position."""
    resolved = []
    missing = []
    for position, ingredient in enumerate(ingredients):
        try:
            food_id = int(chosen_ids[position])
        except (KeyError, TypeError, ValueError):
            missing.append(ingredient.name)
        else:
            resolved.append(replace(ingredient, id=food_id))

    if missing:
        raise InputError("Pick a database entry for: " + ", ".join(missing))
    return resolved

