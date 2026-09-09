/* The work panels — one table engine, three surfaces.
 *
 * Running, Work and Projects were three pages on a second document at `/`,
 * each with its own nav, its own numbers and its own chat dock. They are
 * panels in the one surface now, and they share this engine: a second
 * component for the same object is the duplication this pass exists to remove.
 *
 * The board could report thirty agent threads in state "running", twenty
 * scheduled jobs with a next-run time, and eight of Brutus's own services,
 * and its only row action was "Ask". Watching work you cannot stop reads as
 * control while being none, so the control lives on the row that names the
 * problem rather than in a terminal somewhere else.
 *
 * Three sources, one table vocabulary. Each group is a real record table with
 * search, sort, row actions and the five states a grid owes the reader —
 * loading, loaded, empty, filtered-empty, error — because a source that
 * cannot be reached rendered as an empty list is indistinguishable from a
 * source with nothing in it.
 *
 * Everything is inside one IIFE. session.js and this file are both classic
 * scripts sharing a single global scope, and both wanted `const $` — which is
 * a SyntaxError at parse time, so the first version of this panel did not
 * execute a single line and the tray tab silently did nothing. Nothing in the
 * Python test suite could see that; the browser said it immediately.
 */

(function () {
"use strict";

const $ = (sel) => document.querySelector(sel);

// One state record per panel. It used to be flat, for one panel.
const ops = {
  panels: {},
  busy: new Set(),
};

function panelState(key) {
  if (!ops.panels[key]) {
    ops.panels[key] = { query: "", filter: "all", liveOnly: true, sort: {}, groups: {}, loaded: false };
  }
  return ops.panels[key];
}

/* --- what each group is, and what may be done to a row ------------------- */

const RUNNING_GROUPS = [
  {
    key: "threads",
    title: "Agent threads",
    subtitle: "Codex, Cursor and Claude sessions on this laptop",
    empty: "No agent threads are running.",
    columns: [
      { key: "title", label: "Thread", grow: true },
      { key: "surface", label: "Surface" },
      { key: "project", label: "Project" },
      { key: "state", label: "State", kind: "status" },
      { key: "age", label: "Age", sortOn: "mtime", numeric: true },
      { key: "pid", label: "pid", numeric: true, mono: true },
    ],
    load: async () => {
      const data = await getJSON("/api/agents?");
      return (data.agents || []).map((row) => ({
        id: row.id,
        key: `thread:${row.id}`,
        title: row.title || row.name || row.session_id || row.id,
        surface: row.surface || "",
        project: row.project || "",
        state: row.state || (row.live ? "running" : "idle"),
        live: Boolean(row.live),
        age: row.age || "",
        mtime: Number(row.mtime || 0),
        pid: row.pid || null,
      }));
    },
    actions: (row) =>
      row.live && row.pid
        ? [{
            label: "Cancel",
            danger: true,
            confirm: {
              title: "Cancel this thread?",
              detail: `${row.title} — ${row.surface || "agent"}, pid ${row.pid}`,
              consequence: "It gets SIGTERM, then SIGKILL after three seconds. Unsaved work in that session is lost.",
              go: "Cancel the thread",
            },
            run: () => post(`/api/agents/${encodeURIComponent(row.id)}/cancel`),
          }]
        : [],
  },
  {
    key: "jobs",
    title: "Scheduled jobs",
    subtitle: "launchd jobs on the Studio",
    empty: "No scheduled jobs reported.",
    columns: [
      { key: "name", label: "Job", grow: true },
      { key: "status", label: "Status", kind: "status" },
      { key: "last_run_at", label: "Last run", kind: "time" },
      { key: "next_run_at", label: "Next run", kind: "time" },
      { key: "loaded", label: "Loaded", kind: "bool" },
    ],
    load: async () => {
      const data = await getJSON("/api/studio-runs");
      if (data.connection === "stale" && data.error) throw new SourceStale(data.error);
      return (data.jobs || []).map((job) => ({
        id: job.id,
        key: `job:${job.id}`,
        name: job.name || job.id,
        status: job.status || job.health || "unknown",
        live: String(job.status || "").toLowerCase() === "running",
        loaded: Boolean(job.loaded),
        last_run_at: job.last_run_at,
        next_run_at: job.next_run_at,
      }));
    },
    actions: (row) => [
      row.loaded
        ? {
            label: "Turn off",
            danger: true,
            confirm: {
              title: "Turn this job off?",
              detail: row.name,
              consequence: "It stops running on its schedule until you turn it back on.",
              go: "Turn it off",
            },
            run: () => post(`/api/studio-runs/${encodeURIComponent(row.id)}/disable`),
          }
        : {
            label: "Turn on",
            run: () => post(`/api/studio-runs/${encodeURIComponent(row.id)}/enable`),
          },
      { label: "Run now", run: () => post(`/api/studio-runs/${encodeURIComponent(row.id)}/run`) },
    ],
  },
  {
    key: "services",
    title: "Brutus services",
    subtitle: "This laptop's launchd jobs, including the one serving this page",
    empty: "No Brutus services are installed.",
    columns: [
      { key: "name", label: "Service", grow: true },
      { key: "state", label: "State", kind: "status" },
      { key: "pid", label: "pid", numeric: true, mono: true },
      { key: "last_exit", label: "Last exit", numeric: true, mono: true },
    ],
    load: async () => {
      const data = await getJSON("/api/services");
      return (data.services || []).map((service) => ({
        id: service.label,
        key: `service:${service.label}`,
        name: service.name + (service.is_core ? " (serving this page)" : ""),
        isCore: Boolean(service.is_core),
        state: service.running ? "running" : service.loaded ? "loaded" : "off",
        live: Boolean(service.running),
        loaded: Boolean(service.loaded),
        installed: Boolean(service.plist_installed),
        pid: service.pid,
        last_exit: service.last_exit,
      }));
    },
    actions: (row) => {
      const list = [];
      if (row.loaded) {
        list.push({
          label: "Restart",
          confirm: row.isCore
            ? {
                title: "Restart Brutus itself?",
                detail: row.name,
                consequence: "This page loses its connection for a few seconds and reconnects on its own.",
                go: "Restart Brutus",
              }
            : null,
          run: () => post(`/api/services/${encodeURIComponent(row.id)}/restart`),
        });
        list.push({
          label: "Stop",
          danger: true,
          confirm: {
            title: "Stop this service?",
            detail: row.name,
            consequence: row.isCore
              ? "Brutus stops answering entirely. You will need a terminal to start it again."
              : "It stays stopped until you start it or the machine restarts.",
            go: "Stop it",
          },
          run: () => post(`/api/services/${encodeURIComponent(row.id)}/stop`),
        });
      } else if (row.installed) {
        list.push({ label: "Start", run: () => post(`/api/services/${encodeURIComponent(row.id)}/start`) });
      }
      return list;
    },
  },
];


/* Work — the canon ledger. The console called this "Inbox", which named the
 * smallest of its three lists; what Justin opens it for is what is in review
 * and what is moving today. */
const WORK_GROUPS = [
  {
    key: "review",
    title: "In review",
    subtitle: "Waiting on your decision",
    empty: "Nothing is waiting on you.",
    columns: [
      { key: "title", label: "Item", grow: true },
      { key: "type", label: "Kind" },
      { key: "state", label: "State", kind: "status" },
      { key: "priority", label: "Priority", numeric: true },
      { key: "state_entered_at", label: "Since", kind: "time" },
    ],
    load: () => canonGroup("review"),
    actions: (row) => [
      {
        label: "Mark reviewed",
        confirm: {
          title: "Mark this reviewed?",
          detail: row.title,
          consequence: "It leaves your review queue and the decision is recorded against it.",
          go: "Mark it reviewed",
        },
        run: () => post(`/api/canon/work/${encodeURIComponent(row.id)}/review`),
      },
    ],
  },
  {
    key: "today",
    title: "Moving today",
    subtitle: "Canon work items with activity",
    empty: "Nothing has moved today.",
    columns: [
      { key: "title", label: "Item", grow: true },
      { key: "type", label: "Kind" },
      { key: "state", label: "State", kind: "status" },
      { key: "assignee", label: "Owner" },
      { key: "state_entered_at", label: "Since", kind: "time" },
    ],
    load: () => canonGroup("today"),
    actions: () => [],
  },
  {
    key: "inbox",
    title: "Inbox",
    subtitle: "Captured, not yet in the ledger",
    empty: "The inbox is clear.",
    columns: [
      { key: "title", label: "Item", grow: true },
      { key: "origin", label: "From" },
      { key: "state", label: "State", kind: "status" },
      { key: "state_entered_at", label: "Captured", kind: "time" },
    ],
    load: () => canonGroup("inbox"),
    actions: (row) => [
      {
        label: "Promote",
        confirm: {
          title: "Promote this into the ledger?",
          detail: row.title,
          consequence: "It becomes a tracked work item.",
          go: "Promote it",
        },
        run: () => post(`/api/canon/inbox/${encodeURIComponent(row.id)}/promote`),
      },
    ],
  },
];

/* Projects — the operating graph. Served from the summary projection, so this
 * is the 87 KB table rather than the 1.04 MB one that never finished loading. */
const PROJECT_GROUPS = [
  {
    key: "projects",
    title: "Projects",
    subtitle: "Linear, git and agent activity per project",
    empty: "No projects are tracked.",
    columns: [
      { key: "name", label: "Project", grow: true },
      { key: "status", label: "Attention", kind: "status" },
      { key: "attention_score", label: "Score", numeric: true },
      // `active_ticket_count` and `live_thread_count` were the first choice and
      // are 0 on every row — they count a narrower thing than the labels
      // promised. A column that is structurally empty is worse than no column:
      // it reads as "no tickets" rather than "wrong field".
      { key: "ticket_count", label: "Tickets", numeric: true },
      { key: "recent_thread_count", label: "Threads 48h", numeric: true },
      { key: "thread_count", label: "Threads", numeric: true },
      { key: "last_activity_epoch", label: "Last activity", kind: "epoch", numeric: true },
    ],
    load: async () => {
      const data = await getJSON("/api/nucleus");
      if (data.building) throw new SourceStale("The project graph is still building.");
      return (data.projects || []).map((project) => ({
        id: project.id,
        key: `project:${project.id}`,
        name: project.name || project.id,
        status: project.status || "",
        // "Running" for these rows means it needs you — the same idea the
        // toolbar's filter carries everywhere else in the tray.
        live: project.status === "needs_you" || project.status === "at_risk",
        attention_score: project.attention_score,
        ticket_count: project.ticket_count,
        recent_thread_count: project.recent_thread_count,
        thread_count: project.thread_count,
        last_activity_epoch: project.last_activity_epoch,
        pinned: Boolean(project.pinned),
        archived: Boolean(project.archived),
      }));
    },
    actions: (row) => [
      {
        label: row.pinned ? "Unpin" : "Pin",
        run: () => patch(`/api/nucleus/projects/${encodeURIComponent(row.id)}`, { pinned: !row.pinned }),
      },
      {
        label: row.archived ? "Unarchive" : "Archive",
        danger: !row.archived,
        confirm: row.archived
          ? null
          : {
              title: "Archive this project?",
              detail: row.name,
              consequence: "It drops out of the attention list. Nothing in Linear or git changes.",
              go: "Archive it",
            },
        run: () => patch(`/api/nucleus/projects/${encodeURIComponent(row.id)}`, { archived: !row.archived }),
      },
    ],
  },
];

/* One canon fetch feeds three groups. */
let canonPromise = null;
let canonAt = 0;

async function canonSnapshot() {
  if (!canonPromise || Date.now() - canonAt > 15000) {
    canonAt = Date.now();
    canonPromise = getJSON("/api/canon").catch((error) => {
      canonPromise = null;
      throw error;
    });
  }
  return canonPromise;
}

async function canonGroup(name) {
  const data = await canonSnapshot();
  return (data[name] || []).map((item) => ({
    id: item.id,
    key: `${name}:${item.id}`,
    title: item.title || item.id,
    type: item.type || "",
    state: item.state || "",
    // Everything in the ledger is in motion; the filter still has to mean
    // something, so "running only" keeps what is not already settled.
    live: !["done", "closed", "completed", "cancelled", "canceled"].includes(
      String(item.state || "").toLowerCase(),
    ),
    priority: item.priority,
    assignee: item.assignee || "",
    origin: item.origin || "",
    state_entered_at: item.state_entered_at,
  }));
}

const PANELS = [
  { key: "running", title: "everything running", groups: RUNNING_GROUPS, liveLabel: "Running only" },
  { key: "work", title: "the canon ledger", groups: WORK_GROUPS, liveLabel: "Open only" },
  { key: "projects", title: "projects", groups: PROJECT_GROUPS, liveLabel: "Needs you only" },
];
const PANEL_BY_KEY = Object.fromEntries(PANELS.map((p) => [p.key, p]));



/* --- fetch helpers ------------------------------------------------------- */

class SourceStale extends Error {}

async function getJSON(url) {
  const response = await fetch(url, { headers: { accept: "application/json" } });
  if (!response.ok) throw new Error(await detail(response));
  return response.json();
}

async function post(url) {
  const response = await fetch(url, { method: "POST", headers: { accept: "application/json" } });
  if (!response.ok) throw new Error(await detail(response));
  return response.json();
}

async function patch(url, body) {
  const response = await fetch(url, {
    method: "PATCH",
    headers: { accept: "application/json", "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await detail(response));
  return response.json();
}

async function detail(response) {
  try {
    const body = await response.json();
    return body.detail || body.error || `${response.status} ${response.statusText}`;
  } catch {
    return `${response.status} ${response.statusText}`;
  }
}

/* --- rendering ----------------------------------------------------------- */

// The graph's status values are field names. On screen they should be English.
const STATUS_WORDS = {
  needs_you: "Needs you",
  at_risk: "At risk",
  in_review: "In review",
  never_ran: "Never ran",
  approval_needed: "Needs approval",
  not_loaded: "Not loaded",
};

function statusWords(value) {
  const key = String(value || "").toLowerCase();
  return STATUS_WORDS[key] || value;
}

function statusTone(value) {
  const text = String(value || "").toLowerCase();
  if (["running", "healthy", "success", "live", "loaded", "fresh", "active", "done"].includes(text)) return "ok";
  if (["failure", "failed", "error", "crit", "at_risk", "off", "blocked", "needs_you"].includes(text)) return "bad";
  if (["stale", "unknown", "never_ran", "idle", "waiting", "quiet", "in_review"].includes(text)) return "warn";
  return "";
}

function cellText(row, column) {
  const raw = row[column.key];
  if (column.kind === "bool") return raw ? "yes" : "no";
  if (column.kind === "time") return raw ? relative(raw) : "\u2014";
  if (column.kind === "epoch") return raw ? relative(new Date(Number(raw) * 1000).toISOString()) : "\u2014";
  return raw === null || raw === undefined || raw === "" ? "\u2014" : String(raw);
}

function relative(iso) {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return String(iso);
  const seconds = Math.round((Date.now() - then) / 1000);
  const future = seconds < 0;
  let value = Math.abs(seconds);
  let unit = "s";
  if (value >= 86400) { value = Math.round(value / 86400); unit = "d"; }
  else if (value >= 3600) { value = Math.round(value / 3600); unit = "h"; }
  else if (value >= 60) { value = Math.round(value / 60); unit = "m"; }
  return future ? `in ${value}${unit}` : `${value}${unit} ago`;
}

function sortKeyFor(row, column) {
  if (column.sortOn) return Number(row[column.sortOn] || 0);
  const raw = row[column.key];
  if (column.numeric || column.kind === "epoch") return Number(raw || 0);
  if (column.kind === "time") return Date.parse(raw || 0) || 0;
  if (column.kind === "bool") return raw ? 1 : 0;
  return String(raw ?? "").toLowerCase();
}

function visibleRows(panel, group) {
  const state = panelState(panel.key);
  const held = state.groups[group.key] || {};
  let rows = (held.rows || []).slice();
  if (state.liveOnly) rows = rows.filter((row) => row.live);
  const query = state.query.trim().toLowerCase();
  if (query) {
    // Every word, any order — the same rule the thread search had to learn.
    const words = query.split(/\s+/).filter(Boolean);
    rows = rows.filter((row) => {
      const blob = group.columns.map((column) => cellText(row, column)).join(" ").toLowerCase();
      return words.every((word) => blob.includes(word));
    });
  }
  const sort = state.sort[group.key];
  if (sort) {
    const column = group.columns.find((c) => c.key === sort.column);
    if (column) {
      rows.sort((a, b) => {
        const left = sortKeyFor(a, column);
        const right = sortKeyFor(b, column);
        const order = left < right ? -1 : left > right ? 1 : 0;
        return sort.direction === "desc" ? -order : order;
      });
    }
  }
  return rows;
}

function state(kind, message) {
  const box = document.createElement("p");
  box.className = "ops-state";
  box.dataset.state = kind;
  box.textContent = message;
  return box;
}

function chrome(panel) {
  const state = panelState(panel.key);
  const bar = document.createElement("div");
  bar.className = "ops-toolbar";

  const label = document.createElement("label");
  label.className = "sr-only";
  label.htmlFor = `${panel.key}-search`;
  label.textContent = `Search ${panel.title}`;
  const search = document.createElement("input");
  search.type = "search";
  search.id = `${panel.key}-search`;
  search.placeholder = `Search ${panel.title}\u2026`;
  search.autocomplete = "off";
  search.value = state.query;
  search.addEventListener("input", () => {
    state.query = search.value;
    paint(panel);
  });

  const filters = document.createElement("div");
  filters.className = "ops-filters";
  filters.role = "toolbar";
  filters.setAttribute("aria-label", "Filter by kind");
  for (const [key, text] of [["all", "All"], ...panel.groups.map((g) => [g.key, g.title])]) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text;
    button.dataset.opsFilter = key;
    const on = state.filter === key;
    button.classList.toggle("is-active", on);
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.addEventListener("click", () => {
      state.filter = key;
      paint(panel);
    });
    filters.append(button);
  }

  const liveWrap = document.createElement("label");
  liveWrap.className = "ops-live-only";
  const live = document.createElement("input");
  live.type = "checkbox";
  live.checked = state.liveOnly;
  live.addEventListener("change", () => {
    state.liveOnly = live.checked;
    paint(panel);
  });
  liveWrap.append(live, document.createTextNode(` ${panel.liveLabel}`));

  const refresh = document.createElement("button");
  refresh.type = "button";
  refresh.textContent = "Refresh";
  refresh.addEventListener("click", () => loadPanel(panel, { force: true }));

  bar.append(label, search, filters, liveWrap, refresh);

  const note = document.createElement("p");
  note.className = "ops-note";
  note.id = `${panel.key}-note`;
  note.setAttribute("role", "status");
  note.hidden = true;

  const groups = document.createElement("div");
  groups.className = "ops-groups";
  groups.id = `${panel.key}-groups`;
  groups.setAttribute("aria-live", "polite");

  return { bar, note, groups };
}

function paint(panel) {
  const host = document.querySelector(`[data-ops-panel="${panel.key}"]`);
  if (!host) return;
  const pState = panelState(panel.key);

  let groupHost = host.querySelector(".ops-groups");
  if (!groupHost) {
    const built = chrome(panel);
    host.replaceChildren(built.bar, built.note, built.groups);
    groupHost = built.groups;
  }
  groupHost.textContent = "";

  let liveTotal = 0;
  for (const group of panel.groups) {
    if (pState.filter !== "all" && pState.filter !== group.key) continue;
    const held = pState.groups[group.key] || { state: "loading" };
    const rows = visibleRows(panel, group);
    liveTotal += (held.rows || []).filter((row) => row.live).length;

    const section = document.createElement("section");
    section.className = "ops-group";
    section.dataset.group = group.key;

    const head = document.createElement("header");
    const heading = document.createElement("h3");
    heading.textContent = group.title;
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = held.state === "loaded" ? String(rows.length) : "";
    heading.append(count);
    const sub = document.createElement("p");
    sub.className = "ops-group-sub";
    sub.textContent = group.subtitle;
    head.append(heading, sub);
    section.append(head);

    if (held.state === "loading") {
      section.append(state("loading", "Reading\u2026"));
    } else if (held.state === "error") {
      const box = state("error", held.error || "This source could not be read.");
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "Try again";
      retry.addEventListener("click", () => loadGroup(panel, group));
      box.append(retry);
      section.append(box);
    } else if (!(held.rows || []).length) {
      section.append(state("empty", group.empty));
    } else if (!rows.length) {
      section.append(
        state(
          "filtered-empty",
          pState.query.trim()
            ? `Nothing here matches \u201c${pState.query.trim()}\u201d.`
            : `Nothing here is showing. Untick \u201c${panel.liveLabel}\u201d to see the rest.`,
        ),
      );
    } else {
      section.append(table(panel, group, rows));
    }
    groupHost.append(section);
  }

  // Every count on the surface resolves to the projection under it. Four
  // different answers to "how many projects" is how a surface gets bypassed.
  const badge = document.getElementById(`${panel.key}-count`);
  if (badge) badge.textContent = pState.loaded ? String(liveTotal) : "";
}

function table(panel, group, rows) {
  const pState = panelState(panel.key);
  const wrap = document.createElement("div");
  wrap.className = "ops-grid";
  const grid = document.createElement("table");
  grid.className = "ops-table";
  grid.setAttribute("data-shine-contract", "table");

  const caption = document.createElement("caption");
  caption.className = "sr-only";
  caption.textContent = `${group.title} \u2014 ${group.subtitle}`;
  grid.append(caption);

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const column of group.columns) {
    const th = document.createElement("th");
    th.scope = "col";
    if (column.numeric) th.className = "num";
    const sort = pState.sort[group.key];
    const active = sort && sort.column === column.key;
    th.setAttribute(
      "aria-sort",
      active ? (sort.direction === "desc" ? "descending" : "ascending") : "none",
    );
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = column.label;
    const arrow = document.createElement("span");
    arrow.setAttribute("aria-hidden", "true");
    arrow.className = "ops-arrow";
    arrow.textContent = active ? (sort.direction === "desc" ? "\u2193" : "\u2191") : "";
    button.append(arrow);
    button.addEventListener("click", () => {
      const current = pState.sort[group.key];
      const direction =
        current && current.column === column.key && current.direction === "asc" ? "desc" : "asc";
      pState.sort[group.key] = { column: column.key, direction };
      paint(panel);
    });
    th.append(button);
    headRow.append(th);
  }
  const actionsHead = document.createElement("th");
  actionsHead.scope = "col";
  actionsHead.className = "ops-actions-head";
  actionsHead.textContent = "Action";
  headRow.append(actionsHead);
  thead.append(headRow);
  grid.append(thead);

  const tbody = document.createElement("tbody");
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.dataset.rowKey = row.key;
    for (const column of group.columns) {
      const td = document.createElement("td");
      if (column.numeric) td.className = "num";
      if (column.mono) td.classList.add("mono");
      const text = cellText(row, column);
      if (column.kind === "status") {
        const chip = document.createElement("span");
        chip.className = "ops-status";
        chip.dataset.tone = statusTone(text);
        chip.textContent = statusWords(text);
        td.append(chip);
      } else {
        td.textContent = text;
      }
      tr.append(td);
    }
    const actionCell = document.createElement("td");
    actionCell.className = "ops-actions";
    const actions = (group.actions(row) || []).filter(Boolean);
    if (!actions.length) {
      // A row with no action says why rather than showing a control that lies.
      const why = document.createElement("span");
      why.className = "ops-no-action";
      why.textContent = row.pid === null && "pid" in row ? "no pid" : "nothing to do";
      actionCell.append(why);
    }
    for (const action of actions) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = action.label;
      if (action.danger) button.classList.add("danger");
      button.disabled = ops.busy.has(row.key);
      button.addEventListener("click", () => runAction(panel, group, row, action, button));
      actionCell.append(button);
    }
    tr.append(actionCell);
    tbody.append(tr);
  }
  grid.append(tbody);
  wrap.append(grid);
  return wrap;
}

/* --- doing it ------------------------------------------------------------ */

function note(panel, message, tone = "") {
  const el = document.getElementById(`${panel.key}-note`);
  if (!el) return;
  el.textContent = message || "";
  el.dataset.tone = tone;
  el.hidden = !message;
}

async function confirmAction(confirm) {
  const dialog = $("#ops-confirm");
  if (!dialog || !confirm) return true;
  $("#ops-confirm-title").textContent = confirm.title;
  $("#ops-confirm-detail").textContent = confirm.detail || "";
  $("#ops-confirm-consequence").textContent = confirm.consequence || "";
  $("#ops-confirm-go").textContent = confirm.go || "Stop it";
  dialog.showModal();
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
  });
}

async function runAction(panel, group, row, action, button) {
  if (ops.busy.has(row.key)) return;
  if (action.confirm && !(await confirmAction(action.confirm))) return;

  ops.busy.add(row.key);
  button.disabled = true;
  const was = button.textContent;
  button.textContent = "\u2026";
  note(panel, "");
  try {
    const result = await action.run();
    // A control with no observable effect is decoration, so the source is
    // re-read and the row re-rendered from what the machine now reports.
    note(panel, result.detail || `${action.label} \u2014 done.`, "ok");
    canonPromise = null;
    await loadGroup(panel, group);
  } catch (error) {
    note(panel, String(error.message || error), "bad");
    button.textContent = was;
  } finally {
    ops.busy.delete(row.key);
    paint(panel);
  }
}

async function loadGroup(panel, group) {
  const pState = panelState(panel.key);
  pState.groups[group.key] = { ...(pState.groups[group.key] || {}), state: "loading" };
  paint(panel);
  try {
    const rows = await group.load();
    pState.groups[group.key] = { state: "loaded", rows };
  } catch (error) {
    pState.groups[group.key] = {
      state: "error",
      rows: (pState.groups[group.key] || {}).rows || [],
      error:
        error instanceof SourceStale
          ? `${error.message} Showing nothing rather than guessing.`
          : String(error.message || error),
    };
  }
  pState.loaded = true;
  paint(panel);
}

async function loadPanel(panel, { force = false } = {}) {
  const pState = panelState(panel.key);
  if (pState.loaded && !force) return;
  if (force) canonPromise = null;
  await Promise.all(panel.groups.map((group) => loadGroup(panel, group)));
}

/* --- wiring -------------------------------------------------------------- */

function selectTab(id) {
  const tabs = Array.from(document.querySelectorAll('.tray-tabs [role="tab"]'));
  for (const tab of tabs) {
    const selected = tab.id === id;
    tab.setAttribute("aria-selected", selected ? "true" : "false");
    tab.tabIndex = selected ? 0 : -1;
    const target = document.getElementById(tab.getAttribute("aria-controls"));
    if (target) target.hidden = !selected;
  }
  try {
    localStorage.setItem("brutus.tray.tab", id);
  } catch {
    /* private mode */
  }
  const panel = PANEL_BY_KEY[id.replace(/^tab-/, "")];
  // Loaded on first open, not on page load: the console fetched twelve
  // endpoints for pages nobody had opened, two of which took 36 seconds.
  if (panel) loadPanel(panel);
}

function initTabs() {
  const tabs = Array.from(document.querySelectorAll('.tray-tabs [role="tab"]'));
  if (!tabs.length) return;
  for (const tab of tabs) {
    tab.addEventListener("click", () => selectTab(tab.id));
    tab.addEventListener("keydown", (event) => {
      const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (!step) return;
      event.preventDefault();
      const next = tabs[(tabs.indexOf(tab) + step + tabs.length) % tabs.length];
      next.focus();
      selectTab(next.id);
    });
  }
  let remembered = "tab-queue";
  try {
    remembered = localStorage.getItem("brutus.tray.tab") || remembered;
  } catch {
    /* private mode */
  }
  selectTab(tabs.some((t) => t.id === remembered) ? remembered : tabs[0].id);
}

function start() {
  if (!document.querySelector("[data-ops-panel]")) return;
  for (const panel of PANELS) paint(panel);
  initTabs();
  document.querySelector(".work-tray")?.addEventListener("toggle", (event) => {
    if (!event.target.open) return;
    const active = document.querySelector('.tray-tabs [role="tab"][aria-selected="true"]');
    const panel = active && PANEL_BY_KEY[active.id.replace(/^tab-/, "")];
    if (panel) loadPanel(panel);
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", start, { once: true });
} else {
  start();
}

// One deliberate global, for the browser checks and for the console.
window.brutusOps = { ops, PANELS, PANEL_BY_KEY, loadPanel, paint, selectTab, relative, statusTone, statusWords };
})();
