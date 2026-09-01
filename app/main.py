"""RsyncWebUI – Flask application."""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import time
from functools import wraps
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory, render_template

from . import db, scheduler
from .rsync_runner import build_command, command_string, manager

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
)
log = logging.getLogger("rsyncweb")

BROWSE_ROOTS = [
    p.strip() for p in os.environ.get("BROWSE_ROOTS", "/mnt,/data,/config").split(",") if p.strip()
]
AUTH_USER = os.environ.get("AUTH_USER", "").strip()
AUTH_PASS = os.environ.get("AUTH_PASS", "").strip()
APP_NAME = "RsyncWebUI"
APP_VERSION = "1.2.0"

# Very long logs are served tail-first; the browser should never have to
# build megabytes of text at once.
LOG_TAIL_BYTES = 400_000

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["JSON_SORT_KEYS"] = False


# ------------------------------------------------------------------ optional auth

def requires_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not AUTH_USER:
            return fn(*args, **kwargs)
        auth = request.authorization
        if auth and auth.username == AUTH_USER and auth.password == AUTH_PASS:
            return fn(*args, **kwargs)
        return Response(
            "Authentication required.",
            401,
            {"WWW-Authenticate": 'Basic realm="RsyncWebUI"'},
        )

    return wrapper


@app.after_request
def no_store(resp):
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ------------------------------------------------------------------------- pages

@app.get("/")
@requires_auth
def index():
    return render_template("index.html", version=APP_VERSION, app_name=APP_NAME)


@app.get("/health")
def health():
    return jsonify(status="ok", name=APP_NAME, version=APP_VERSION, rsync=bool(shutil.which("rsync")))


# ------------------------------------------------------------------------ browse

def _within_roots(path: Path) -> bool:
    for root in BROWSE_ROOTS:
        try:
            path.relative_to(Path(root).resolve())
            return True
        except (ValueError, OSError):
            continue
    return False


@app.get("/api/browse")
@requires_auth
def browse():
    raw = request.args.get("path", "").strip()
    if not raw:
        entries = []
        for root in BROWSE_ROOTS:
            p = Path(root)
            if p.is_dir():
                entries.append({"name": root, "path": str(p.resolve()), "type": "dir"})
        return jsonify(path="", parent=None, roots=BROWSE_ROOTS, entries=entries)

    try:
        target = Path(raw).resolve()
    except OSError:
        return jsonify(error="Path cannot be resolved."), 400

    if not _within_roots(target):
        return jsonify(
            error=f"Path is outside the allowed roots ({', '.join(BROWSE_ROOTS)})."
        ), 403
    if not target.is_dir():
        return jsonify(error="Directory does not exist."), 404

    entries = []
    try:
        for item in sorted(target.iterdir(), key=lambda i: i.name.lower()):
            try:
                is_dir = item.is_dir()
            except OSError:
                continue
            entries.append(
                {
                    "name": item.name,
                    "path": str(item),
                    "type": "dir" if is_dir else "file",
                    "size": item.stat().st_size if not is_dir else None,
                }
            )
    except PermissionError:
        return jsonify(error="No permission to read this directory."), 403

    parent = str(target.parent) if _within_roots(target.parent) and target.parent != target else None
    return jsonify(path=str(target), parent=parent, roots=BROWSE_ROOTS, entries=entries)


# ------------------------------------------------------------------------- tasks

REQUIRED = ("name", "source", "destination")
LABELS = {"name": "Name", "source": "Source", "destination": "Destination"}


def _normalise(payload: dict) -> dict:
    """The UI sends sources as a list; they are stored one per line."""
    source = payload.get("source", "")
    if isinstance(source, list):
        source = "\n".join(str(s).strip() for s in source if str(s).strip())
    payload["source"] = source.strip()
    payload["destination"] = str(payload.get("destination", "")).strip()
    payload["name"] = str(payload.get("name", "")).strip()
    return payload


def _validate(payload: dict) -> str | None:
    for field in REQUIRED:
        if not str(payload.get(field) or "").strip():
            return f"{LABELS[field]} must not be empty."
    if payload.get("schedule_on"):
        ok, message = scheduler.validate_cron(payload.get("schedule", ""))
        if not ok:
            return message
    return None


@app.get("/api/tasks")
@requires_auth
def api_tasks():
    tasks = db.list_tasks()
    running = manager.running_ids()
    upcoming = scheduler.next_run_times()
    for task in tasks:
        run_id = running.get(task["id"])
        task["running_run_id"] = run_id
        active = manager.get(run_id) if run_id else None
        task["progress"] = active.progress if active else 0
        task["next_run"] = upcoming.get(task["id"])
        try:
            task["command"] = command_string(build_command(task))
        except Exception:  # noqa: BLE001
            task["command"] = ""
    return jsonify(tasks=tasks)


@app.post("/api/tasks")
@requires_auth
def api_create_task():
    payload = _normalise(request.get_json(force=True, silent=True) or {})
    error = _validate(payload)
    if error:
        return jsonify(error=error), 400
    task_id = db.create_task(payload)
    scheduler.sync_task(db.get_task(task_id))
    return jsonify(id=task_id), 201


@app.put("/api/tasks/<int:task_id>")
@requires_auth
def api_update_task(task_id: int):
    if not db.get_task(task_id):
        return jsonify(error="Task not found."), 404
    payload = _normalise(request.get_json(force=True, silent=True) or {})
    error = _validate(payload)
    if error:
        return jsonify(error=error), 400
    db.update_task(task_id, payload)
    scheduler.sync_task(db.get_task(task_id))
    return jsonify(id=task_id)


@app.delete("/api/tasks/<int:task_id>")
@requires_auth
def api_delete_task(task_id: int):
    if manager.is_running(task_id):
        return jsonify(error="Task is currently running. Cancel it first."), 409
    scheduler.remove_task(task_id)
    db.delete_task(task_id)
    return jsonify(ok=True)


@app.post("/api/tasks/<int:task_id>/duplicate")
@requires_auth
def api_duplicate_task(task_id: int):
    task = db.get_task(task_id)
    if not task:
        return jsonify(error="Task not found."), 404
    task["name"] = f"{task['name']} (copy)"
    task["schedule_on"] = False
    new_id = db.create_task(task)
    return jsonify(id=new_id), 201


@app.post("/api/tasks/<int:task_id>/run")
@requires_auth
def api_run_task(task_id: int):
    task = db.get_task(task_id)
    if not task:
        return jsonify(error="Task not found."), 404
    if request.args.get("dry") == "1":
        task["options"] = {**task.get("options", {}), "dry_run": True}
    ok, result = manager.start(task, trigger="manual")
    if not ok:
        return jsonify(error=result), 409
    return jsonify(run_id=result), 202


@app.post("/api/preview")
@requires_auth
def api_preview():
    payload = _normalise(request.get_json(force=True, silent=True) or {})
    try:
        return jsonify(command=command_string(build_command(payload)))
    except Exception as exc:  # noqa: BLE001
        return jsonify(error=str(exc)), 400


@app.post("/api/cron/validate")
@requires_auth
def api_validate_cron():
    payload = request.get_json(force=True, silent=True) or {}
    ok, message = scheduler.validate_cron(payload.get("schedule", ""))
    return jsonify(valid=ok, message=message)


# -------------------------------------------------------------------------- runs

@app.get("/api/runs")
@requires_auth
def api_runs():
    task_id = request.args.get("task_id", type=int)
    return jsonify(runs=db.list_runs(task_id, limit=request.args.get("limit", 50, type=int)))


@app.get("/api/runs/<int:run_id>")
@requires_auth
def api_run(run_id: int):
    run = db.get_run(run_id)
    if not run:
        return jsonify(error="Run not found."), 404
    live = manager.get(run_id)
    log = run.get("log") or ""
    if len(log) > LOG_TAIL_BYTES:
        cut = log[-LOG_TAIL_BYTES:]
        log = ("… Showing the end of the log only; the beginning was truncated.\n\n"
               + cut[cut.find("\n") + 1:])
        run["log_trimmed"] = True
    run["log"] = log
    run["live"] = live is not None
    run["progress"] = live.progress if live else 100
    run["progress_text"] = live.progress_text if live else ""
    return jsonify(run=run)


@app.post("/api/runs/<int:run_id>/cancel")
@requires_auth
def api_cancel(run_id: int):
    if manager.cancel(run_id):
        return jsonify(ok=True)
    return jsonify(error="Run is no longer active."), 409


@app.get("/api/runs/<int:run_id>/stream")
@requires_auth
def api_stream(run_id: int):
    run = manager.get(run_id)

    def generate():
        if run is None:
            stored = db.get_run(run_id)
            payload = {"type": "end", "status": stored["status"] if stored else "unknown"}
            yield f"data: {json.dumps(payload)}\n\n"
            return
        q = run.subscribe()
        try:
            while True:
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "end":
                    return
        finally:
            run.unsubscribe(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.get("/api/status")
@requires_auth
def api_status():
    return jsonify(
        name=APP_NAME,
        version=APP_VERSION,
        rsync_available=bool(shutil.which("rsync")),
        timezone=os.environ.get("TZ", "Europe/Berlin"),
        browse_roots=BROWSE_ROOTS,
        running=manager.running_ids(),
        server_time=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )


@app.get("/favicon.svg")
def favicon():
    return send_from_directory(app.static_folder, "favicon.svg")


def bootstrap() -> None:
    db.init_db()
    db.mark_orphaned_runs()
    scheduler.start()
    if not shutil.which("rsync"):
        log.error("rsync is not installed in this container - runs will fail.")


bootstrap()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), threaded=True)
