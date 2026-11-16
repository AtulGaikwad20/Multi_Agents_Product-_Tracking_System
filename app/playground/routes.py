"""HTTP API for the AI Agent Playground web application."""
from __future__ import annotations

import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from functools import wraps

from flask import (Blueprint, Response, current_app, jsonify, make_response, redirect, render_template, request,
                   send_from_directory, session, stream_with_context, url_for)

from . import location as loc
from .capabilities import PROFILES
from .reports import FORMATS, export
from .store import StoreError
from .supervisor import RunLimitError

pg_bp = Blueprint("pg", __name__)

MAX_TEXT = 500
START_LIMIT = (20, 600)  # 20 runs per 10 minutes per user
_hits = defaultdict(deque)

LANGUAGES = [
    ("en-IN", "English (India)"), ("hi-IN", "हिन्दी"), ("mr-IN", "मराठी"), ("ta-IN", "தமிழ்"),
    ("te-IN", "తెలుగు"), ("bn-IN", "বাংলা"), ("gu-IN", "ગુજરાતી"), ("kn-IN", "ಕನ್ನಡ"),
    ("ml-IN", "മലയാളം"), ("pa-IN", "ਪੰਜਾਬੀ"), ("en-US", "English (US)"), ("en-GB", "English (UK)"),
    ("es-ES", "Español"), ("fr-FR", "Français"), ("de-DE", "Deutsch"), ("ar-SA", "العربية"),
    ("ja-JP", "日本語"), ("zh-CN", "中文"),
]


def svc():
    return current_app.extensions["playground"]


def _csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def api(view):
    """Auth + CSRF for JSON endpoints (401/403 as JSON, never redirects)."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify(error="Please sign in again."), 401
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            sent = request.headers.get("X-CSRF-Token", "")
            if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
                return jsonify(error="Security check failed. Reload the page and try again."), 403
        return view(*args, **kwargs)
    return wrapper


def _rate_ok(user_id):
    limit, window = START_LIMIT
    now = time.monotonic()
    q = _hits[user_id]
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def _text_from(body):
    text = " ".join(str((body or {}).get("text", "")).split())
    if not text:
        return None, "Please say or type what you are looking for."
    if len(text) > MAX_TEXT:
        return None, f"Please keep requests under {MAX_TEXT} characters."
    return text, None


def _lang_from(body):
    value = str((body or {}).get("language", "en-IN"))
    return value if re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?", value) else "en-IN"


def _search_usage(user_id):
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    limit = int(current_app.config["SEARCH_LIMIT_PER_MONTH"])
    used = svc().supervisor.store.count_since(user_id, month_start)
    return {"used": used, "limit": limit, "remaining": max(0, limit - used)}


# ----------------------------------------------------------------- pages / PWA
@pg_bp.route("/playground")
def playground():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    return render_template("playground.html", name=session["name"], csrf=_csrf_token(), languages=LANGUAGES)


@pg_bp.route("/product-search")
def legacy_search():
    return redirect(url_for("pg.playground"))


@pg_bp.route("/sw.js")
def service_worker():
    resp = send_from_directory(current_app.static_folder, "pwa/sw.js", mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@pg_bp.route("/manifest.webmanifest")
def manifest():
    return send_from_directory(current_app.static_folder, "pwa/manifest.webmanifest",
                               mimetype="application/manifest+json")


# --------------------------------------------------------------------- config
@pg_bp.route("/api/config")
@api
def config():
    s = svc()
    return jsonify(
        csrf=_csrf_token(), languages=LANGUAGES,
        status={"search_configured": s.supervisor.search.configured, "llm_available": s.supervisor.llm.available,
                "store": s.supervisor.store.name, "store_persistent": s.supervisor.store.persistent,
                "geocoder_enabled": bool(current_app.config.get("GEOCODER_URL"))},
        agents=[{"key": p.key, "label": p.label, "role": p.role, "kind": p.kind, "avatar": p.avatar,
                 "note": p.note, "hyperlocal": p.hyperlocal} for p in PROFILES],
        location=loc.current(session),
        formats=sorted(FORMATS))


@pg_bp.route("/api/usage")
@api
def search_usage():
    return jsonify(search_usage=_search_usage(session["user_id"]))


# ----------------------------------------------------------------------- runs
@pg_bp.route("/api/runs", methods=["POST"])
@api
def start_run():
    body = request.get_json(silent=True) or {}
    text, error = _text_from(body)
    if error:
        return jsonify(error=error), 400
    if not _rate_ok(session["user_id"]):
        return jsonify(error="Too many searches in a short time. Please wait a few minutes."), 429
    try:
        run = svc().supervisor.start(session["user_id"], text, _lang_from(body), loc.current(session))
    except RunLimitError as e:
        return jsonify(error=str(e)), 429
    except StoreError as e:
        current_app.logger.error("Could not save initial research run: %s", e)
        return jsonify(error=str(e)), 503
    return jsonify(run_id=run.id), 202


@pg_bp.route("/api/runs", methods=["GET"])
@api
def list_runs():
    return jsonify(runs=svc().supervisor.store.list(session["user_id"]))


@pg_bp.route("/api/runs/<run_id>", methods=["GET"])
@api
def get_run(run_id):
    run = svc().supervisor.open_run(session["user_id"], run_id)
    if not run:
        return jsonify(error="Run not found."), 404
    return jsonify(id=run.id, status=run.status, text=run.text, language=run.language,
                   brief=run.brief.to_dict() if run.brief else None, report=run.report, revision=run.revision,
                   events=len(run.log.all()))


@pg_bp.route("/api/runs/<run_id>/events")
@api
def run_events(run_id):
    run = svc().supervisor.open_run(session["user_id"], run_id)
    if not run:
        return jsonify(error="Run not found."), 404
    try:
        after = int(request.headers.get("Last-Event-ID") or request.args.get("after") or 0)
    except ValueError:
        after = 0
    return Response(stream_with_context(run.log.stream(after)), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
                             "Connection": "keep-alive"})


@pg_bp.route("/api/runs/<run_id>/cancel", methods=["POST"])
@api
def cancel_run(run_id):
    run = svc().supervisor.open_run(session["user_id"], run_id)
    if not run:
        return jsonify(error="Run not found."), 404
    svc().supervisor.cancel(run_id)
    return jsonify(ok=True, status=run.status)


@pg_bp.route("/api/runs/<run_id>/followup", methods=["POST"])
@api
def followup(run_id):
    body = request.get_json(silent=True) or {}
    text, error = _text_from(body)
    if error:
        return jsonify(error=error), 400
    if not _rate_ok(session["user_id"]):
        return jsonify(error="Too many requests in a short time. Please wait a few minutes."), 429
    try:
        result = svc().supervisor.followup(session["user_id"], run_id, text, _lang_from(body), loc.current(session))
    except RunLimitError as e:
        return jsonify(error=str(e)), 429
    except StoreError as e:
        current_app.logger.error("Could not save follow-up research run: %s", e)
        return jsonify(error=str(e)), 503
    if result is None:
        return jsonify(error="Run not found."), 404
    return jsonify(result)


@pg_bp.route("/api/runs/<run_id>/export")
@api
def export_run(run_id):
    fmt = request.args.get("format", "pdf").lower()
    if fmt not in FORMATS:
        return jsonify(error="Unsupported format."), 400
    s = svc()
    record = s.supervisor.store.get(session["user_id"], run_id)
    if not record:
        run = s.supervisor.open_run(session["user_id"], run_id)
        record = run.record() if run else None
    if not record or not record.get("report"):
        return jsonify(error="This run has no finished report yet."), 404
    data, mime, ext = export(record, fmt, s.supervisor.fetcher)
    slug = re.sub(r"[^a-z0-9]+", "-", record["report"]["product"].lower()).strip("-")[:40] or "report"
    resp = make_response(data)
    resp.headers["Content-Type"] = mime + ("; charset=utf-8" if mime.startswith("text/") else "")
    resp.headers["Content-Disposition"] = f'attachment; filename="research-{slug}.{ext}"'
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@pg_bp.route("/api/runs/<run_id>", methods=["DELETE"])
@api
def delete_run(run_id):
    svc().supervisor.store.delete(session["user_id"], run_id)
    svc().supervisor.runs.pop(run_id, None)
    return jsonify(ok=True)


# -------------------------------------------------------------------- location
@pg_bp.route("/api/location", methods=["GET"])
@api
def get_location():
    return jsonify(location=loc.current(session))


@pg_bp.route("/api/location", methods=["POST"])
@api
def set_location():
    body = request.get_json(silent=True) or {}
    try:
        if body.get("mode") == "manual":
            result = loc.from_manual(body.get("text"))
        elif body.get("mode") == "device":
            result = loc.from_device(body.get("lat"), body.get("lng"), body.get("precision", "approximate"),
                                     bool(body.get("share_with_geocoder")), current_app.config)
        else:
            return jsonify(error="Unknown location mode."), 400
    except loc.LocationError as e:
        return jsonify(error=e.user_message), 400
    session["pg_location"] = result
    session.modified = True
    return jsonify(location=result)


@pg_bp.route("/api/location", methods=["DELETE"])
@api
def delete_location():
    session.pop("pg_location", None)
    return jsonify(ok=True)


# --------------------------------------------------------------------- headers
@pg_bp.after_app_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "microphone=(self), geolocation=(self), camera=()")
    if request.path == "/playground":
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' https: data:; "
            "connect-src 'self'; media-src 'self'; manifest-src 'self'; worker-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    return response
