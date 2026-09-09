/* Running now — the panel that can stop things.
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

const ops = {
  filter: "all",
  liveOnly: true,
  query: "",
  sort: {},              // group key -> { column, direction }
  groups: {},            // group key -> { state, rows, error }
  busy: new Set(),       // row keys with an action in flight
  loaded: false,
};

/* --- what each group is, and what may be done to a row ------------------- */

const GROUPS = [
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

const GROUP_BY_KEY = Object.fromEntries(GROUPS.map((g) => [g.key, g]));

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

async function detail(response) {
  try {
    const body = await response.json();
    return body.detail || body.error || `${response.status} ${response.statusText}`;
  } catch {
    return `${response.status} ${response.statusText}`;
  }
}

/* --- rendering ----------------------------------------------------------- */

function statusTone(value) {
  const text = String(value || "").toLowerCase();
  if (["running", "healthy", "success", "live", "loaded", "fresh"].includes(text)) return "ok";
  if (["failure", "failed", "error", "crit", "at_risk", "off"].includes(text)) return "bad";
  if (["stale", "unknown", "never_ran", "idle", "waiting"].includes(text)) return "warn";
  return "";
}

function cellText(row, column) {
  const raw = row[column.key];
  if (column.kind === "bool") return raw ? "yes" : "no";
  if (column.kind === "time") return raw ? relative(raw) : "—";
  return raw === null || raw === undefined || raw === "" ? "—" : String(raw);
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
  if (column.numeric) return Number(raw || 0);
  if (column.kind === "time") return Date.parse(raw || 0) || 0;
  if (column.kind === "bool") return raw ? 1 : 0;
  return String(raw ?? "").toLowerCase();
}

function visibleRows(group) {
  const held = ops.groups[group.key] || {};
  let rows = (held.rows || []).slice();
  if (ops.liveOnly) rows = rows.filter((row) => row.live);
  const query = ops.query.trim().toLowerCase();
  if (query) {
    rows = rows.filter((row) =>
      group.columns.some((column) => cellText(row, column).toLowerCase().includes(query)),
    );
  }
  const sort = ops.sort[group.key];
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

function render() {
  const host = $("#ops-groups");
  if (!host) return;
  host.textContent = "";
  let runningTotal = 0;

  for (const group of GROUPS) {
    if (ops.filter !== "all" && ops.filter !== group.key) continue;
    const held = ops.groups[group.key] || { state: "loading" };
    const rows = visibleRows(group);
    runningTotal += (held.rows || []).filter((row) => row.live).length;

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
      section.append(state("loading", "Reading…"));
    } else if (held.state === "error") {
      const box = state("error", held.error || "This source could not be read.");
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "Try again";
      retry.addEventListener("click", () => loadGroup(group));
      box.append(retry);
      section.append(box);
    } else if (!(held.rows || []).length) {
      section.append(state("empty", group.empty));
    } else if (!rows.length) {
      const why = ops.query.trim()
        ? `Nothing here matches “${ops.query.trim()}”.`
        : "Nothing here is running. Untick “Running only” to see the rest.";
      section.append(state("filtered-empty", why));
    } else {
      section.append(table(group, rows));
    }
    host.append(section);
  }

  const badge = $("#running-count");
  if (badge) badge.textContent = ops.loaded ? String(runningTotal) : "";
}

function state(kind, message) {
  const box = document.createElement("p");
  box.className = "ops-state";
  box.dataset.state = kind;
  box.textContent = message;
  return box;
}

function table(group, rows) {
  const wrap = document.createElement("div");
  wrap.className = "ops-grid";
  const table = document.createElement("table");
  table.className = "ops-table";
  table.setAttribute("data-shine-contract", "table");

  const caption = document.createElement("caption");
  caption.className = "sr-only";
  caption.textContent = `${group.title} — ${group.subtitle}`;
  table.append(caption);

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const column of group.columns) {
    const th = document.createElement("th");
    th.scope = "col";
    if (column.numeric) th.className = "num";
    const sort = ops.sort[group.key];
    const active = sort && sort.column === column.key;
    th.setAttribute("aria-sort", active ? (sort.direction === "desc" ? "descending" : "ascending") : "none");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = column.label;
    const arrow = document.createElement("span");
    arrow.setAttribute("aria-hidden", "true");
    arrow.className = "ops-arrow";
    arrow.textContent = active ? (sort.direction === "desc" ? "↓" : "↑") : "";
    button.append(arrow);
    button.addEventListener("click", () => {
      const current = ops.sort[group.key];
      const direction = current && current.column === column.key && current.direction === "asc" ? "desc" : "asc";
      ops.sort[group.key] = { column: column.key, direction };
      render();
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
  table.append(thead);

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
        chip.textContent = text;
        td.append(chip);
      } else {
        td.textContent = text;
      }
      tr.append(td);
    }
    const actionCell = document.createElement("td");
    actionCell.className = "ops-actions";
    const actions = group.actions(row) || [];
    if (!actions.length) {
      const why = document.createElement("span");
      why.className = "ops-no-action";
      why.textContent = row.live ? "no pid" : "not running";
      actionCell.append(why);
    }
    for (const action of actions) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = action.label;
      if (action.danger) button.classList.add("danger");
      button.disabled = ops.busy.has(row.key);
      button.addEventListener("click", () => runAction(group, row, action, button));
      actionCell.append(button);
    }
    tr.append(actionCell);
    tbody.append(tr);
  }
  table.append(tbody);
  wrap.append(table);
  return wrap;
}

/* --- doing it ------------------------------------------------------------ */

function note(message, tone = "") {
  const el = $("#ops-note");
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

async function runAction(group, row, action, button) {
  if (ops.busy.has(row.key)) return;
  if (action.confirm && !(await confirmAction(action.confirm))) return;

  ops.busy.add(row.key);
  button.disabled = true;
  const was = button.textContent;
  button.textContent = "…";
  note("");
  try {
    const result = await action.run();
    // A control with no observable effect is decoration, so the source is
    // re-read and the row re-rendered from what the machine now reports.
    note(result.detail || `${action.label} — done.`, "ok");
    await loadGroup(group);
  } catch (error) {
    note(String(error.message || error), "bad");
    button.textContent = was;
  } finally {
    ops.busy.delete(row.key);
    render();
  }
}

async function loadGroup(group) {
  ops.groups[group.key] = { ...(ops.groups[group.key] || {}), state: "loading" };
  render();
  try {
    const rows = await group.load();
    ops.groups[group.key] = { state: "loaded", rows };
  } catch (error) {
    ops.groups[group.key] = {
      state: "error",
      rows: (ops.groups[group.key] || {}).rows || [],
      error: error instanceof SourceStale
        ? `${error.message} Showing nothing rather than guessing.`
        : String(error.message || error),
    };
  }
  ops.loaded = true;
  render();
}

async function loadAll() {
  await Promise.all(GROUPS.map((group) => loadGroup(group)));
}

/* --- wiring -------------------------------------------------------------- */

function selectTab(id) {
  const tabs = Array.from(document.querySelectorAll('.tray-tabs [role="tab"]'));
  for (const tab of tabs) {
    const selected = tab.id === id;
    tab.setAttribute("aria-selected", selected ? "true" : "false");
    tab.tabIndex = selected ? 0 : -1;
    const panel = document.getElementById(tab.getAttribute("aria-controls"));
    if (panel) panel.hidden = !selected;
  }
  try {
    localStorage.setItem("brutus.tray.tab", id);
  } catch {
    /* private mode */
  }
  if (id === "tab-running" && !ops.loaded) loadAll();
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

function initControls() {
  $("#ops-search")?.addEventListener("input", (event) => {
    ops.query = event.target.value;
    render();
  });
  $("#ops-live-only")?.addEventListener("change", (event) => {
    ops.liveOnly = event.target.checked;
    render();
  });
  $("#ops-refresh")?.addEventListener("click", () => loadAll());
  for (const button of document.querySelectorAll("[data-ops-filter]")) {
    button.addEventListener("click", () => {
      ops.filter = button.dataset.opsFilter;
      for (const other of document.querySelectorAll("[data-ops-filter]")) {
        const on = other === button;
        other.classList.toggle("is-active", on);
        other.setAttribute("aria-pressed", on ? "true" : "false");
      }
      render();
    });
  }
  // The tray is collapsed at rest; opening it on Running is the first read.
  document.querySelector(".work-tray")?.addEventListener("toggle", (event) => {
    if (event.target.open && !ops.loaded && $("#tab-running")?.getAttribute("aria-selected") === "true") {
      loadAll();
    }
  });
}

function start() {
  if (!$("#panel-running")) return;
  initTabs();
  initControls();
  render();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", start, { once: true });
} else {
  start();
}

// One deliberate global, for the browser checks and for the console.
window.brutusOps = { ops, GROUPS, GROUP_BY_KEY, loadAll, render, selectTab, relative, statusTone };
})();
