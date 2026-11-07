# /// script
# requires-python = ">=3.12"
# dependencies = ["openpyxl>=3.1"]
# ///
"""Rebuild bls.sqlite3's `food` table from the official BLS 4.0 Excel export.

Usage:
    uv run scripts/import_bls.py \\
        BLS_4_0_2025_DE/BLS_4_0_Daten_2025_DE.xlsx bls.sqlite3
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import openpyxl

COLUMNS = {
    "bls_code": "BLS Code",
    "description": "Lebensmittelbezeichnung",
    "fat": "FAT Fett [g/100g]",
    "protein": "PROT625 Protein (Nx6,25) [g/100g]",
    "carbs": "CHO Kohlenhydrate, verfügbar [g/100g]",
}

SHEET_NAME = "BLS_4_0_Daten_2025_DE"

SCHEMA = """
CREATE TABLE food (
    id          INTEGER PRIMARY KEY,
    bls_code    TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    fat         REAL,
    protein     REAL,
    carbs       REAL
);
CREATE INDEX food_description_idx ON food(description);
"""


def to_float(value: object) -> float | None:
    """Parse a BLS nutrient cell.

    Besides numbers, the sheet uses a few textual placeholders: "-" means the
    value was not determined (kept as NULL), while "TR", "<LOD", "<LOQ" and
    "<LOD or <LOQ" mark a trace amount below the detection/quantification
    limit, which the original export rounds down to 0.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 4)
    return None if value.strip() == "-" else 0.0


def read_rows(xlsx_path: Path) -> list[tuple[str, str, float | None, float | None, float | None]]:
    workbook = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    sheet = workbook[SHEET_NAME]
    row_iter = sheet.iter_rows(values_only=True)

    header = next(row_iter)
    index = {name: header.index(label) for name, label in COLUMNS.items()}

    rows = []
    for row in row_iter:
        rows.append((
            row[index["bls_code"]],
            row[index["description"]].strip(),
            to_float(row[index["fat"]]),
            to_float(row[index["protein"]]),
            to_float(row[index["carbs"]]),
        ))
    return rows


def write_db(rows: list[tuple], db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("DROP TABLE IF EXISTS food")
        connection.executescript(SCHEMA)
        connection.executemany(
            "INSERT INTO food (bls_code, description, fat, protein, carbs) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx", type=Path, help="Path to BLS_4_0_Daten_2025_DE.xlsx")
    parser.add_argument("db", type=Path, help="Path to the SQLite database to (re)create")
    args = parser.parse_args()

    rows = read_rows(args.xlsx)
    write_db(rows, args.db)
    print(f"Imported {len(rows)} foods from {args.xlsx} into {args.db}", file=sys.stderr)


if __name__ == "__main__":
    main()