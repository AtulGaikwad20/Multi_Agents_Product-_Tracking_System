import os
from collections.abc import Mapping
from typing import Callable, Protocol, cast

from flask import Flask

import app


class _AppModule(Protocol):
    create_app: Callable[[Mapping[str, object] | None], Flask]


def create_app(test_config: Mapping[str, object] | None = None) -> Flask:
    return cast(_AppModule, app).create_app(test_config)


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_PORT", "5000")),
        debug=cast(bool, app.config["DEBUG"]),
    )
