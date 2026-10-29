"""Web frontend routes.

Three steps, each a POST carrying the typed recipe forward in a hidden field:
enter the ingredients, pick the database entry for each, read off the macros.
The database is read-only, so nothing is stored between steps.
"""

from flask import Blueprint, render_template, request

from . import controllers

bp = Blueprint("main", __name__)

#: Fields that only label the product — what it is and who makes it. They take
#: no part in the calculation, they ride along to be printed on the result.
LABEL_FIELDS = ("product", "manufacturer")


def labels() -> dict[str, str]:
    """The product labels posted with the recipe, to carry on to the next step."""
    return {field: request.form.get(field, "") for field in LABEL_FIELDS}


@bp.route("/", methods=["GET", "POST"])
def index():
    """The input form, prefilled when coming back from a later step."""
    return render_template("index.html", recipe_text=request.form.get("recipe", ""), **labels())


@bp.post("/choose")
def choose():
    text = request.form.get("recipe", "")
    try:
        context = controllers.choice_context(text)
    except controllers.InputError as error:
        return render_template("index.html", recipe_text=text, error=str(error), **labels()), 400
    return render_template("choose.html", **context, **labels())


#: Prefix of the radio group per ingredient; see `templates/choose.html`.
CHOICE_PREFIX = "choice-"


def chosen_ids() -> dict[int, str]:
    """The picked food ids, keyed by the position of their ingredient.

    Keyed rather than listed because an ingredient left unpicked submits
    nothing at all, and the remaining ids must not slide up into its place.
    """
    picked = {}
    for field, value in request.form.items():
        if field.startswith(CHOICE_PREFIX):
            position = field.removeprefix(CHOICE_PREFIX)
            if position.isdigit():
                picked[int(position)] = value
    return picked


@bp.post("/result")
def result():
    text = request.form.get("recipe", "")
    try:
        context = controllers.result_context(text, chosen_ids())
    except controllers.InputError as error:
        # A bad selection belongs back on the selection page, a bad recipe on
        # the form; `choice_context` reparses and tells the two apart.
        try:
            context = controllers.choice_context(text)
        except controllers.InputError:
            return (
                render_template("index.html", recipe_text=text, error=str(error), **labels()),
                400,
            )
        return render_template("choose.html", error=str(error), **context, **labels()), 400
    return render_template("result.html", **context, **labels())
