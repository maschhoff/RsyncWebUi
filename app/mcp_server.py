"""MCP server for RsyncWebUI.

Exposes every action of the web UI (create/edit/duplicate/delete tasks, run
or cancel transfers, browse the filesystem, inspect run history and logs,
pause/resume the scheduler, ...) as MCP tools, so an AI assistant can drive
the app the same way a human would through the browser.

This process does not touch the database or the scheduler directly. It is a
thin client for the existing REST API (see app/main.py) over HTTP, so it can
run either inside the same container as a second process or on a completely
different machine that can merely reach the RsyncWebUI URL.

Configuration (environment variables):
  RSYNCWEBUI_URL   Base URL of the running instance. Default: http://127.0.0.1:8080
  AUTH_USER        Basic-auth username, if the web UI has one configured.
  AUTH_PASS        Basic-auth password.
  MCP_TRANSPORT    "stdio" (default, for local AI clients such as Claude
                    Desktop or Claude Code), "sse" or "streamable-http" (to
                    expose the server over the network).
  MCP_HOST         Bind address for the sse/streamable-http transports. Default: 127.0.0.1
  MCP_PORT         Bind port for the sse/streamable-http transports. Default: 8090

Run it with:
  python -m app.mcp_server

The network transports have no authentication of their own beyond the
RsyncWebUI basic-auth credentials used for the upstream calls - put a
reverse proxy in front if exposing MCP_TRANSPORT=sse/streamable-http beyond
a trusted network.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("RSYNCWEBUI_URL", "http://127.0.0.1:8080").rstrip("/")
AUTH_USER = os.environ.get("AUTH_USER", "").strip()
AUTH_PASS = os.environ.get("AUTH_PASS", "").strip()

_client = httpx.Client(
    base_url=BASE_URL,
    auth=(AUTH_USER, AUTH_PASS) if AUTH_USER else None,
    timeout=30.0,
)

mcp = FastMCP(
    "RsyncWebUI",
    instructions="Manage rsync backup/sync tasks: create and edit them, run or cancel "
    "transfers, inspect history and live logs, browse the server's filesystem, and "
    "pause or resume the cron scheduler.",
    host=os.environ.get("MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("MCP_PORT", "8090")),
)


def _call(method: str, path: str, **kwargs: Any) -> Any:
    try:
        resp = _client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach RsyncWebUI at {BASE_URL}: {exc}") from exc
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("error", detail)
        except ValueError:
            pass
        raise RuntimeError(f"RsyncWebUI API error ({resp.status_code}): {detail}")
    return resp.json() if resp.content else {}


def _get_task(task_id: int) -> dict:
    for task in _call("GET", "/api/tasks")["tasks"]:
        if task["id"] == task_id:
            return task
    raise RuntimeError(f"Task {task_id} not found.")


OPTIONS_HELP = """
Rsync options, all optional (unset booleans default to false unless noted):
  archive, recursive, times, perms, owner, group, links, hard_links, acls,
    xattrs, devices, specials, numeric_ids, sparse (bool)
  no_owner, no_group, no_perms, no_times, no_links, no_devices, no_specials
    (bool; override individual parts of "archive")
  delete, delete_excluded, update, existing, ignore_existing, checksum,
    size_only, one_file_system, prune_empty_dirs, partial, inplace (bool)
  verbose, itemize, stats, dry_run (bool)
  progress (bool, default true - feeds the live progress display)
  source_contents (bool, default true - trailing slash on sources: copy the
    *contents* of each source directory rather than the directory itself)
  compress (bool)
  bwlimit (str, e.g. "10M"), timeout (int, seconds)
  backup (bool), backup_dir (str path)
  chmod (str, e.g. "D775,F664"), chown (str, e.g. "nobody:users")
  excludes, includes (str with one pattern per line, or a list of patterns)
  ssh_enabled (bool), ssh_port (str), ssh_key (str path to a private key),
    ssh_no_hostkey_check (bool)
  extra_args (str, appended to the rsync command line verbatim)
"""


@mcp.tool()
def list_tasks() -> dict:
    """List every configured task, with its schedule, last-run outcome and
    live progress if one is currently running."""
    return _call("GET", "/api/tasks")


@mcp.tool()
def get_task(task_id: int) -> dict:
    """Fetch one task by id."""
    return _get_task(task_id)


def create_task(
    name: str,
    source: list[str] | str,
    destination: str,
    description: str = "",
    schedule: str = "",
    schedule_on: bool = False,
    options: dict[str, Any] | None = None,
) -> dict:
    """Create a new rsync task.

    source may be a single path, a remote "user@host:/path", or a list of
    several sources sharing one destination. schedule is a 5-field cron
    expression (minute hour day month weekday); schedule_on turns the cron
    job on or off. Returns the new task's id.
    """
    payload = {
        "name": name,
        "source": source,
        "destination": destination,
        "description": description,
        "schedule": schedule,
        "schedule_on": schedule_on,
        "options": options or {},
    }
    return _call("POST", "/api/tasks", json=payload)


create_task.__doc__ += OPTIONS_HELP
mcp.tool()(create_task)


def update_task(
    task_id: int,
    name: str | None = None,
    source: list[str] | str | None = None,
    destination: str | None = None,
    description: str | None = None,
    schedule: str | None = None,
    schedule_on: bool | None = None,
    options: dict[str, Any] | None = None,
) -> dict:
    """Update a task. Every field is optional and left unchanged when
    omitted; options are merged into the task's existing options rather than
    replacing them wholesale. Re-syncs the cron job if the schedule changed.
    """
    current = _get_task(task_id)
    payload = {
        "name": current["name"] if name is None else name,
        "source": current["source"] if source is None else source,
        "destination": current["destination"] if destination is None else destination,
        "description": current.get("description", "") if description is None else description,
        "schedule": current.get("schedule", "") if schedule is None else schedule,
        "schedule_on": current.get("schedule_on", False) if schedule_on is None else schedule_on,
        "options": {**current.get("options", {}), **(options or {})},
    }
    return _call("PUT", f"/api/tasks/{task_id}", json=payload)


update_task.__doc__ += OPTIONS_HELP
mcp.tool()(update_task)


@mcp.tool()
def delete_task(task_id: int) -> dict:
    """Delete a task permanently. Transferred data is left untouched. Fails
    while the task is currently running - cancel it first."""
    return _call("DELETE", f"/api/tasks/{task_id}")


@mcp.tool()
def duplicate_task(task_id: int) -> dict:
    """Duplicate a task as "<name> (copy)". The copy's schedule starts
    turned off so it doesn't silently double up runs. Returns the new id."""
    return _call("POST", f"/api/tasks/{task_id}/duplicate")


@mcp.tool()
def run_task(task_id: int, dry_run: bool = False) -> dict:
    """Start a task now, outside its schedule. Fails if the task is already
    running. Set dry_run=true to preview what would change without writing
    anything (adds --dry-run for this run only)."""
    params = {"dry": "1"} if dry_run else None
    return _call("POST", f"/api/tasks/{task_id}/run", params=params)


@mcp.tool()
def cancel_run(run_id: int) -> dict:
    """Request cancellation of an in-progress run."""
    return _call("POST", f"/api/runs/{run_id}/cancel")


@mcp.tool()
def get_run(run_id: int, tail_lines: int = 200) -> dict:
    """Fetch one run's status, exit summary and log. tail_lines caps how
    many of the last log lines are returned (0 omits the log body entirely)
    to keep large runs from overwhelming the response."""
    run = _call("GET", f"/api/runs/{run_id}")["run"]
    log = run.get("log", "")
    if tail_lines <= 0:
        run.pop("log", None)
    elif log:
        lines = log.splitlines()
        if len(lines) > tail_lines:
            run["log"] = "\n".join(lines[-tail_lines:])
            run["log_truncated_to_last_n_lines"] = tail_lines
    return run


@mcp.tool()
def list_runs(task_id: int | None = None, limit: int = 50) -> dict:
    """List recent runs, newest first, across all tasks or filtered to one."""
    params: dict[str, Any] = {"limit": limit}
    if task_id is not None:
        params["task_id"] = task_id
    return _call("GET", "/api/runs", params=params)


@mcp.tool()
def browse(path: str = "") -> dict:
    """List a directory on the server, for picking source/destination paths.
    Leave path empty to list the configured root directories (see
    get_status for the allowed roots); only paths under those roots are
    readable."""
    return _call("GET", "/api/browse", params={"path": path})


def preview_command(
    source: list[str] | str,
    destination: str,
    options: dict[str, Any] | None = None,
) -> dict:
    """Render the exact rsync command line these settings would produce,
    without creating or running a task. Useful to sanity-check options
    before calling create_task or update_task.
    """
    payload = {"source": source, "destination": destination, "options": options or {}}
    return _call("POST", "/api/preview", json=payload)


preview_command.__doc__ += OPTIONS_HELP
mcp.tool()(preview_command)


@mcp.tool()
def validate_cron(schedule: str) -> dict:
    """Validate a 5-field cron expression (minute hour day month weekday)
    against the server's configured timezone."""
    return _call("POST", "/api/cron/validate", json={"schedule": schedule})


@mcp.tool()
def pause_scheduler() -> dict:
    """Pause ALL scheduled (cron) task runs, server-wide, until resumed.
    Manual runs (run_task) still work while paused. Persists across
    restarts of RsyncWebUI."""
    return _call("POST", "/api/scheduler/pause")


@mcp.tool()
def resume_scheduler() -> dict:
    """Resume scheduled task runs after pause_scheduler."""
    return _call("POST", "/api/scheduler/resume")


@mcp.tool()
def get_status() -> dict:
    """Server info: version, rsync availability, timezone, allowed browse
    roots, ids of currently running tasks, and whether the scheduler is
    paused."""
    return _call("GET", "/api/status")


if __name__ == "__main__":
    mcp.run(transport=os.environ.get("MCP_TRANSPORT", "stdio"))
