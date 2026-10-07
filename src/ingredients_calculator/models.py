"""Models."""

from dataclasses import dataclass

@dataclass
class Ingredient:
    id: int
    name: str
    inp_text: str
    amount: float

@dataclass
class Macros:
    fatt: float
    prot: float
    carbs: float
