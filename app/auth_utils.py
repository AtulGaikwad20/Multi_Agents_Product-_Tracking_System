from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from flask import redirect, session, url_for
from werkzeug.wrappers import Response


_P = ParamSpec("_P")
_R = TypeVar("_R")


def login_required(
    view: Callable[_P, _R],
) -> Callable[_P, _R | Response]:
    @wraps(view)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R | Response:
        if "user_id" not in session:
            return redirect(url_for("auth.login"))

        return view(*args, **kwargs)

    return wrapper
