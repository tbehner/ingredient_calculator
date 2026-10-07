"""Flask application factory."""

from flask import Flask

from . import db, views


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    if config:
        app.config.update(config)

    db.init_app(app)
    app.register_blueprint(views.bp)

    return app
