# Contributing

Thanks for taking an interest. Bug reports, ideas and pull requests are all welcome.

## Reporting a bug

Please include:

- what you did and what happened instead,
- the run log if a transfer is involved (the *Copy log* button in the log drawer),
- the command shown in the editor's preview strip,
- your platform (Unraid version, Docker, bare Python) and the RsyncWebUI version from the footer.

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DATA_DIR=./config BROWSE_ROOTS=/tmp,/home python wsgi.py
```

rsync has to be installed locally. The app then runs on `http://localhost:8080`.

## Things worth knowing before you change something

**One worker, many threads.** The scheduler and the registry of running transfers live in process
memory. A second gunicorn worker would schedule every job twice and lose track of running
processes. If you need more concurrency, add threads, not workers.

**Progress output is not log output.** rsync separates progress redraws with `\r` and real output
with `\n`. `rsync_runner.py` relies on that distinction: `\r` fragments update the progress state
and are never persisted. Blurring the two is what made version 1.0 lock up the browser.

**Option order is meaningful.** rsync resolves conflicting options in favour of the later one, so
the negation flags must be appended after the positive ones. `build_command()` does this in a
separate loop — please keep it that way.

**No frontend build step.** The interface is plain HTML, CSS and JavaScript, served directly by
Flask. Please do not introduce a bundler or a CDN dependency; being self-contained is a feature
for a tool that often runs on a LAN with no internet access.

## Style

- Python: standard library plus Flask and APScheduler. Keep functions small and readable.
- JavaScript: no framework, no build step. `const`/`let`, no globals beyond the `state` object.
- Comments explain *why*, not *what*.

## Pull requests

Small, focused changes are easiest to review. For anything larger, please open an issue first so
we can agree on the approach before you spend time on it.
