import re

from flask import Blueprint, render_template, request, redirect, url_for, session
from pymysql.err import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash

from app.database import db_cursor


auth_bp = Blueprint("auth", __name__)

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "GET":
        if "user_id" in session:
            return redirect(url_for("pg.playground"))
        return render_template("register.html")

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not name or not email or not password:
        return render_template(
            "register.html",
            error="All fields are required."
        ), 400

    if len(name) < 2 or len(name) > 100:
        return render_template(
            "register.html",
            error="Name must be between 2 and 100 characters."
        ), 400

    if len(email) > 255 or not EMAIL_PATTERN.match(email):
        return render_template(
            "register.html",
            error="Enter a valid email address."
        ), 400

    if len(password) < 6:
        return render_template(
            "register.html",
            error="Password must be at least 6 characters."
        ), 400

    if len(password) > 128:
        return render_template(
            "register.html",
            error="Password must be 128 characters or fewer."
        ), 400

    if password != confirm_password:
        return render_template(
            "register.html",
            error="Passwords do not match."
        ), 400

    try:
        with db_cursor(commit=True) as cursor:

            cursor.execute(
                "SELECT user_id FROM users WHERE email = %s",
                (email,)
            )

            if cursor.fetchone():
                return render_template(
                    "register.html",
                    error="An account with this email already exists."
                ), 409

            cursor.execute(
                """
                INSERT INTO users
                (name, email, password_hash)
                VALUES (%s, %s, %s)
                """,
                (name, email, generate_password_hash(password))
            )

        return redirect(url_for("auth.login", registered=1))

    except IntegrityError:
        return render_template(
            "register.html",
            error="An account with this email already exists."
        ), 409

    except Exception:
        return render_template(
            "register.html",
            error="Registration failed. Please try again."
        ), 500


@auth_bp.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "GET":
        if "user_id" in session:
            return redirect(url_for("pg.playground"))

        success = None

        if request.args.get("registered"):
            success = "Your account was created. Please log in."

        return render_template("login.html", success=success)

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not email or not password:
        return render_template(
            "login.html",
            error="Email and password are required."
        ), 400

    try:
        with db_cursor() as cursor:

            cursor.execute(
                """
                SELECT user_id, name, email, password_hash,
                       role, account_status
                FROM users
                WHERE email = %s
                """,
                (email,)
            )

            user = cursor.fetchone()

        if not user:
            return render_template(
                "login.html",
                error="Invalid email or password."
            ), 401

        if user["account_status"] != "active":
            return render_template(
                "login.html",
                error="Your account is disabled."
            ), 403

        if not check_password_hash(user["password_hash"], password):
            return render_template(
                "login.html",
                error="Invalid email or password."
            ), 401

        session.clear()

        session["user_id"] = user["user_id"]
        session["name"] = user["name"]
        session["email"] = user["email"]
        session["role"] = user["role"]

        return redirect(url_for("pg.playground"))

    except Exception:
        return render_template(
            "login.html",
            error="Login failed. Please try again."
        ), 500


@auth_bp.route("/dashboard")
def dashboard():
    # The old dashboard was removed: everything it offered now lives in the playground.
    # Kept as a redirect so old bookmarks still work.
    return redirect(url_for("pg.playground"))


@auth_bp.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("auth.login"))
