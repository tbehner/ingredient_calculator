import sqlite3

import pytest

from ingredients_calculator.app import create_app


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "test.sqlite3"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE ingredients (id INTEGER PRIMARY KEY, name TEXT);
        INSERT INTO ingredients (name) VALUES ('oats'), ('milk');
        """
    )
    con.commit()
    con.close()
    return path


@pytest.fixture
def client(database):
    app = create_app({"DATABASE": str(database), "TESTING": True})
    return app.test_client()

