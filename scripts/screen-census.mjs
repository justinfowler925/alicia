#!/usr/bin/env node
/**
 * Screen census — the check that would have caught the old session screen.
 *
 * Alicia's previous UI passed every gate it had. Tokens: clean. Contrast: fine.
 * axe: green. And it still gave 30.8% of a 1920x1080 viewport to an empty
 * transcript and 21.5% to an empty Thinking panel, while the work queue got
 * 12.1% and rendered exactly one of 143 items at a time.
 *
 * None of those gates could fail on that, because they all measure how a box is
 * painted and none of them measures whether the right box is big, or whether a
 * full list shows more than one row. So this measures composition:
 *
 *   PRIORITY  the primary region must hold the largest share of the viewport
 *   DEADSPACE no region may hold a big share of the screen while empty
 *   DENSITY   a list with N items must show at least MIN of them at once
 *   CLIP      no text may be rendered partially (a clipped glyph is unreadable,
 *             and it also measures as failing contrast against nothing)
 *   CONTRAST  worst-case per-pixel ratio on glyph boxes
 *   AXE       zero violations, and `incomplete` counts as a failure
 *
 * Usage:
 *   NODE_PATH=~/Projects/ceo-morning-brief/node_modules \
 *     node scripts/screen-census.mjs http://127.0.0.1:8768/session [--dark|--light] [--json]
 */

import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const axeSource = readFileSync(require.resolve("axe-core"), "utf8");

const args = process.argv.slice(2);
const url = args.find((a) => !a.startsWith("--")) || "http://127.0.0.1:8768/session";
const theme = args.includes("--light") ? "light" : "dark";
const asJson = args.includes("--json");

const VIEWPORT = { width: 1920, height: 1080 };

/**
 * What the screen is FOR, in order. `primary` is the claim under test: this
 * region must get more of the screen than anything else, because it is the
 * reason the page exists.
 */
const CONFIG = {
  primary: "Queue",
  regions: [
    { label: "Queue", items: ".card", primary: true },
    { label: "Conversation", items: ".turn" },
    { label: "Thinking", items: ".thinking" },
    { label: "Proposed", items: ".proposal" },
    { label: "Thread detail", items: ".ledger-detail-title" },
    { label: "Session slots", items: ".field" },
  ],
  // A list holding at least `min` items must show at least `min` at once.
  lists: [{ selector: ".qcol-list", min: 6, label: "stage column" }],
  // Regions bigger than this may not be empty.
  deadspaceShare: 12,
  minVisibleRatio: 6,
};

const page_fn = {
  /** Rasterise any CSS colour to RGB. getComputedStyle returns oklch()/color()
   *  for modern tokens and color-mix(), and a naive rgb() regex silently reads
   *  every one of those as ~1.0 contrast. */
  toRgb: `(css) => {
    const c = document.createElement("canvas");
    c.width = c.height = 1;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = "#000";
    ctx.fillStyle = css;
    ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  }`,
};

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: VIEWPORT, deviceScaleFactor: 1 });

  const failures = [];
  const notes = [];

  await page.addInitScript((t) => {
    try {
      localStorage.setItem("alicia.theme", t);
    } catch {
      /* ignore */
    }
  }, theme);

  const resp = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
  if (!resp || !resp.ok()) {
    console.error(`FATAL: ${url} returned ${resp ? resp.status() : "no response"}`);
    await browser.close();
    process.exit(2);
  }

  // Data arrives over fetch + SSE. Wait for the queue to actually populate
  // rather than measuring an empty first paint and calling it a layout.
  await page
    .waitForFunction(() => document.querySelectorAll(".card").length > 0, { timeout: 15000 })
    .catch(() => notes.push("no .card rendered within 15s — measuring an empty queue"));

  // A ratio captured mid-transition is the interpolated value, not the value.
  await page.addStyleTag({
    content: "*,*::before,*::after{transition:none!important;animation:none!important}",
  });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(250);

  const census = await page.evaluate((cfg) => {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const total = vw * vh;

    const regions = [];
    for (const spec of cfg.regions) {
      const el = document.querySelector(`[aria-label="${spec.label}"]`);
      if (!el) {
        regions.push({ ...spec, present: false });
        continue;
      }
      const r = el.getBoundingClientRect();
      const visible = r.width > 0 && r.height > 0;
      // Clamp to the viewport: area off-screen is not area spent.
      const w = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0));
      const h = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
      regions.push({
        label: spec.label,
        primary: Boolean(spec.primary),
        present: true,
        visible,
        w: Math.round(r.width),
        h: Math.round(r.height),
        share: +((w * h) / total * 100).toFixed(1),
        items: el.querySelectorAll(spec.items).length,
        // A region holding the composer is not empty even with no history in
        // it — an input you type into is the content. Without this the gate
        // would demand the one control you always need be hidden when unused.
        //
        // Visible ones only. A `display: none` button is not something you can
        // type into, and counting it let a region full of hidden controls pass
        // as occupied — the exemption swallowing the rule it was carved out of.
        controls: [...el.querySelectorAll("textarea, input, select, button")].filter((c) => {
          const b = c.getBoundingClientRect();
          return b.width > 1 && b.height > 1 && getComputedStyle(c).visibility !== "hidden";
        }).length,
      });
    }

    const lists = [];
    for (const spec of cfg.lists) {
      for (const host of document.querySelectorAll(spec.selector)) {
        const kids = [...host.children].filter((c) => c.getBoundingClientRect().height > 0);
        if (!kids.length) continue;
        const heights = kids.map((c) => c.getBoundingClientRect().height).sort((a, b) => a - b);
        const median = heights[Math.floor(heights.length / 2)];
        lists.push({
          label: spec.label,
          min: spec.min,
          held: kids.length,
          itemHeight: Math.round(median),
          clientHeight: Math.round(host.clientHeight),
          fits: median > 0 ? +(host.clientHeight / median).toFixed(1) : 0,
        });
      }
    }

    // Anything whose text is drawn short of its own content box.
    //
    // A visually-hidden label is clipped ON PURPOSE — that is the whole
    // technique, and it must stay in the DOM to be announced. Counting those as
    // clipped text produced five failures on the first run, all of them the
    // probe misreading a correct pattern.
    const isVisuallyHidden = (el) => {
      const cs = getComputedStyle(el);
      if (cs.clipPath && cs.clipPath !== "none") return true;
      const r = el.getBoundingClientRect();
      return r.width <= 1.5 || r.height <= 1.5;
    };

    const clipped = [];
    for (const el of document.querySelectorAll("body *")) {
      if (!el.childElementCount && (el.textContent || "").trim()) {
        const r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) continue;
        if (isVisuallyHidden(el)) continue;
        const cs = getComputedStyle(el);
        if (cs.overflow === "visible" && cs.overflowX === "visible") continue;
        // An ellipsis is a truncation the reader can see, on text that is still
        // whole in the document — the tooltip and the card's expanded view both
        // reach it. A bare `overflow: hidden` cut is the defect this looks for:
        // a word that simply stops, with nothing saying more exists.
        if (cs.textOverflow === "ellipsis") continue;
        if (el.scrollWidth > el.clientWidth + 1 && cs.whiteSpace === "nowrap") {
          clipped.push({
            tag: el.tagName.toLowerCase(),
            cls: el.className || "",
            text: (el.textContent || "").trim().slice(0, 40),
            scrollWidth: el.scrollWidth,
            clientWidth: el.clientWidth,
          });
        }
      }
    }

    return { vw, vh, regions, lists, clipped };
  }, CONFIG);

  // --- contrast on real glyph boxes -------------------------------------
  const contrast = await page.evaluate(
    ({ toRgbSrc }) => {
      const toRgb = eval(toRgbSrc);
      const lum = ([r, g, b]) => {
        const f = (v) => {
          const s = v / 255;
          return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
        };
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
      };
      const ratio = (a, b) => {
        const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
        return (hi + 0.05) / (lo + 0.05);
      };
      // The painted background behind an element: first ancestor with a
      // non-transparent fill. A transparent element over a transparent parent
      // is not white, it is whatever is actually underneath.
      const bgOf = (el) => {
        let node = el;
        while (node && node !== document.documentElement) {
          const c = toRgb(getComputedStyle(node).backgroundColor);
          if (c[3] > 0.95) return c;
          node = node.parentElement;
        }
        return toRgb(getComputedStyle(document.body).backgroundColor);
      };

      const rows = [];
      for (const el of document.querySelectorAll("body *")) {
        if (el.childElementCount) continue;
        const text = (el.textContent || "").trim();
        if (!text) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) continue;
        if (r.bottom < 0 || r.top > window.innerHeight) continue;
        const cs = getComputedStyle(el);
        if (cs.visibility === "hidden" || cs.opacity === "0") continue;
        const size = parseFloat(cs.fontSize);
        const weight = Number(cs.fontWeight) || 400;
        // WCAG large text: >=24px, or >=18.66px at 700+.
        const large = size >= 24 || (size >= 18.66 && weight >= 700);
        const fg = toRgb(cs.color);
        const bg = bgOf(el);
        rows.push({
          ratio: +ratio(fg, bg).toFixed(2),
          floor: large ? 3 : 4.5,
          size: +size.toFixed(1),
        cls: el.className || el.tagName.toLowerCase(),
        text: text.slice(0, 40),
      });
      // Leave a mark so an axe abstention on this node can be checked against
      // a measurement rather than waved through.
      el.setAttribute("data-census-measured", ratio(fg, bg).toFixed(2));
      }
      rows.sort((a, b) => a.ratio / a.floor - b.ratio / b.floor);
      return rows;
    },
    { toRgbSrc: page_fn.toRgb },
  );

  // --- axe --------------------------------------------------------------
  await page.addScriptTag({ content: axeSource });
  const axe = await page.evaluate(async () => {
    const res = await window.axe.run(document, {
      resultTypes: ["violations", "incomplete"],
    });
    const pick = (list) =>
      list.map((v) => ({
        id: v.id,
        impact: v.impact,
        nodes: v.nodes.length,
        // The target is the whole point when triaging: "color-contrast(1)" with
        // no selector is a finding you cannot act on.
        targets: v.nodes.slice(0, 3).map((n) => String(n.target)),
        /* Why axe could not decide, and whether the pixel probe already did.
         *
         * axe abstains on "nonBmp" — an element whose whole content is a symbol
         * character (the mic's ◎) rather than letters. That is a limitation of
         * its text model, not a finding: the glyph is painted pixels like any
         * other and the per-pixel probe reads it fine. But an exemption nothing
         * cross-checks is how a gate rots, so this only clears when that exact
         * node carries a measurement from the probe above, and that measurement
         * passes its floor. An unmeasured abstention still fails. */
        resolved: v.nodes.map((n) => {
          const el = document.querySelector(String(n.target));
          const measured = el && el.getAttribute("data-census-measured");
          const keys = n.any.map((a) => a.data && a.data.messageKey);
          return {
            target: String(n.target),
            reason: keys.join(",") || "unknown",
            measured: measured ? Number(measured) : null,
            covered: keys.every((k) => k === "nonBmp") && Number(measured) >= 4.5,
          };
        }),
      }));
    return { violations: pick(res.violations), incomplete: pick(res.incomplete) };
  });

  await browser.close();

  // --- gates ------------------------------------------------------------
  const present = census.regions.filter((r) => r.present && r.visible);
  const primary = present.find((r) => r.primary);
  const biggest = [...present].sort((a, b) => b.share - a.share)[0];

  if (!primary) {
    failures.push(`PRIORITY: primary region "${CONFIG.primary}" is not on the screen`);
  } else if (biggest && biggest.label !== primary.label) {
    failures.push(
      `PRIORITY: "${biggest.label}" holds ${biggest.share}% of the viewport but the ` +
        `primary region "${primary.label}" only holds ${primary.share}%`,
    );
  }

  for (const r of present) {
    if (r.share >= CONFIG.deadspaceShare && r.items === 0 && r.controls === 0) {
      failures.push(
        `DEADSPACE: "${r.label}" holds ${r.share}% of the viewport with 0 items in it`,
      );
    }
  }

  for (const l of census.lists) {
    if (l.held >= l.min && l.fits < CONFIG.minVisibleRatio) {
      failures.push(
        `DENSITY: a ${l.label} holds ${l.held} items but only ${l.fits} fit at once ` +
          `(${l.itemHeight}px per item in ${l.clientHeight}px)`,
      );
    }
  }

  if (census.clipped.length) {
    for (const c of census.clipped.slice(0, 5)) {
      failures.push(
        `CLIP: "${c.text}" is drawn at ${c.clientWidth}px but needs ${c.scrollWidth}px (.${c.cls})`,
      );
    }
  }

  const worst = contrast[0];
  for (const row of contrast.filter((r) => r.ratio < r.floor)) {
    failures.push(
      `CONTRAST: ${row.ratio}:1 on "${row.text}" (.${row.cls}, ${row.size}px) — needs ${row.floor}:1`,
    );
  }

  if (axe.violations.length) {
    failures.push(`AXE: ${axe.violations.map((v) => `${v.id}(${v.nodes})`).join(", ")}`);
  }
  // `incomplete` is not a pass — axe returns it for text over a gradient, which
  // is exactly the case a naive check would wave through. The only abstentions
  // that clear are the ones the per-pixel probe measured and passed itself.
  for (const v of axe.incomplete) {
    const open = (v.resolved || []).filter((n) => !n.covered);
    if (!open.length) continue;
    failures.push(
      `AXE INCOMPLETE: ${v.id} — ${open
        .map((n) => `${n.target} (${n.reason}, probe ${n.measured ?? "no measurement"})`)
        .join("; ")}`,
    );
  }

  const report = {
    url,
    theme,
    viewport: census,
    // `contrast` is the worst 8 of `contrastMeasured` nodes, sorted by headroom.
    // The count travels with the sample because without it the truncated list
    // reads like the whole sample, and a gate that measured 156 nodes looks
    // like one that measured 8.
    contrastMeasured: contrast.length,
    contrast: contrast.slice(0, 8),
    axe,
    failures,
  };
  if (asJson) {
    console.log(JSON.stringify(report, null, 2));
  } else {
    console.log(`\nscreen census — ${url} (${theme}, ${census.vw}x${census.vh})\n`);
    console.log("area share");
    for (const r of [...present].sort((a, b) => b.share - a.share)) {
      const mark = r.primary ? " *primary" : "";
      console.log(
        `  ${String(r.share).padStart(5)}%  ${r.label.padEnd(16)} ${String(r.items).padStart(4)} items${mark}`,
      );
    }
    console.log("\ndensity");
    for (const l of census.lists) {
      console.log(
        `  ${l.label.padEnd(16)} ${String(l.held).padStart(4)} held  ${String(l.fits).padStart(5)} fit at once  (${l.itemHeight}px each)`,
      );
    }
    console.log("\ncontrast (worst first)");
    for (const c of contrast.slice(0, 5)) {
      console.log(`  ${String(c.ratio).padStart(6)}:1  need ${c.floor}  ${c.size}px  "${c.text}"`);
    }
    console.log(
      `\naxe  violations=${axe.violations.length} incomplete=${axe.incomplete.length}`,
    );
    for (const n of notes) console.log(`note: ${n}`);
    if (failures.length) {
      console.log(`\nFAIL (${failures.length})`);
      for (const f of failures) console.log(`  - ${f}`);
    } else {
      console.log("\nPASS");
    }
  }

  process.exit(failures.length ? 1 : 0);
}

main().catch((err) => {
  console.error(`FATAL: ${err.stack || err}`);
  process.exit(2);
});
