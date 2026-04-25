import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, request, send_file, send_from_directory
from dotenv import load_dotenv


app = Flask(__name__, static_folder="static")

# Load local .env automatically for local dev runs.
ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "5000"))
DOWNLOADS_DIR = Path(os.getenv("DOWNLOADS_DIR", "/downloads")).resolve()

QBIT_BASE_URL = os.getenv("QBIT_BASE_URL", "http://localhost:8080").rstrip("/")
QBIT_USER = os.getenv("QBIT_USER", "")
QBIT_PASS = os.getenv("QBIT_PASS", "")

JACKETT_BASE_URL = os.getenv("JACKETT_BASE_URL", "http://localhost:9117").rstrip("/")
JACKETT_API_KEY = os.getenv("JACKETT_API_KEY", "")

qbit_session = requests.Session()
_qbit_authed = False


def _safe_download_path(rel_path: str) -> Path:
    target = (DOWNLOADS_DIR / rel_path).resolve()
    if not str(target).startswith(str(DOWNLOADS_DIR)):
        raise ValueError("Path escapes download directory")
    return target


def _qbit_login():
    global _qbit_authed
    if _qbit_authed:
        return
    response = qbit_session.post(
        f"{QBIT_BASE_URL}/api/v2/auth/login",
        data={"username": QBIT_USER, "password": QBIT_PASS},
        timeout=15,
    )
    response.raise_for_status()
    if "ok." not in response.text.lower():
        raise RuntimeError("qBittorrent login failed")
    _qbit_authed = True


def _qbit_get_torrents():
    _qbit_login()
    response = qbit_session.get(
        f"{QBIT_BASE_URL}/api/v2/torrents/info",
        timeout=30,
    )
    if response.status_code == 403:
        global _qbit_authed
        _qbit_authed = False
        _qbit_login()
        response = qbit_session.get(
            f"{QBIT_BASE_URL}/api/v2/torrents/info",
            timeout=30,
        )
    response.raise_for_status()
    return response.json()

def _qbit_post(path: str, data: dict):
    _qbit_login()
    response = qbit_session.post(
        f"{QBIT_BASE_URL}{path}",
        data=data,
        timeout=30,
    )
    if response.status_code == 403:
        global _qbit_authed
        _qbit_authed = False
        _qbit_login()
        response = qbit_session.post(
            f"{QBIT_BASE_URL}{path}",
            data=data,
            timeout=30,
        )
    response.raise_for_status()
    return response


def _torrent_link_path(torrent: dict) -> str:
    save_path = (torrent.get("save_path") or "").strip()
    content_path = (torrent.get("content_path") or "").strip()
    name = (torrent.get("name") or "").strip()

    if content_path:
        parsed = urlparse(content_path)
        raw_content_path = parsed.path if parsed.scheme else content_path
        candidate = Path(raw_content_path)
        if candidate.is_absolute():
            try:
                rel = candidate.resolve().relative_to(DOWNLOADS_DIR)
                return rel.as_posix()
            except Exception:
                pass

    if save_path and name:
        candidate = Path(save_path) / name
        try:
            rel = candidate.resolve().relative_to(DOWNLOADS_DIR)
            return rel.as_posix()
        except Exception:
            pass

    return name


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/torrents")
def torrents():
    try:
        data = _qbit_get_torrents()
        rows = []
        for item in data:
            rel_path = _torrent_link_path(item)
            rows.append(
                {
                    "hash": item.get("hash", ""),
                    "name": item.get("name", ""),
                    "progress_percent": round(float(item.get("progress", 0)) * 100, 2),
                    "ratio": item.get("ratio", 0),
                    "state": item.get("state", ""),
                    "download_speed": item.get("dlspeed", 0),
                    "upload_speed": item.get("upspeed", 0),
                    "path": rel_path,
                }
            )
        return jsonify({"items": rows})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/search")
def search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"items": []})
    if not JACKETT_API_KEY:
        return jsonify({"error": "JACKETT_API_KEY is missing"}), 500

    params = {
        "apikey": JACKETT_API_KEY,
        "Query": query,
    }
    try:
        response = requests.get(
            f"{JACKETT_BASE_URL}/api/v2.0/indexers/all/results",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("Results", [])
        indexers = payload.get("Indexers", [])
        if not indexers:
            return (
                jsonify(
                    {
                        "error": "Jackett has no enabled indexers. Configure and test at least one indexer in Jackett UI.",
                    }
                ),
                500,
            )
        items = []
        for result in results:
            link = result.get("MagnetUri") or result.get("Link") or ""
            if not link:
                continue
            items.append(
                {
                    "title": result.get("Title", ""),
                    "size": result.get("Size", 0),
                    "seeders": result.get("Seeders", 0),
                    "source": result.get("Tracker", ""),
                    "link": link,
                }
            )
        return jsonify({"items": items})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/add")
def add_torrent():
    payload = request.get_json(silent=True) or {}
    torrent_url = (payload.get("url") or "").strip()
    sequential = bool(payload.get("sequential", False))
    if not torrent_url:
        return jsonify({"error": "Missing url"}), 400
    try:
        _qbit_login()
        _qbit_post(
            "/api/v2/torrents/add",
            {
                "urls": torrent_url,
                "savepath": str(DOWNLOADS_DIR),
                "sequentialDownload": "true" if sequential else "false",
                "firstLastPiecePrio": "true" if sequential else "false",
            },
        )
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@app.post("/api/torrents/pause")
def pause_torrent():
    payload = request.get_json(silent=True) or {}
    torrent_hash = (payload.get("hash") or "").strip()
    if not torrent_hash:
        return jsonify({"error": "Missing hash"}), 400
    try:
        _qbit_post("/api/v2/torrents/pause", {"hashes": torrent_hash})
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/torrents/resume")
def resume_torrent():
    payload = request.get_json(silent=True) or {}
    torrent_hash = (payload.get("hash") or "").strip()
    if not torrent_hash:
        return jsonify({"error": "Missing hash"}), 400
    try:
        _qbit_post("/api/v2/torrents/resume", {"hashes": torrent_hash})
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.post("/api/torrents/delete")
def delete_torrent():
    payload = request.get_json(silent=True) or {}
    torrent_hash = (payload.get("hash") or "").strip()
    delete_files = bool(payload.get("delete_files", True))
    if not torrent_hash:
        return jsonify({"error": "Missing hash"}), 400
    try:
        _qbit_post(
            "/api/v2/torrents/delete",
            {
                "hashes": torrent_hash,
                "deleteFiles": "true" if delete_files else "false",
            },
        )
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/limits")
def get_limits():
    try:
        _qbit_login()
        response = qbit_session.get(f"{QBIT_BASE_URL}/api/v2/transfer/info", timeout=30)
        response.raise_for_status()
        data = response.json()
        return jsonify(
            {
                "download_limit_bps": data.get("dl_rate_limit", 0),
                "upload_limit_bps": data.get("up_rate_limit", 0),
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/status")
def service_status():
    jackett_ok = False
    jackett_error = ""
    qbit_ok = False
    qbit_error = ""

    try:
        if not JACKETT_API_KEY:
            raise RuntimeError("JACKETT_API_KEY missing")
        response = requests.get(
            f"{JACKETT_BASE_URL}/api/v2.0/indexers/all/results",
            params={"apikey": JACKETT_API_KEY, "Query": "healthcheck"},
            timeout=10,
        )
        response.raise_for_status()
        jackett_ok = True
    except Exception as exc:
        jackett_error = str(exc)

    try:
        _qbit_login()
        response = qbit_session.get(f"{QBIT_BASE_URL}/api/v2/app/version", timeout=10)
        response.raise_for_status()
        qbit_ok = True
    except Exception as exc:
        qbit_error = str(exc)

    disk_payload: dict
    try:
        dl_path = DOWNLOADS_DIR.resolve()
        if not dl_path.exists():
            raise FileNotFoundError(f"Downloads directory does not exist: {dl_path}")
        usage = shutil.disk_usage(dl_path)
        disk_payload = {
            "ok": True,
            "path": str(dl_path),
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
        }
    except Exception as exc:
        disk_payload = {"ok": False, "error": str(exc)}

    return jsonify(
        {
            "jackett": {"ok": jackett_ok, "error": jackett_error},
            "qbittorrent": {"ok": qbit_ok, "error": qbit_error},
            "disk": disk_payload,
        }
    )


@app.post("/api/limits")
def set_limits():
    payload = request.get_json(silent=True) or {}
    try:
        dl_limit_bps = int(payload.get("download_limit_bps", 0))
        ul_limit_bps = int(payload.get("upload_limit_bps", 0))
    except Exception:
        return jsonify({"error": "Invalid limit values"}), 400

    if dl_limit_bps < 0 or ul_limit_bps < 0:
        return jsonify({"error": "Limits must be non-negative"}), 400

    try:
        _qbit_post("/api/v2/transfer/setDownloadLimit", {"limit": dl_limit_bps})
        _qbit_post("/api/v2/transfer/setUploadLimit", {"limit": ul_limit_bps})
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/files/<path:rel_path>")
def files(rel_path: str):
    try:
        target = _safe_download_path(rel_path)
    except ValueError:
        return jsonify({"error": "Invalid path"}), 400

    if target.is_dir():
        entries = []
        for child in sorted(target.iterdir(), key=lambda x: x.name.lower()):
            child_rel = child.relative_to(DOWNLOADS_DIR).as_posix()
            entries.append(
                {
                    "name": child.name + ("/" if child.is_dir() else ""),
                    "href": f"/files/{child_rel}",
                }
            )
        links = "".join(
            f'<li><a href="{entry["href"]}">{entry["name"]}</a></li>' for entry in entries
        )
        parent_rel = target.parent.relative_to(DOWNLOADS_DIR).as_posix() if target != DOWNLOADS_DIR else ""
        parent_link = f'/files/{parent_rel}' if parent_rel else "/"
        html = (
            f"<h3>{target.relative_to(DOWNLOADS_DIR)}</h3>"
            f'<p><a href="{parent_link}">Back</a></p>'
            f"<ul>{links}</ul>"
        )
        return html

    if target.exists():
        return send_file(target, as_attachment=True)
    return jsonify({"error": "Not found"}), 404


if __name__ == "__main__":
    app.run(host=APP_HOST, port=APP_PORT, debug=False)
