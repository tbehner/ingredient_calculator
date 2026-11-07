"""Models."""

from dataclasses import dataclass

@dataclass
class Ingredient:
    id: int
    name: str
    inp_text: str
    amount: float

#: Energy per gram of fat, carbohydrate and protein, in kJ and kcal. The
#: conversion factors the label has to use, from Anhang XIV of the LMIV
#: (Verordnung (EU) Nr. 1169/2011).
ENERGY_KJ = {"fatt": 37.0, "carbs": 17.0, "prot": 17.0}
ENERGY_KCAL = {"fatt": 9.0, "carbs": 4.0, "prot": 4.0}

@dataclass
class Macros:
    fatt: float
    satfat: float
    prot: float
    carbs: float
    fiber: float
    sugar: float

    def __post_init__(self) -> None:
        # Sugars and saturated fat are sub-parts of carbs and fat ("davon
        # Zucker"/"davon gesättigte Fettsäuren" on the label), so neither can
        # add up to more than the nutrient it is part of. A tiny tolerance
        # absorbs float error from summing ingredients.
        if self.sugar > self.carbs + 1e-9:
            raise ValueError(
                f"sugar ({self.sugar}g) exceeds carbs ({self.carbs}g), but sugar is part of carbs"
            )
        if self.satfat > self.fatt + 1e-9:
            raise ValueError(
                f"satfat ({self.satfat}g) exceeds fat ({self.fatt}g), but satfat is part of fat"
            )

    @property
    def kj(self) -> float:
        """Energy in kJ, as the label declares it."""
        return sum(getattr(self, name) * factor for name, factor in ENERGY_KJ.items())

    @property
    def kcal(self) -> float:
        """Energy in kcal, as the label declares it."""
        return sum(getattr(self, name) * factor for name, factor in ENERGY_KCAL.items())

@dataclass
class Food:
    """A row of the `food` table: a description and its macros per 100g."""
    id: int
    description: str
    macros: Macros

@dataclass
class Recipe:
    """What the user typed into the form, parsed.

    `total` is the gross weight of the filled container, `container` its tare
    weight; both in grams.
    """
    ingredients: list[Ingredient]
    total: float
    container: float
