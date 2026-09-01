# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0]

### Added
- Archive overrides: `--no-owner`, `--no-group`, `--no-perms`, `--no-times`, `--no-links`,
  `--no-devices` and `--no-specials`, for destinations that cannot store POSIX metadata.
  They are always emitted after the positive flags, because rsync resolves conflicting
  options in favour of the later one.

### Changed
- The interface and all messages are now in English.
- Repository prepared for publication: GitHub README, changelog, contributing guide,
  issue templates and a build workflow.

## [1.1.0]

### Fixed
- **The browser froze as soon as a transfer started.** rsync writes its progress display with
  a carriage return so that the line overwrites itself. Every redraw was being treated as a new
  log line: 6000 files produced 12,016 lines, half of them progress redraws, each inserted as
  its own DOM node with a forced layout. Progress is now a separate event type that replaces
  itself and is never stored.
- Log rendering batches incoming lines into one insert per animation frame and measures layout
  once per batch rather than once per line. The view keeps at most 3000 lines.
- Stored logs are capped at 4 MB and the API serves at most the last 400 KB.

### Added
- Multiple sources per task, transferred in a single rsync invocation, with a warning when a
  trailing slash would merge them into one destination directory.
- Light and dark mode, following the system setting on first visit.

### Changed
- Renamed from "rsync web" to RsyncWebUI. The database file `rsyncweb.db` is renamed to
  `rsyncwebui.db` automatically on first start.

## [1.0.0]

### Added
- Initial release: tasks with sources, destination and rsync options; file browser; cron
  schedules; live log; dry runs; run history; Dockerfile and Unraid template.
