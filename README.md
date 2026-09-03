# RsyncWebUI

A web interface for rsync, built for Unraid. Pick your sources and destination from a file
browser, tick the rsync options you want, save it as a task, and let it repeat on a cron
schedule. Every run streams a live log with a progress bar and is kept in a history.

No external CDN, no web fonts, no JavaScript framework — the container serves everything it
needs.

![Screenshot 2](https://raw.githubusercontent.com/maschhoff/RsyncWebUi/refs/heads/main/Bildschirmfoto%20vom%202026-09-01%2009-46-24.png)
 
![Screenshot 3](https://raw.githubusercontent.com/maschhoff/RsyncWebUi/refs/heads/main/Bildschirmfoto%20vom%202026-09-01%2009-46-50.png)


---

## Features

- **Tasks** with a name, note, one or more sources, a destination and the full option set
- **Multiple sources** per task, transferred in a single rsync invocation
- **File browser** for sources and destination, restricted to configured roots
- **Cron schedule** per task, with presets and plain-language validation
- **Live log** over Server-Sent Events, with a progress bar
- **Dry run** on demand, without touching the saved task
- **Cancel** a running transfer; per-task run history
- **Command preview**: the editor always shows the exact rsync command being built
- **Archive overrides** (`--no-owner`, `--no-group`, `--no-perms`, …) for destinations that
  cannot store ownership or permissions
- **SSH destinations** (`user@host:/path`) with a custom port and key
- **Light and dark mode**, following the system setting on first visit
- Optional username and password protection

---

## Quick start

### Docker Compose

```bash
git clone https://github.com/maschhoff/RsyncWebUi
cd RsyncWebUI
docker compose up -d --build
```

Open `http://localhost:8080`.

### Unraid

**1. Community Apps
RsyncWebUi can be installed with Community Apps


**2. Install the template:**

```bash
cp unraid/rsyncwebui.xml /boot/config/plugins/dockerMan/templates-user/my-rsyncwebui.xml
```

**3.** In the Unraid web interface go to **Docker → Add Container**, pick `RsyncWebUI` from the
*Template* dropdown at the top, check the paths, and hit **Apply**.

The interface is then available at `http://<unraid-ip>:8080`.

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8080` | Port inside the container |
| `TZ` | `Europe/Berlin` | Timezone used for schedules |
| `DATA_DIR` | `/config` | Where the database lives |
| `BROWSE_ROOTS` | `/mnt,/data,/config` | Comma-separated paths the file browser may show |
| `AUTH_USER` | empty | Username for access protection; empty means no login |
| `AUTH_PASS` | empty | Matching password |
| `THREADS` | `16` | Worker threads of the web server |

### Volumes

| Container path | Purpose | Mode |
|---|---|---|
| `/config` | SQLite database (`rsyncwebui.db`), optionally `/config/ssh` for keys | `rw` |
| `/mnt/user` | Unraid shares | `rw` |
| `/mnt/disks` | Unassigned Devices, for external backup drives | `rw,slave` |
| `/mnt/remotes` | Mounted SMB/NFS shares | `rw,slave` |

`rw,slave` matters: without it a drive plugged in later stays invisible inside the container
even though Unraid has already mounted it.

---

## Using it

**Creating a task.** Hit *New task*, give it a name, then pick sources and a destination with
*Browse*.

**Multiple sources.** *Add source* puts as many directories as you like into one task. They are
all transferred in a single rsync invocation, which is faster than several tasks and lets
`--delete` reason about the combined file list.

**Directory or contents.** The *Transfer the contents only* switch is rsync's trailing slash.
Enabled, the *contents* of `/mnt/user/photos` land directly in the destination; disabled, a
`photos` subdirectory is created there. With several sources this is the switch that matters
most: with the slash all sources are merged into one directory, without it each gets its own
subdirectory. The interface warns you as soon as that combination appears.

**Before the first real run.** Especially with `--delete`, use the *Dry run* button. It runs the
same task with `--dry-run`, so nothing is written, but the log shows exactly what would happen.

**Schedules.** Enable *Repeat automatically* in the editor and pick a preset or write your own
cron expression. It is validated immediately and the next occurrence appears on the task card.

**Theme.** The sun/moon button in the top right switches between light and dark. Your choice is
remembered in the browser; without one, the system setting applies.

---

## Archive overrides

`--archive` is the right default for most backups, but it fails on filesystems that cannot store
POSIX metadata. Copying to an exFAT or NTFS drive, or to an SMB share, produces a stream of
`failed to set permissions` errors and a non-zero exit code.

The *Override archive mode* block in the **Transfer** tab cancels individual parts of `--archive`:

| Option | Effect |
|---|---|
| `--no-owner` | Everything is written as the user running the transfer |
| `--no-group` | Avoids "failed to set group" on foreign filesystems |
| `--no-perms` | The destination applies its own default permissions |
| `--no-times` | No timestamps — careful, subsequent runs then compare by size only |
| `--no-links` | Symlinks are skipped instead of recreated |
| `--no-devices`, `--no-specials` | Device files, sockets and FIFOs are left alone |

Order is not cosmetic here. rsync lets a later option override an earlier one, so `--no-perms`
only works *after* `--archive`. RsyncWebUI always emits the negations after the positive flags,
which you can verify in the command preview.

One caveat worth knowing about `--no-perms`: for *new* files rsync still applies the source
permissions masked by the umask, so a fresh copy may look unchanged. The flag shows its effect on
*existing* files, whose permissions are then left alone.

---

## SSH destinations

1. Create a key pair on the server and place it in `/mnt/user/appdata/rsyncwebui/ssh`:

   ```bash
   ssh-keygen -t ed25519 -f /mnt/user/appdata/rsyncwebui/ssh/id_ed25519 -N ""
   ssh-copy-id -i /mnt/user/appdata/rsyncwebui/ssh/id_ed25519.pub user@remotehost
   ```

2. Set the destination to `user@remotehost:/path/to/backup`.
3. In the **Network & limits** tab, enable *Transfer over SSH* and set the key to
   `/config/ssh/id_ed25519`.

The container fixes the permissions on the key files at startup. Password authentication is
deliberately unsupported: rsync runs without a terminal, so `BatchMode=yes` is set.

---

## Security notes

- The container runs as `root` so that ownership and permissions survive the copy. That is normal
  for a backup tool, but it makes access to the interface sensitive.
- `BROWSE_ROOTS` limits the file browser only. Any path the container can see may still be typed
  into the input fields. Mount fewer volumes if that bothers you.
- This does not belong on the open internet. If it must be, put it behind a reverse proxy with
  TLS and set `AUTH_USER` / `AUTH_PASS`.
- `/health` stays reachable without a login, otherwise the container health check fails. It
  returns only the version and whether rsync is present — no task data.
- `--delete` deletes at the destination. Task cards mark it in red.

---

## Project layout

```
RsyncWebUI/
├── app/
│   ├── main.py           Flask routes and REST API
│   ├── db.py             SQLite access for tasks and runs
│   ├── rsync_runner.py   Command building, process handling, live log
│   ├── scheduler.py      Cron schedules via APScheduler
│   ├── templates/        HTML
│   └── static/           CSS and JavaScript, no external dependencies
├── unraid/rsyncwebui.xml Unraid template
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
└── wsgi.py
```

The database lives at `/config/rsyncwebui.db`. The most recent 40 runs per task are kept; older
ones are pruned automatically.

---

## API

The interface uses nothing but these endpoints, so scripts can too.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/tasks` | all tasks with their last and next run |
| `POST` | `/api/tasks` | create a task |
| `PUT` | `/api/tasks/<id>` | update a task |
| `DELETE` | `/api/tasks/<id>` | delete a task |
| `POST` | `/api/tasks/<id>/run` | start a run, `?dry=1` for a dry run |
| `POST` | `/api/tasks/<id>/duplicate` | copy a task |
| `POST` | `/api/preview` | build the rsync command without saving |
| `POST` | `/api/cron/validate` | validate a cron expression |
| `GET` | `/api/runs` | run history |
| `GET` | `/api/runs/<id>` | one run including its log |
| `POST` | `/api/runs/<id>/cancel` | cancel a running transfer |
| `GET` | `/api/runs/<id>/stream` | live log as Server-Sent Events |
| `GET` | `/api/browse?path=` | directory listing |
| `GET` | `/health` | health check |

Example:

```bash
curl -X POST localhost:8080/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "Photos",
        "source": ["/mnt/user/photos", "/mnt/user/videos"],
        "destination": "/mnt/disks/usb/backup",
        "schedule": "0 3 * * *",
        "schedule_on": true,
        "options": {"archive": true, "verbose": true, "delete": true,
                    "no_owner": true, "no_group": true,
                    "source_contents": false, "excludes": "*.tmp"}
      }'
```

---

## Known limits

Large runs produce a lot of output. Three limits keep both the database and the browser out of
trouble: the stored log stops at 4 MB, the API serves at most the last 400 KB, and the view keeps
at most 3000 lines in memory. For a run across hundreds of thousands of files it is worth turning
off *Verbose output* — rsync then reports summaries instead of naming every file.

Progress redraws are never stored. rsync writes them with a carriage return so they overwrite
each other on a terminal; treating them as ordinary lines is what made earlier versions lock up
the browser.

---

## Upgrading from 1.0

The database file was renamed from `rsyncweb.db` to `rsyncwebui.db`. If the old file is present
in `/config`, the application renames it on first start and your tasks and history survive.

If you also change the config path in the Unraid template from `/mnt/user/appdata/rsync-web` to
`/mnt/user/appdata/rsyncwebui`, move the directory yourself first:

```bash
mv /mnt/user/appdata/rsync-web /mnt/user/appdata/rsyncwebui
```

Otherwise the container starts with an empty database.

---

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DATA_DIR=./config BROWSE_ROOTS=/tmp,/home python wsgi.py
```

The app is then on `http://localhost:8080`. rsync must be installed locally.

In production the container uses gunicorn with **one worker and several threads**. That is not
incidental: the scheduler and the registry of running transfers live in process memory, so a
second worker would schedule jobs twice and lose track of running processes.

---

## Contributing

Bug reports and pull requests are welcome. For anything larger, please open an issue first so we
can talk it through.

When reporting a problem, the run log helps a lot — the *Copy log* button in the log drawer puts
it on your clipboard.

---

## License

MIT, see [LICENSE](LICENSE).
