/* RsyncWebUI — frontend */

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/* Maximum number of lines the log view keeps in the DOM. Beyond that the oldest
   are dropped: a run across 200,000 files must not bring the browser down. */
const MAX_LOG_NODES = 3000;

const state = {
  tasks: [],
  editing: null,
  sources: [],
  browseTarget: null,
  browsePath: "",
  stream: null,
  currentRun: null,
};

/* ------------------------------------------------------------- option model */

const OPTION_GROUPS = [
  {
    id: "transfer",
    label: "Transfer",
    options: [
      ["archive", "--archive", "Archive mode", "Recursive, preserves permissions, times, symlinks and ownership. The usual choice for backups."],
      ["recursive", "--recursive", "Recursive", "Include subdirectories. Already implied by archive mode."],
      ["times", "--times", "Preserve timestamps", "Carry over each file's modification time."],
      ["perms", "--perms", "Preserve permissions", "Copy file permissions verbatim."],
      ["owner", "--owner", "Preserve owner", "Requires root inside the container."],
      ["group", "--group", "Preserve group", "Carry over group ownership."],
      ["links", "--links", "Copy symlinks", "Transfer links as links rather than as copies."],
      ["hard_links", "--hard-links", "Preserve hard links", "Noticeably slows down large runs."],
      ["acls", "--acls", "Transfer ACLs", "Include extended access control lists."],
      ["xattrs", "--xattrs", "Extended attributes", "macOS metadata, for example."],
      ["devices", "--devices", "Device files", "Only meaningful as root."],
      ["specials", "--specials", "Special files", "Include sockets and FIFOs."],
      ["numeric_ids", "--numeric-ids", "Numeric IDs", "Do not map UID/GID through names. Recommended between different systems."],
      ["sparse", "--sparse", "Sparse files", "Handle holes in files efficiently."],
    ],
    /* Overrides get their own block: they only make sense as corrections to
       --archive, and rsync applies them because they come later on the line. */
    extra: {
      title: "Override archive mode",
      note: "These cancel individual parts of --archive and are placed after it on the command line. "
          + "Useful when the destination cannot store ownership or permissions at all — "
          + "an exFAT or NTFS drive, or an SMB share.",
      options: [
        ["no_owner", "--no-owner", "Do not preserve owner", "Everything is written as the user running the transfer."],
        ["no_group", "--no-group", "Do not preserve group", "Avoids \"failed to set group\" errors on foreign filesystems."],
        ["no_perms", "--no-perms", "Do not preserve permissions", "The destination applies its own default permissions."],
        ["no_times", "--no-times", "Do not preserve timestamps", "Careful: without times every run compares by size only."],
        ["no_links", "--no-links", "Skip symlinks", "Links are ignored instead of being recreated."],
        ["no_devices", "--no-devices", "Skip device files", "Leaves block and character devices alone."],
        ["no_specials", "--no-specials", "Skip special files", "Leaves sockets and FIFOs alone."],
      ],
    },
  },
  {
    id: "behaviour",
    label: "Behaviour",
    options: [
      ["delete", "--delete", "Delete at destination", "Removes anything at the destination that no longer exists in the source. Try a dry run first.", "warn"],
      ["delete_excluded", "--delete-excluded", "Delete excluded files", "Also removes files that your filters exclude.", "warn"],
      ["update", "--update", "Keep newer files", "Never overwrite a file that is newer at the destination."],
      ["existing", "--existing", "Update existing only", "Do not create any new files at the destination."],
      ["ignore_existing", "--ignore-existing", "Skip existing files", "Transfer new files only."],
      ["checksum", "--checksum", "Compare checksums", "The most reliable comparison, but reads both sides in full."],
      ["size_only", "--size-only", "Compare size only", "Ignores modification times."],
      ["one_file_system", "--one-file-system", "Stay on one filesystem", "Do not follow mounted volumes."],
      ["prune_empty_dirs", "--prune-empty-dirs", "Skip empty directories", "Do not transfer directories with no content."],
      ["partial", "--partial", "Keep partial transfers", "Resume interrupted files on the next run."],
      ["inplace", "--inplace", "Write in place", "Writes into the destination file instead of a temporary copy.", "warn"],
    ],
  },
  {
    id: "output",
    label: "Output",
    options: [
      ["verbose", "--verbose", "Verbose output", "Names every transferred file. With very many files the log grows accordingly."],
      ["progress", "--info=progress2", "Show progress", "Feeds the progress display in this interface."],
      ["itemize", "--itemize-changes", "Itemize changes", "Shows what changed for each file."],
      ["stats", "--stats", "Statistics at the end", "Summary of file count and data volume."],
      ["dry_run", "--dry-run", "Always a dry run", "Nothing is written. Useful while you get to know a new task.", "warn"],
    ],
  },
];

const DEFAULT_OPTIONS = {
  archive: true,
  verbose: true,
  progress: true,
  partial: true,
  source_contents: true,
};

const CRON_PRESETS = [
  ["Every 15 minutes", "*/15 * * * *"],
  ["Hourly", "0 * * * *"],
  ["Daily 03:00", "0 3 * * *"],
  ["Daily 22:00", "0 22 * * *"],
  ["Weekly (Sun 03:00)", "0 3 * * 0"],
  ["Monthly (1st, 03:00)", "0 3 1 * *"],
];

/* ---------------------------------------------------------------- utilities */

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) throw new Error(data.error || `Server error ${res.status}`);
  return data;
}

function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), 4200);
}

function esc(str) {
  return String(str ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function fmtBytes(bytes) {
  if (bytes === null || bytes === undefined) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, n = bytes;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}

const STATE_TEXT = {
  success: "succeeded",
  failed: "failed",
  warning: "finished with warnings",
  running: "running",
  cancelled: "cancelled",
  aborted: "interrupted",
};

function describeCron(expr) {
  const parts = (expr || "").trim().split(/\s+/);
  if (parts.length !== 5) return "";
  const [min, hour, dom, mon, dow] = parts;
  const days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const pad = v => String(v).padStart(2, "0");
  if (min.startsWith("*/") && hour === "*" && dom === "*" && mon === "*" && dow === "*")
    return `every ${min.slice(2)} minutes`;
  if (/^\d+$/.test(min) && hour === "*" && dom === "*" && mon === "*" && dow === "*")
    return `hourly at minute ${pad(min)}`;
  if (/^\d+$/.test(min) && /^\d+$/.test(hour) && dom === "*" && mon === "*" && dow === "*")
    return `daily at ${pad(hour)}:${pad(min)}`;
  if (/^\d+$/.test(min) && /^\d+$/.test(hour) && dom === "*" && mon === "*" && /^[0-6]$/.test(dow))
    return `every ${days[+dow]} at ${pad(hour)}:${pad(min)}`;
  if (/^\d+$/.test(min) && /^\d+$/.test(hour) && /^\d+$/.test(dom) && mon === "*" && dow === "*")
    return `monthly on day ${dom} at ${pad(hour)}:${pad(min)}`;
  return "";
}

function sourcesOf(task) {
  return String(task.source || "").split("\n").map(s => s.trim()).filter(Boolean);
}

/* ------------------------------------------------------------------- theme */

function currentTheme() {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("theme", theme); } catch (_) { /* private mode */ }
  $("#btnTheme").textContent = theme === "dark" ? "☀" : "☾";
  $("#btnTheme").title = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
}

/* --------------------------------------------------------------- task list */

function chainMarkup(task) {
  const list = sourcesOf(task);
  const contents = task.options?.source_contents;
  const shown = list.slice(0, 3).map(s =>
    `<div class="p src" title="${esc(s)}">${esc(contents ? s.replace(/\/?$/, "/") : s)}</div>`).join("");
  const more = list.length > 3
    ? `<div class="p src more">and ${list.length - 3} more</div>` : "";
  return `
    <div class="chain">
      <div class="sources">${shown}${more}</div>
      <div class="arrow">▸▸</div>
      <div class="p dst" title="${esc(task.destination)}">${esc(task.destination)}</div>
    </div>`;
}

function renderTasks() {
  const list = $("#taskList");
  $("#emptyState").hidden = state.tasks.length > 0;
  list.innerHTML = "";
  let anyRunning = false;

  for (const task of state.tasks) {
    const running = Boolean(task.running_run_id);
    if (running) anyRunning = true;
    const last = task.last_run;
    const stateKey = running ? "running" : (last?.status || "idle");

    const tags = [];
    if (task.schedule_on && task.schedule) {
      tags.push(`<span class="tag sched" title="${esc(describeCron(task.schedule) || task.schedule)}">${esc(task.schedule)}</span>`);
    }
    if (task.options?.dry_run) tags.push('<span class="tag dry">dry run</span>');
    if (task.options?.delete) tags.push('<span class="tag del">--delete</span>');
    const count = sourcesOf(task).length;
    if (count > 1) tags.push(`<span class="tag multi">${count} sources</span>`);

    const el = document.createElement("article");
    el.className = "task";
    el.dataset.state = stateKey;
    el.dataset.id = task.id;
    el.innerHTML = `
      <div class="task-main">
        <div class="task-title">
          <h3>${esc(task.name)}</h3>
          ${task.description ? `<span class="task-note">${esc(task.description)}</span>` : ""}
        </div>
        ${chainMarkup(task)}
        <div class="task-meta">
          <span>Last run: <b>${last ? `${fmtTime(last.started_at)} · ${STATE_TEXT[last.status] || last.status}` : "none yet"}</b></span>
          ${task.next_run ? `<span>Next run: <b>${fmtTime(task.next_run)}</b></span>` : ""}
          ${tags.join("")}
        </div>
      </div>
      <div class="task-actions">
        ${running
          ? `<button class="btn danger small" data-act="cancel">Cancel</button>
             <button class="btn small" data-act="log">Live log</button>`
          : `<button class="btn primary small" data-act="run">Run</button>
             <button class="btn ghost small" data-act="dry">Dry run</button>
             <button class="btn ghost small" data-act="log" ${last ? "" : "disabled"}>Log</button>
             <button class="btn ghost small" data-act="edit">Edit</button>
             <button class="btn ghost small" data-act="delete">Delete</button>`}
      </div>
      ${running ? `<div class="task-progress"><span style="width:${task.progress || 0}%"></span></div>` : ""}
    `;
    list.append(el);
  }

  $("#healthLamp").className = "lamp " + (anyRunning ? "busy" : "ok");
}

async function loadTasks() {
  try {
    const data = await api("/api/tasks");
    state.tasks = data.tasks;
    renderTasks();
  } catch (err) {
    $("#healthLamp").className = "lamp error";
    toast(err.message, "error");
  }
}

$("#taskList").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const id = Number(ev.target.closest(".task").dataset.id);
  const task = state.tasks.find(t => t.id === id);
  const act = btn.dataset.act;

  if (act === "run" || act === "dry") {
    btn.disabled = true;
    try {
      const res = await api(`/api/tasks/${id}/run${act === "dry" ? "?dry=1" : ""}`, { method: "POST" });
      await loadTasks();
      openLog(res.run_id, task.name);
    } catch (err) { toast(err.message, "error"); btn.disabled = false; }
  }

  if (act === "cancel") {
    try {
      await api(`/api/runs/${task.running_run_id}/cancel`, { method: "POST" });
      toast("Cancellation requested.");
    } catch (err) { toast(err.message, "error"); }
  }

  if (act === "log") {
    if (task.running_run_id) openLog(task.running_run_id, task.name);
    else if (task.last_run) openLog(task.last_run.id, task.name);
  }

  if (act === "edit") openEditor(task);

  if (act === "delete") {
    if (!confirm(`Delete task "${task.name}"? Transferred data is left untouched.`)) return;
    try {
      await api(`/api/tasks/${id}`, { method: "DELETE" });
      toast("Task deleted.", "ok");
      await loadTasks();
    } catch (err) { toast(err.message, "error"); }
  }
});

/* --------------------------------------------------------- multiple sources */

function renderSources() {
  const box = $("#sourceList");
  box.innerHTML = "";

  if (!state.sources.length) state.sources = [""];

  state.sources.forEach((value, index) => {
    const row = document.createElement("div");
    row.className = "source-row";
    row.innerHTML = `
      <input type="text" spellcheck="false" value="${esc(value)}"
             placeholder="/mnt/user/photos" data-index="${index}">
      <button class="btn tiny" data-pick="${index}">Browse</button>
      <button class="icon-btn small-x" data-remove="${index}" title="Remove source"
              aria-label="Remove source" ${state.sources.length === 1 ? "disabled" : ""}>&times;</button>`;
    box.append(row);
  });

  updateMultiWarning();
  updatePreview();
}

function updateMultiWarning() {
  const multi = state.sources.filter(s => s.trim()).length > 1;
  const warn = $("#multiWarn");
  if (multi && $("#fSourceContents").checked) {
    warn.hidden = false;
    warn.textContent = "With a trailing slash, several sources are merged into one destination "
      + "directory. Turn it off to give each source its own subdirectory.";
  } else {
    warn.hidden = true;
  }
}

function readSources() {
  state.sources = $$("#sourceList input").map(i => i.value);
  return state.sources.filter(s => s.trim());
}

$("#sourceList").addEventListener("input", () => {
  readSources();
  updateMultiWarning();
  updatePreview();
});

$("#sourceList").addEventListener("click", (ev) => {
  const remove = ev.target.closest("[data-remove]");
  if (remove) {
    readSources();
    state.sources.splice(Number(remove.dataset.remove), 1);
    renderSources();
    return;
  }
  const pick = ev.target.closest("[data-pick]");
  if (pick) {
    readSources();
    const index = Number(pick.dataset.pick);
    state.browseTarget = { kind: "source", index };
    openBrowser(state.sources[index] || "");
  }
});

$("#btnAddSource").addEventListener("click", () => {
  readSources();
  state.sources.push("");
  renderSources();
  const inputs = $$("#sourceList input");
  inputs[inputs.length - 1]?.focus();
});

/* ------------------------------------------------------------------ editor */

function optionMarkup([key, flag, name, desc, mod]) {
  return `
    <label class="opt ${mod || ""}">
      <input type="checkbox" data-opt="${key}">
      <span class="opt-text">
        <span class="opt-name">${name} <span class="opt-flag">${flag}</span></span>
        <span class="opt-desc">${desc}</span>
      </span>
    </label>`;
}

function buildOptionUI() {
  const tabs = $("#optTabs");
  const panels = $("#optPanels");
  tabs.innerHTML = "";
  panels.innerHTML = "";

  const groups = [...OPTION_GROUPS,
    { id: "filter", label: "Filters" },
    { id: "network", label: "Network & limits" },
    { id: "advanced", label: "Advanced" },
  ];

  groups.forEach((group, index) => {
    const tab = document.createElement("button");
    tab.className = "opt-tab" + (index === 0 ? " active" : "");
    tab.textContent = group.label;
    tab.dataset.tab = group.id;
    tabs.append(tab);

    const panel = document.createElement("div");
    panel.className = "opt-panel";
    panel.dataset.panel = group.id;
    panel.hidden = index !== 0;

    if (group.options) {
      let html = `<div class="opt-grid">${group.options.map(optionMarkup).join("")}</div>`;
      if (group.extra) {
        html += `
          <div class="opt-subgroup">
            <span class="lbl">${group.extra.title}</span>
            <p class="hint">${group.extra.note}</p>
            <div class="opt-grid">${group.extra.options.map(optionMarkup).join("")}</div>
          </div>`;
      }
      panel.innerHTML = html;
    } else if (group.id === "filter") {
      panel.innerHTML = `
        <div class="field">
          <span class="lbl">Exclude (one pattern per line)</span>
          <textarea data-opt="excludes" placeholder=".DS_Store&#10;@eaDir/&#10;*.tmp&#10;/cache/"></textarea>
          <p class="hint">A leading <code>/</code> anchors the pattern to the root of the source.</p>
        </div>
        <div class="field" style="margin-top:14px">
          <span class="lbl">Include (evaluated before the excludes)</span>
          <textarea data-opt="includes" placeholder="*.jpg&#10;*/"></textarea>
        </div>`;
    } else if (group.id === "network") {
      panel.innerHTML = `
        <div class="opt-grid">
          <label class="opt">
            <input type="checkbox" data-opt="compress">
            <span class="opt-text">
              <span class="opt-name">Compress <span class="opt-flag">--compress</span></span>
              <span class="opt-desc">Worth it over the network, pure CPU cost locally.</span>
            </span>
          </label>
        </div>
        <div class="opt-row">
          <label class="field">
            <span class="lbl">Bandwidth limit</span>
            <input type="text" data-opt="bwlimit" class="mono" placeholder="e.g. 10M">
          </label>
          <label class="field">
            <span class="lbl">Timeout (seconds)</span>
            <input type="number" data-opt="timeout" min="0" placeholder="0">
          </label>
        </div>
        <div class="opt-grid" style="margin-top:14px">
          <label class="opt">
            <input type="checkbox" data-opt="ssh_enabled">
            <span class="opt-text">
              <span class="opt-name">Transfer over SSH <span class="opt-flag">--rsh=ssh</span></span>
              <span class="opt-desc">For sources or destinations of the form user@host:/path.</span>
            </span>
          </label>
        </div>
        <div class="opt-row">
          <label class="field">
            <span class="lbl">SSH port</span>
            <input type="text" data-opt="ssh_port" class="mono" placeholder="22">
          </label>
          <label class="field">
            <span class="lbl">Path to private key</span>
            <input type="text" data-opt="ssh_key" class="mono" placeholder="/config/ssh/id_ed25519">
          </label>
        </div>
        <label class="check" style="margin-top:12px">
          <input type="checkbox" data-opt="ssh_no_hostkey_check">
          <span>Skip host key checking <em>(trusted networks only)</em></span>
        </label>`;
    } else {
      panel.innerHTML = `
        <div class="opt-grid">
          <label class="opt">
            <input type="checkbox" data-opt="backup">
            <span class="opt-text">
              <span class="opt-name">Back up replaced files <span class="opt-flag">--backup</span></span>
              <span class="opt-desc">Overwritten versions are kept.</span>
            </span>
          </label>
        </div>
        <div class="opt-row">
          <label class="field">
            <span class="lbl">Backup directory</span>
            <input type="text" data-opt="backup_dir" class="mono" placeholder="/mnt/user/backup/versions">
          </label>
          <label class="field">
            <span class="lbl">Set permissions (--chmod)</span>
            <input type="text" data-opt="chmod" class="mono" placeholder="D775,F664">
          </label>
          <label class="field">
            <span class="lbl">Set ownership (--chown)</span>
            <input type="text" data-opt="chown" class="mono" placeholder="nobody:users">
          </label>
        </div>
        <label class="field" style="margin-top:14px">
          <span class="lbl">Additional arguments</span>
          <input type="text" data-opt="extra_args" class="mono" placeholder="--max-size=4G --exclude-from=/config/filter.txt">
          <p class="hint">Passed to rsync unchanged.</p>
        </label>`;
    }
    panels.append(panel);
  });

  tabs.addEventListener("click", ev => {
    const tab = ev.target.closest(".opt-tab");
    if (!tab) return;
    $$(".opt-tab", tabs).forEach(t => t.classList.toggle("active", t === tab));
    $$(".opt-panel", panels).forEach(p => { p.hidden = p.dataset.panel !== tab.dataset.tab; });
  });

  panels.addEventListener("input", updatePreview);
  panels.addEventListener("change", updatePreview);
}

function collectOptions() {
  const opts = { source_contents: $("#fSourceContents").checked };
  $$("[data-opt]").forEach(el => {
    const key = el.dataset.opt;
    if (el.type === "checkbox") opts[key] = el.checked;
    else if (el.value.trim()) opts[key] = el.value.trim();
  });
  return opts;
}

function applyOptions(opts) {
  $("#fSourceContents").checked = opts.source_contents !== false;
  $$("[data-opt]").forEach(el => {
    const key = el.dataset.opt;
    if (el.type === "checkbox") el.checked = Boolean(opts[key]);
    else el.value = opts[key] ?? "";
  });
}

function currentPayload() {
  return {
    name: $("#fName").value.trim(),
    description: $("#fDescription").value.trim(),
    source: readSources(),
    destination: $("#fDestination").value.trim(),
    schedule: $("#fSchedule").value.trim(),
    schedule_on: $("#fScheduleOn").checked,
    options: collectOptions(),
  };
}

let previewTimer = null;
function updatePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    try {
      const data = await api("/api/preview", { method: "POST", body: JSON.stringify(currentPayload()) });
      $("#cmdPreview").innerHTML = highlightCommand(data.command);
    } catch (_) { /* the preview is not critical */ }
  }, 180);
}

function highlightCommand(cmd) {
  return esc(cmd)
    .split(" ")
    .map(part => {
      if (part.startsWith("--") || /^-[a-zA-Z]/.test(part)) return `<span class="flag">${part}</span>`;
      if (part.startsWith("&#39;/") || part.startsWith("/") || part.includes(":/")) return `<span class="path">${part}</span>`;
      return part;
    })
    .join(" ");
}

function openEditor(task = null) {
  state.editing = task;
  $("#editorTitle").textContent = task ? "Edit task" : "New task";
  $("#editorError").textContent = "";
  $("#fName").value = task?.name || "";
  $("#fDescription").value = task?.description || "";
  $("#fDestination").value = task?.destination || "";
  $("#fSchedule").value = task?.schedule || "";
  $("#fScheduleOn").checked = Boolean(task?.schedule_on);
  applyOptions(task?.options || DEFAULT_OPTIONS);
  state.sources = task ? sourcesOf(task) : [""];
  renderSources();
  syncScheduleUI();
  $("#editorModal").hidden = false;
  setTimeout(() => $("#fName").focus(), 60);
}

function syncScheduleUI() {
  const on = $("#fScheduleOn").checked;
  $("#scheduleBody").hidden = !on;
  const value = $("#fSchedule").value.trim();
  $$("#cronPresets .preset").forEach(p => p.classList.toggle("active", p.dataset.cron === value));
  if (!on) { $("#cronStatus").textContent = ""; return; }
  checkCron();
}

let cronTimer = null;
function checkCron() {
  clearTimeout(cronTimer);
  cronTimer = setTimeout(async () => {
    const expr = $("#fSchedule").value.trim();
    const el = $("#cronStatus");
    if (!expr) { el.className = "cron-status"; el.textContent = "Enter a schedule or pick a preset."; return; }
    try {
      const res = await api("/api/cron/validate", { method: "POST", body: JSON.stringify({ schedule: expr }) });
      if (res.valid) {
        const human = describeCron(expr);
        el.className = "cron-status good";
        el.textContent = human ? `Runs ${human}.` : "Valid schedule.";
      } else {
        el.className = "cron-status bad";
        el.textContent = res.message;
      }
    } catch (_) { /* stay quiet */ }
  }, 250);
}

$("#btnSave").addEventListener("click", async () => {
  const payload = currentPayload();
  const err = $("#editorError");
  err.textContent = "";
  if (!payload.name || !payload.source.length || !payload.destination) {
    err.textContent = "A name, at least one source and a destination are required.";
    return;
  }
  try {
    if (state.editing) {
      await api(`/api/tasks/${state.editing.id}`, { method: "PUT", body: JSON.stringify(payload) });
      toast("Task saved.", "ok");
    } else {
      await api("/api/tasks", { method: "POST", body: JSON.stringify(payload) });
      toast("Task created.", "ok");
    }
    $("#editorModal").hidden = true;
    await loadTasks();
  } catch (e) { err.textContent = e.message; }
});

/* ------------------------------------------------------------- file browser */

function openBrowser(startPath) {
  $("#browseTitle").textContent = state.browseTarget?.kind === "source"
    ? "Choose a source" : "Choose a destination";
  $("#browseModal").hidden = false;
  browse(String(startPath || "").startsWith("/") ? startPath : "");
}

async function browse(path) {
  const list = $("#browserList");
  list.innerHTML = '<p class="note">Loading …</p>';
  try {
    const data = await api(`/api/browse?path=${encodeURIComponent(path || "")}`);
    state.browsePath = data.path;
    $("#browseCurrent").textContent = data.path || "Allowed roots";
    $("#btnPick").disabled = !data.path;
    renderCrumbs(data);
    const frag = document.createDocumentFragment();
    if (data.parent) frag.append(entryButton({ name: "..", path: data.parent, type: "dir" }, true));
    data.entries.forEach(e => frag.append(entryButton(e)));
    list.innerHTML = "";
    if (!data.entries.length && !data.parent) list.innerHTML = '<p class="note">This directory is empty.</p>';
    list.append(frag);
  } catch (err) {
    list.innerHTML = `<p class="note">${esc(err.message)}</p>`;
  }
}

function entryButton(entry, isUp = false) {
  const btn = document.createElement("button");
  btn.className = "entry " + (entry.type === "file" ? "file" : "");
  btn.innerHTML = `<span class="ico">${isUp ? "↰" : entry.type === "dir" ? "▸" : "·"}</span>
                   <span class="nm">${esc(entry.name)}</span>
                   ${entry.size != null ? `<span class="size">${fmtBytes(entry.size)}</span>` : ""}`;
  if (entry.type === "dir") btn.addEventListener("click", () => browse(entry.path));
  return btn;
}

function renderCrumbs(data) {
  const box = $("#crumbs");
  box.innerHTML = "";
  const root = document.createElement("button");
  root.className = "crumb";
  root.textContent = "Roots";
  root.addEventListener("click", () => browse(""));
  box.append(root);
  if (!data.path) return;
  let acc = "";
  data.path.split("/").filter(Boolean).forEach(seg => {
    acc += "/" + seg;
    const target = acc;
    const sep = document.createElement("span");
    sep.className = "crumb-sep";
    sep.textContent = "/";
    const b = document.createElement("button");
    b.className = "crumb";
    b.textContent = seg;
    b.addEventListener("click", () => browse(target));
    box.append(sep, b);
  });
}

document.addEventListener("click", ev => {
  const btn = ev.target.closest("[data-browse]");
  if (!btn) return;
  state.browseTarget = { kind: "destination" };
  openBrowser($("#" + btn.dataset.browse).value.trim());
});

$("#btnPick").addEventListener("click", () => {
  const target = state.browseTarget;
  if (target && state.browsePath) {
    if (target.kind === "source") {
      readSources();
      state.sources[target.index] = state.browsePath;
      renderSources();
    } else {
      $("#fDestination").value = state.browsePath;
      updatePreview();
    }
  }
  $("#browseModal").hidden = true;
});

/* ---------------------------------------------------------------- log drawer

   The important part: incoming lines are collected and inserted as one block
   per animation frame. Without that, every single line forces its own layout,
   and tens of thousands of lines lock up the browser.                        */

const logBuffer = [];
let flushScheduled = false;

function queueLine(line) {
  logBuffer.push(line);
  if (!flushScheduled) {
    flushScheduled = true;
    requestAnimationFrame(flushLog);
  }
}

function flushLog() {
  flushScheduled = false;
  if (!logBuffer.length) return;

  const body = $("#logBody");
  // Measured once, not once per line.
  const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 80;

  const frag = document.createDocumentFragment();
  for (const line of logBuffer) {
    const span = document.createElement("span");
    const cls = classifyLine(line);
    if (cls) span.className = cls;
    span.textContent = line + "\n";
    frag.append(span);
  }
  logBuffer.length = 0;
  body.append(frag);

  const excess = body.childElementCount - MAX_LOG_NODES;
  if (excess > 0) {
    for (let i = 0; i < excess; i++) body.firstElementChild?.remove();
  }
  if (atBottom) body.scrollTop = body.scrollHeight;
}

function classifyLine(line) {
  if (line.startsWith("$ ")) return "l-cmd";
  if (/rsync error|ERROR|error:|failed|Permission denied|No such file/i.test(line)) return "l-err";
  if (/^— Completed successfully/.test(line)) return "l-ok";
  if (/^—|^…|rsync: |warning/i.test(line)) return "l-warn";
  return "";
}

function resetLog() {
  logBuffer.length = 0;
  $("#logBody").textContent = "";
}

function showProgress(percent, text) {
  const strip = $("#logProgress");
  strip.hidden = false;
  strip.firstElementChild.style.width = `${percent || 0}%`;
  const live = $("#liveLine");
  if (text) { live.hidden = false; live.textContent = text; }
}

async function openLog(runId, taskName) {
  closeStream();
  state.currentRun = runId;
  $("#logDrawer").hidden = false;
  $("#logTitle").textContent = taskName || "Run";
  $("#logMeta").textContent = "loading …";
  resetLog();
  $("#logProgress").hidden = true;
  $("#liveLine").hidden = true;

  let run;
  try {
    run = (await api(`/api/runs/${runId}`)).run;
  } catch (err) { toast(err.message, "error"); return; }

  $("#logMeta").textContent =
    `Run ${runId} · ${run.trigger === "schedule" ? "scheduled" : "manual"} · started ${fmtTime(run.started_at)}`;
  $("#btnCancelRun").hidden = !run.live;

  if (run.live) {
    // The stream replays its own buffer, so nothing is inserted from the
    // database here — otherwise everything would appear twice.
    showProgress(run.progress, run.progress_text);
    connectStream(runId);
  } else {
    renderStoredLog(run.log || "");
  }
}

function renderStoredLog(text) {
  const lines = text.split("\n");
  const shown = lines.length > MAX_LOG_NODES
    ? ["… Showing the last lines of this run only.", ""].concat(lines.slice(-MAX_LOG_NODES))
    : lines;
  shown.forEach(queueLine);
}

function connectStream(runId) {
  const es = new EventSource(`/api/runs/${runId}/stream`);
  state.stream = es;
  es.onmessage = ev => {
    const data = JSON.parse(ev.data);
    if (data.type === "line") {
      queueLine(data.line);
    } else if (data.type === "progress") {
      showProgress(data.progress, data.text);
      const bar = document.querySelector(`.task[data-id="${runTaskId(runId)}"] .task-progress span`);
      if (bar) bar.style.width = `${data.progress || 0}%`;
    } else if (data.type === "reset") {
      resetLog();
    } else if (data.type === "end") {
      closeStream();
      flushLog();
      $("#btnCancelRun").hidden = true;
      $("#liveLine").hidden = true;
      $("#logProgress").firstElementChild.style.width = "100%";
      toast(`Run finished: ${STATE_TEXT[data.status] || data.status}`,
            data.status === "success" ? "ok" : "error");
      loadTasks();
    }
  };
  es.onerror = () => { closeStream(); loadTasks(); };
}

function runTaskId(runId) {
  const task = state.tasks.find(t => t.running_run_id === runId);
  return task ? task.id : -1;
}

function closeStream() {
  if (state.stream) { state.stream.close(); state.stream = null; }
}

$("#btnCloseLog").addEventListener("click", () => {
  closeStream();
  $("#logDrawer").hidden = true;
});

$("#btnCancelRun").addEventListener("click", async () => {
  try {
    await api(`/api/runs/${state.currentRun}/cancel`, { method: "POST" });
    toast("Cancellation requested.");
  } catch (err) { toast(err.message, "error"); }
});

$("#btnCopyLog").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("#logBody").textContent);
    toast("Log copied to the clipboard.", "ok");
  } catch (_) { toast("The browser refused clipboard access.", "error"); }
});

/* ------------------------------------------------------------------ history */

$("#btnHistory").addEventListener("click", async () => {
  const box = $("#historyList");
  box.innerHTML = '<p class="note">Loading …</p>';
  $("#historyModal").hidden = false;
  try {
    const { runs } = await api("/api/runs?limit=60");
    if (!runs.length) { box.innerHTML = '<p class="note">No runs recorded yet.</p>'; return; }
    box.innerHTML = "";
    runs.forEach(run => {
      const row = document.createElement("div");
      row.className = "hrow";
      row.innerHTML = `
        <span class="hdot ${run.status}"></span>
        <span class="hname">${esc(run.task_name)}
          <span class="hmeta">· ${STATE_TEXT[run.status] || run.status}</span></span>
        <span class="hmeta">${fmtTime(run.started_at)} · ${run.trigger === "schedule" ? "scheduled" : "manual"}</span>`;
      row.addEventListener("click", () => {
        $("#historyModal").hidden = true;
        openLog(run.id, run.task_name);
      });
      box.append(row);
    });
  } catch (err) { box.innerHTML = `<p class="note">${esc(err.message)}</p>`; }
});

/* --------------------------------------------------------------------- boot */

function wireStaticHandlers() {
  $$("[data-close]").forEach(btn =>
    btn.addEventListener("click", () => { $("#" + btn.dataset.close).hidden = true; }));

  $$(".modal").forEach(modal =>
    modal.addEventListener("mousedown", ev => { if (ev.target === modal) modal.hidden = true; }));

  document.addEventListener("keydown", ev => {
    if (ev.key !== "Escape") return;
    const open = $$(".modal:not([hidden])").pop();
    if (open) { open.hidden = true; return; }
    if (!$("#logDrawer").hidden) { closeStream(); $("#logDrawer").hidden = true; }
  });

  $("#btnTheme").addEventListener("click", () =>
    setTheme(currentTheme() === "dark" ? "light" : "dark"));

  $("#btnNew").addEventListener("click", () => openEditor());
  $("#btnNewEmpty").addEventListener("click", () => openEditor());
  $("#fScheduleOn").addEventListener("change", syncScheduleUI);
  $("#fSchedule").addEventListener("input", syncScheduleUI);
  $("#fName").addEventListener("input", updatePreview);
  $("#fDestination").addEventListener("input", updatePreview);
  $("#fSourceContents").addEventListener("change", () => {
    updateMultiWarning();
    updatePreview();
  });

  const presets = $("#cronPresets");
  CRON_PRESETS.forEach(([label, expr]) => {
    const b = document.createElement("button");
    b.className = "preset";
    b.textContent = label;
    b.dataset.cron = expr;
    b.addEventListener("click", () => { $("#fSchedule").value = expr; syncScheduleUI(); });
    presets.append(b);
  });
}

async function loadStatus() {
  try {
    const s = await api("/api/status");
    $("#topbarMeta").innerHTML =
      `<span class="chip">${esc(s.timezone)}</span>
       <span class="chip">rsync ${s.rsync_available ? "ready" : "missing"}</span>`;
    $("#footRsync").textContent = s.rsync_available
      ? `Allowed roots: ${s.browse_roots.join(", ")}`
      : "rsync is not available inside this container.";
  } catch (_) { /* never mind */ }
}

setTheme(currentTheme());
buildOptionUI();
wireStaticHandlers();
loadStatus();
loadTasks();
setInterval(() => { if ($("#editorModal").hidden) loadTasks(); }, 8000);
