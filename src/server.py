"""Flask server for the interactive DMCA dashboard."""

import logging
import os
import re
import tempfile
import threading

from flask import Flask, jsonify, request, abort

from main import load_domains, fetch_all_reports
from src.transparency_api import fetch_domain_report
from src.dashboard import generate_dashboard
from src.database import get_connection, load_domain_report, save_domain_report, is_stale

logger = logging.getLogger(__name__)

# Labels must start with alnum, segments separated by dots, no trailing hyphens
_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$")
_MAX_DOMAIN_LEN = 253

def _normalize_domain(raw):
    """Normalize and validate a domain string. Returns (domain, error_msg)."""
    domain = raw.strip().lower()
    if len(domain) > _MAX_DOMAIN_LEN or not _DOMAIN_RE.match(domain):
        return None, "Invalid domain format"
    return domain, None


# Generation status shared across requests
_status_lock = threading.Lock()
_status = {"running": False, "current": 0, "total": 0, "domain": "", "done": False, "error": None}


def _read_domains(path):
    """Read domains from file, returning list."""
    try:
        return load_domains(path)
    except FileNotFoundError:
        return []


def _write_domains(path, domains):
    """Atomically write domains list back to file, preserving all comments."""
    comments = []
    try:
        with open(path) as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("#") or stripped == "":
                    comments.append(line)
    except FileNotFoundError:
        pass
    dir_name = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp", prefix=".domains_")
    try:
        with os.fdopen(fd, "w") as f:
            for line in comments:
                f.write(line)
            for d in domains:
                f.write(d + "\n")
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


def create_app(domains_path, settings, max_age=24):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 16  # 16 KB max request body

    @app.before_request
    def _localhost_only():
        """Reject requests not originating from localhost."""
        remote = request.remote_addr
        if remote not in ("127.0.0.1", "::1"):
            abort(403)

    @app.route("/")
    def index():
        """Serve live dashboard with management UI."""
        domains = _read_domains(domains_path)
        conn = get_connection()
        reports = []
        for domain in domains:
            cached = load_domain_report(conn, domain)
            if cached is not None:
                reports.append(cached)
        conn.close()
        html = generate_dashboard(reports, server_mode=True)
        return html

    @app.route("/api/domains", methods=["GET"])
    def get_domains():
        domains = _read_domains(domains_path)
        return jsonify(domains)

    @app.route("/api/domains", methods=["POST"])
    def add_domain():
        data = request.get_json(silent=True)
        if not data or not data.get("domain"):
            return jsonify({"error": "Missing 'domain' field"}), 400

        domain, err = _normalize_domain(data["domain"])
        if err:
            return jsonify({"error": err}), 400

        domains = _read_domains(domains_path)
        if domain in domains:
            return jsonify({"error": "Domain already exists"}), 409

        domains.append(domain)
        _write_domains(domains_path, domains)

        # Fetch report for the new domain immediately so it shows data on reload
        try:
            page_size = settings.get("requests_page_size", 100)
            max_retries = settings.get("max_retries", 3)
            report = fetch_domain_report(domain, page_size=page_size, max_retries=max_retries)
            conn = get_connection()
            save_domain_report(conn, report)
            conn.close()
        except Exception:
            logger.warning("Added domain %s but initial fetch failed", domain)

        return jsonify({"ok": True, "domain": domain}), 201

    @app.route("/api/domains/<domain>", methods=["DELETE"])
    def delete_domain(domain):
        domain, err = _normalize_domain(domain)
        if err:
            return jsonify({"error": err}), 400
        domains = _read_domains(domains_path)
        if domain not in domains:
            return jsonify({"error": "Domain not found"}), 404

        domains.remove(domain)
        _write_domains(domains_path, domains)

        # Remove from DB
        conn = get_connection()
        with conn:
            conn.execute("DELETE FROM notices WHERE domain = ?", (domain,))
            conn.execute("DELETE FROM domain_stats WHERE domain = ?", (domain,))
        conn.close()

        return jsonify({"ok": True})

    @app.route("/api/generate", methods=["POST"])
    def generate():
        with _status_lock:
            if _status["running"]:
                return jsonify({"error": "Generation already in progress"}), 409
            _status.update(running=True, current=0, total=0, domain="", done=False, error=None)

        def _run():
            conn = None
            try:
                domains = _read_domains(domains_path)
                with _status_lock:
                    _status["total"] = len(domains)
                conn = get_connection()

                def _progress(i, total, domain, cached):
                    with _status_lock:
                        _status["current"] = i
                        _status["total"] = total
                        _status["domain"] = domain

                fetch_all_reports(
                    domains, settings, conn,
                    force_refresh=True,
                    max_age=max_age,
                    progress_cb=_progress,
                )
                with _status_lock:
                    _status["done"] = True
            except Exception as e:
                logger.exception("Report generation failed")
                with _status_lock:
                    _status["error"] = "Report generation failed"
                    _status["done"] = True
            finally:
                if conn:
                    conn.close()
                with _status_lock:
                    _status["running"] = False

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return jsonify({"status": "started"})

    @app.route("/api/status", methods=["GET"])
    def status():
        with _status_lock:
            return jsonify({**_status})

    return app
