"""Builds rsync commands and executes them with live log streaming."""

from __future__ import annotations

import os
import queue
import re
import shlex
import signal
import subprocess
import threading
import time

from . import db

RSYNC_BIN = os.environ.get("RSYNC_BIN", "rsync")

# Hard limits so that a run across hundreds of thousands of files overwhelms
# neither the database nor the browser.
MAX_LOG_BYTES = 4 * 1024 * 1024
MAX_MEMORY_LINES = 4000

# flag name -> rsync argument
FLAGS: dict[str, str] = {
    "archive": "--archive",
    "recursive": "--recursive",
    "verbose": "--verbose",
    "compress": "--compress",
    "times": "--times",
    "perms": "--perms",
    "owner": "--owner",
    "group": "--group",
    "links": "--links",
    "hard_links": "--hard-links",
    "acls": "--acls",
    "xattrs": "--xattrs",
    "devices": "--devices",
    "specials": "--specials",
    "delete": "--delete",
    "delete_excluded": "--delete-excluded",
    "dry_run": "--dry-run",
    "checksum": "--checksum",
    "update": "--update",
    "existing": "--existing",
    "ignore_existing": "--ignore-existing",
    "partial": "--partial",
    "inplace": "--inplace",
    "sparse": "--sparse",
    "numeric_ids": "--numeric-ids",
    "one_file_system": "--one-file-system",
    "prune_empty_dirs": "--prune-empty-dirs",
    "size_only": "--size-only",
    "itemize": "--itemize-changes",
    "stats": "--stats",
}

# Negations must appear AFTER the positive flags on the command line, because
# rsync lets a later option override an earlier one. "--archive --no-perms"
# works; the reverse order does not.
NEGATION_FLAGS: dict[str, str] = {
    "no_perms": "--no-perms",
    "no_owner": "--no-owner",
    "no_group": "--no-group",
    "no_times": "--no-times",
    "no_links": "--no-links",
    "no_devices": "--no-devices",
    "no_specials": "--no-specials",
}

PERCENT_RE = re.compile(r"(\d{1,3})%")

EXIT_HINTS = {
    0: "Completed successfully.",
    1: "Syntax error in the rsync options.",
    2: "Protocol incompatibility.",
    3: "Error selecting files or directories.",
    5: "Error negotiating the protocol with the remote side.",
    10: "Socket I/O error.",
    11: "File I/O error.",
    12: "Data stream error in the rsync protocol.",
    13: "Diagnostic error.",
    14: "IPC error.",
    20: "Terminated by a signal.",
    23: "Partial transfer: some files could not be transferred.",
    24: "Partial transfer: some files vanished while the run was in progress.",
    30: "Timed out while sending or receiving data.",
    35: "Timed out while opening the connection.",
}


def source_list(task: dict) -> list[str]:
    """A task may have several sources, stored one per line."""
    raw = task.get("source", "")
    items = raw if isinstance(raw, list) else str(raw).splitlines()
    return [i.strip() for i in items if i.strip()]


def normalise_path(path: str, trailing_slash: bool) -> str:
    path = (path or "").strip()
    if not path:
        return path
    if trailing_slash:
        if not path.endswith("/"):
            path += "/"
    elif path.endswith("/") and path != "/":
        path = path.rstrip("/") or "/"
    return path


def build_command(task: dict) -> list[str]:
    """Turn a stored task into an argv list for subprocess."""
    opts = task.get("options") or {}
    cmd: list[str] = [RSYNC_BIN]

    for key, arg in FLAGS.items():
        if opts.get(key):
            cmd.append(arg)

    # Order matters: these cancel parts of --archive and must come after it.
    for key, arg in NEGATION_FLAGS.items():
        if opts.get(key):
            cmd.append(arg)

    if opts.get("progress", True):
        cmd.append("--info=progress2")
    cmd.append("--human-readable")

    if opts.get("bwlimit"):
        cmd.append(f"--bwlimit={str(opts['bwlimit']).strip()}")
    if opts.get("timeout"):
        cmd.append(f"--timeout={int(opts['timeout'])}")
    if opts.get("backup"):
        cmd.append("--backup")
        if opts.get("backup_dir"):
            cmd.append(f"--backup-dir={opts['backup_dir'].strip()}")
    if opts.get("chmod"):
        cmd.append(f"--chmod={opts['chmod'].strip()}")
    if opts.get("chown"):
        cmd.append(f"--chown={opts['chown'].strip()}")

    for pattern in _lines(opts.get("excludes")):
        cmd.append(f"--exclude={pattern}")
    for pattern in _lines(opts.get("includes")):
        cmd.append(f"--include={pattern}")

    if opts.get("ssh_enabled"):
        ssh = ["ssh"]
        port = str(opts.get("ssh_port") or "").strip()
        if port:
            ssh += ["-p", port]
        key = (opts.get("ssh_key") or "").strip()
        if key:
            ssh += ["-i", key]
        if opts.get("ssh_no_hostkey_check"):
            ssh += ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
        ssh += ["-o", "BatchMode=yes"]
        cmd.append("--rsh=" + " ".join(shlex.quote(p) if " " in p else p for p in ssh))

    extra = (opts.get("extra_args") or "").strip()
    if extra:
        cmd += shlex.split(extra)

    contents = bool(opts.get("source_contents", True))
    for source in source_list(task):
        cmd.append(normalise_path(source, contents))
    cmd.append(normalise_path(task.get("destination", ""), False))
    return cmd


def _lines(value) -> list[str]:
    if not value:
        return []
    items = value if isinstance(value, list) else str(value).splitlines()
    return [i.strip() for i in items if i.strip()]


def command_string(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


# --------------------------------------------------------------------- run manager

class Run:
    def __init__(self, run_id: int, task_id: int | None, task_name: str, command: list[str]):
        self.run_id = run_id
        self.task_id = task_id
        self.task_name = task_name
        self.command = command
        self.process: subprocess.Popen | None = None
        self.lines: list[str] = []
        self.progress: int = 0
        self.progress_text: str = ""
        self.status: str = "running"
        self.cancelled = False
        self.subscribers: list[queue.Queue] = []
        self.lock = threading.Lock()

    def emit_line(self, line: str) -> None:
        """A real output line: stored and appended."""
        with self.lock:
            self.lines.append(line)
            if len(self.lines) > MAX_MEMORY_LINES:
                del self.lines[: len(self.lines) - MAX_MEMORY_LINES]
            self._push({"type": "line", "line": line})

    def emit_progress(self, text: str) -> None:
        """A progress redraw: replaces the previous one and is never stored."""
        percent = PERCENT_RE.search(text)
        with self.lock:
            self.progress_text = text
            if percent:
                self.progress = min(100, int(percent.group(1)))
            self._push({"type": "progress", "text": text, "progress": self.progress})

    def _push(self, event: dict) -> None:
        for sub in list(self.subscribers):
            try:
                sub.put_nowait(event)
            except queue.Full:
                pass

    def close(self, status: str) -> None:
        self.status = status
        with self.lock:
            self._push({"type": "end", "status": status})

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=4000)
        with self.lock:
            q.put_nowait({"type": "reset"})
            for line in self.lines[-800:]:
                q.put_nowait({"type": "line", "line": line})
            if self.progress_text:
                q.put_nowait({"type": "progress", "text": self.progress_text, "progress": self.progress})
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)


class RunManager:
    def __init__(self) -> None:
        self.active: dict[int, Run] = {}
        self.by_task: dict[int, int] = {}
        self.lock = threading.Lock()

    def is_running(self, task_id: int) -> bool:
        with self.lock:
            return task_id in self.by_task

    def running_ids(self) -> dict[int, int]:
        with self.lock:
            return dict(self.by_task)

    def get(self, run_id: int | None) -> Run | None:
        if run_id is None:
            return None
        with self.lock:
            return self.active.get(run_id)

    def start(self, task: dict, trigger: str = "manual") -> tuple[bool, str | int]:
        task_id = task.get("id")
        with self.lock:
            if task_id and task_id in self.by_task:
                return False, "This task is already running."

        cmd = build_command(task)
        cmd_str = command_string(cmd)
        run_id = db.create_run(task_id, task.get("name", "Ad-hoc"), cmd_str, trigger)
        run = Run(run_id, task_id, task.get("name", "Ad-hoc"), cmd)

        with self.lock:
            self.active[run_id] = run
            if task_id:
                self.by_task[task_id] = run_id

        threading.Thread(target=self._execute, args=(run,), daemon=True).start()
        return True, run_id

    def cancel(self, run_id: int) -> bool:
        run = self.get(run_id)
        if not run or not run.process:
            return False
        run.cancelled = True
        try:
            os.killpg(os.getpgid(run.process.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                run.process.terminate()
            except ProcessLookupError:
                return False
        return True

    def _execute(self, run: Run) -> None:
        started = time.time()
        writer = _LogWriter(run.run_id)
        header = f"$ {command_string(run.command)}"
        run.emit_line(header)
        run.emit_line("")
        writer.add(header)
        writer.add("")

        exit_code: int | None = None
        try:
            run.process = subprocess.Popen(
                run.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                bufsize=0,
            )
            assert run.process.stdout is not None
            buffer = b""
            while True:
                chunk = run.process.stdout.read(8192)
                if not chunk:
                    break
                buffer += chunk
                # \n ends a real line, \r only redraws the progress display.
                while True:
                    index = _first_break(buffer)
                    if index is None:
                        break
                    piece = buffer[:index]
                    terminator = buffer[index:index + 1]
                    buffer = buffer[index + 1:]
                    text = piece.decode("utf-8", "replace").strip()
                    if not text:
                        continue
                    if terminator == b"\r":
                        run.emit_progress(text)
                    else:
                        run.emit_line(text)
                        writer.add(text)
                writer.flush_if_due()
            if buffer.strip():
                text = buffer.decode("utf-8", "replace").strip()
                run.emit_line(text)
                writer.add(text)
            exit_code = run.process.wait()
        except FileNotFoundError:
            message = "ERROR: rsync was not found inside the container."
            run.emit_line(message)
            writer.add(message)
            exit_code = 127
        except Exception as exc:  # noqa: BLE001
            message = f"ERROR: {exc}"
            run.emit_line(message)
            writer.add(message)
            exit_code = 1

        duration = time.time() - started
        if run.cancelled:
            status, footer = "cancelled", "Run was cancelled."
        elif exit_code == 0:
            status, footer = "success", EXIT_HINTS[0]
        elif exit_code in (23, 24):
            status, footer = "warning", EXIT_HINTS[exit_code]
        else:
            status = "failed"
            footer = EXIT_HINTS.get(exit_code, f"rsync exited with code {exit_code}.")

        summary_line = f"— {footer} Duration: {_fmt_duration(duration)} (exit code {exit_code})"
        for line in ("", summary_line):
            run.emit_line(line)
            writer.add(line)
        writer.finish()

        db.finish_run(
            run.run_id,
            status,
            exit_code,
            {
                "duration": round(duration, 1),
                "message": footer,
                "truncated": writer.truncated,
                "log_lines": writer.count,
            },
        )
        run.close(status)

        with self.lock:
            self.active.pop(run.run_id, None)
            if run.task_id and self.by_task.get(run.task_id) == run.run_id:
                self.by_task.pop(run.task_id, None)

        try:
            db.prune_runs()
        except Exception:  # noqa: BLE001
            pass


def _first_break(data: bytes) -> int | None:
    n = data.find(b"\n")
    r = data.find(b"\r")
    if n == -1:
        return r if r != -1 else None
    if r == -1:
        return n
    return min(n, r)


class _LogWriter:
    """Collects lines and writes them in batches, with a hard size cap."""

    def __init__(self, run_id: int, interval: float = 2.0):
        self.run_id = run_id
        self.interval = interval
        self.pending: list[str] = []
        self.bytes = 0
        self.count = 0
        self.truncated = False
        self.last = time.time()

    def add(self, line: str) -> None:
        if self.truncated:
            return
        self.count += 1
        self.bytes += len(line) + 1
        if self.bytes > MAX_LOG_BYTES:
            self.truncated = True
            self.pending.append(
                "… Log truncated: this run produced a very large number of lines. "
                "Turn off the \"Verbose output\" option for shorter logs."
            )
            self.flush()
            return
        self.pending.append(line)

    def flush_if_due(self) -> None:
        if self.pending and time.time() - self.last > self.interval:
            self.flush()

    def flush(self) -> None:
        if not self.pending:
            return
        db.append_log(self.run_id, "\n".join(self.pending) + "\n")
        self.pending = []
        self.last = time.time()

    def finish(self) -> None:
        if self.pending:
            db.append_log(self.run_id, "\n".join(self.pending) + "\n")
            self.pending = []


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {sec} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


manager = RunManager()
