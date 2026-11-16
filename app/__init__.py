import logging
import secrets

from flask import Flask, redirect, session, url_for

from config import Config, configuration_problems


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)

    if test_config:
        app.config.update(test_config)
    else:
        errors, warnings = configuration_problems(app.config)

        if errors:
            raise RuntimeError(
                "Configuration problems:\n- " + "\n- ".join(errors)
            )

        if not app.config.get("SECRET_KEY"):
            app.config["SECRET_KEY"] = secrets.token_hex(32)
            warnings.append(
                "SECRET_KEY is not set. A temporary key is being used, "
                "so logins will reset on every restart."
            )

        logging.basicConfig(level=logging.INFO)

        for message in warnings:
            app.logger.warning(message)

    from app.services.formatting import format_count, format_inr
    from app.playground.llm import build_llm
    from app.playground.net import SafeFetcher
    from app.playground.search import build_search
    from app.playground.store import MemoryStore, MySqlStore
    from app.playground.supervisor import Supervisor
    from app.database import get_db_connection

    class _Services:
        pass

    if app.config.get("PLAYGROUND_STORE") == "memory" or app.config.get("TESTING"):
        store = MemoryStore()
    else:
        def connect():
            with app.app_context():
                return get_db_connection()
        store = MySqlStore(connect)
        if not app.config.get("SKIP_PLAYGROUND_STORE_CHECK"):
            with app.app_context():
                store.check()

    fetcher = SafeFetcher(app.config.get("USER_AGENT", "ResquoraResearchBot/1.0"),
                          timeout=app.config.get("HTTP_TIMEOUT_SECONDS", 12))
    services = _Services()
    services.supervisor = Supervisor(build_search(app.config), fetcher, build_llm(app.config), store, dict(app.config))
    app.extensions["playground"] = services

    app.jinja_env.filters["inr"] = format_inr
    app.jinja_env.filters["number"] = format_count

    from app.routes.auth_routes import auth_bp
    from app.playground.routes import pg_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(pg_bp)

    @app.route("/")
    def home():
        if "user_id" in session:
            return redirect(url_for("pg.playground"))

        return redirect(url_for("auth.login"))

    @app.route("/health")
    def health():
        return {
            "status": "success",
            "message": "Multi-Agent Product Platform is running",
        }

    @app.route("/database-test")
    def database_test():
        from app.database import get_db_connection

        connection = None

        try:
            connection = get_db_connection()

            with connection.cursor() as cursor:
                cursor.execute("SELECT DATABASE() AS database_name")
                result = cursor.fetchone()

            return {
                "status": "success",
                "message": "MySQL database connected successfully",
                "database": result["database_name"],
            }

        except Exception as error:
            body = {
                "status": "error",
                "message": "Database connection failed",
            }

            if app.config.get("DEBUG"):
                body["error"] = str(error)

            return body, 500

        finally:
            if connection:
                connection.close()

    return app
