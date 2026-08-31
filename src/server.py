"""Read-only Flask application for the live Lumen DMCA dashboard."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, request

from src.dashboard import render_dashboard
from src.database import DB_PATH, load_dashboard_data


def create_app(db_path: str | None = None) -> Flask:
    app = Flask(__name__)
    selected_db = db_path or os.environ.get("DMCA_DB_PATH", DB_PATH)

    @app.before_request
    def _proxy_guard():
        if os.environ.get("ALLOW_PROXY") == "1":
            return None
        if request.remote_addr not in {"127.0.0.1", "::1"}:
            return "Forbidden", 403
        return None

    @app.after_request
    def _security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    def _load():
        return load_dashboard_data(selected_db)

    @app.get("/")
    def index():
        try:
            return render_dashboard(_load())
        except (FileNotFoundError, sqlite3.DatabaseError):
            app.logger.exception("DMCA database unavailable")
            return (
                "<!doctype html><html lang='fr'><meta charset='utf-8'>"
                "<title>DMCA Monitor</title><body><h1>DMCA Monitor</h1>"
                "<p>La base de données est momentanément indisponible.</p></body></html>",
                503,
            )

    @app.get("/api/notices")
    def notices_api():
        try:
            data = _load()
        except (FileNotFoundError, sqlite3.DatabaseError):
            return jsonify({"error": "database_unavailable"}), 503
        return jsonify(data)

    @app.get("/health")
    def health():
        try:
            data = _load()
        except (FileNotFoundError, sqlite3.DatabaseError):
            return jsonify({"status": "error", "database": str(Path(selected_db).name)}), 503
        return jsonify({
            "status": "ok",
            "database": str(Path(selected_db).name),
            "notices": data["summary"]["total_notices"],
            "site_scopes": data["summary"]["site_scopes"],
            "search_domains": data["summary"]["search_domains"],
            "synced_at": data["metadata"].get("synced_at"),
        })

    return app
