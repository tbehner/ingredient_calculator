import sqlite3

import pytest

from ingredients_calculator import controllers
from ingredients_calculator.models import Ingredient, Macros

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
