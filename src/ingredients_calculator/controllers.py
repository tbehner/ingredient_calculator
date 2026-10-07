"""Controllers: the logic between the routes and the database.

Routes in `views.py` stay thin — they map URLs to a controller call and hand the
result to a template. Anything that decides *what* to show lives here.
"""

from dataclasses import replace

from . import db
from . import models

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

