from __future__ import annotations

import os
import secrets
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from .auth import bp as auth_bp
from .db import DatabaseError, get_database, initialize_database
from .market_api import bp as market_bp
from .password_reset import bp as password_reset_bp
from .state_api import bp as state_bp

ROOT = Path(__file__).resolve().parents[1]


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, static_folder=str(ROOT / "static"), static_url_path="/static")
    app.config.update(
        MAX_CONTENT_LENGTH=4 * 1024 * 1024,
        SECRET_KEY=os.getenv("SESSION_SECRET") or secrets.token_hex(32),
        JSON_SORT_KEYS=False,
    )
    if test_config:
        app.config.update(test_config)

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    initialize_database()
    app.register_blueprint(auth_bp)
    app.register_blueprint(password_reset_bp)
    app.register_blueprint(state_bp)
    app.register_blueprint(market_bp)

    @app.before_request
    def same_origin_writes():
        if request.method in {"GET", "HEAD", "OPTIONS"} or not request.path.startswith("/api/"):
            return None
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jsonify({"error": "Cross-origin request rejected."}), 403
        return None

    @app.errorhandler(DatabaseError)
    def database_error(error: DatabaseError):
        app.logger.error("Database operation failed: %s", error)
        return jsonify({"error": "The database is temporarily unavailable. Please try again."}), 503

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
"Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        return response

    @app.get("/")
    def index():
        response = send_from_directory(app.static_folder, "index.html")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.get("/ready")
    def ready():
        get_database().query_one("SELECT 1 AS ok")
        return jsonify({"ok": True, "database": "connected"})

    return app
