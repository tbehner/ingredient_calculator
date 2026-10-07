"""Web frontend routes."""

from flask import Blueprint, abort, render_template

from . import controllers

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    return render_template("index.html")

