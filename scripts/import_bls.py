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

#: Nutrient columns to pull in, db column name -> xlsx header label. Order
#: matches the `INSERT` below.
NUTRIENTS = {
    "fat": "FAT Fett [g/100g]",
    "satfat": "FASAT Fettsäuren, gesättigt, gesamt [g/100g]",
    "protein": "PROT625 Protein (Nx6,25) [g/100g]",
    "carbs": "CHO Kohlenhydrate, verfügbar [g/100g]",
    "fiber": "FIBT Ballaststoffe, gesamt [g/100g]",
    "sugar": "SUGAR Zucker (Mono- und Disaccharide), gesamt [g/100g]",
}

COLUMNS = {
    "bls_code": "BLS Code",
    "description": "Lebensmittelbezeichnung",
    **NUTRIENTS,
}

SHEET_NAME = "BLS_4_0_Daten_2025_DE"

SCHEMA = f"""
CREATE TABLE food (
    id          INTEGER PRIMARY KEY,
    bls_code    TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    {",\n    ".join(f"{name:<11} REAL" for name in NUTRIENTS)}
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


def read_rows(xlsx_path: Path) -> list[tuple]:
    workbook = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    sheet = workbook[SHEET_NAME]
    row_iter = sheet.iter_rows(values_only=True)

    header = next(row_iter)
    index = {name: header.index(label) for name, label in COLUMNS.items()}

    rows = []
    for row in row_iter:
        values = {name: to_float(row[index[name]]) for name in NUTRIENTS}
        # Total fat and the fatty-acid breakdown come from different
        # measurements, so a handful of entries have saturated fat a shade
        # above total fat (e.g. rounding fat down to 0 while keeping a trace
        # of saturated fat). Capping it at the total keeps that total a valid
        # upper bound for the app's "sugar/satfat are part of carbs/fat" check.
        if values["satfat"] is not None and values["fat"] is not None:
            values["satfat"] = min(values["satfat"], values["fat"])
        rows.append((
            row[index["bls_code"]],
            row[index["description"]].strip(),
            *(values[name] for name in NUTRIENTS),
        ))
    return rows


def write_db(rows: list[tuple], db_path: Path) -> None:
    columns = ", ".join(COLUMNS)
    placeholders = ", ".join("?" * len(COLUMNS))

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("DROP TABLE IF EXISTS food")
        connection.executescript(SCHEMA)
        connection.executemany(
            f"INSERT INTO food ({columns}) VALUES ({placeholders})",
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
