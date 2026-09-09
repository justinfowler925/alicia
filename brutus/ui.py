"""Brutus — Justin's whole work surface.

The front door is a wide command center: capture work, decide what needs
Justin, then drill into the project, ticket, or agent that needs attention.
Conversation is available as a full-screen workspace, never a tiny sidecar. Views:

* **Work** — the dense board. Real ticket titles, stuck grouped by cause.
* **Agents** — local coding-agent threads on this laptop (track, keep, park).
* **Projects** — every git repo under ~/Projects, straight from git: last
  commit, branch, uncommitted files, unpushed commits. The streams that never
  had a plan doc live here.
* **Notes** — two-second capture for TODOs / WIP / ideas, each promotable into
  the real ledger with one click.

Rules that survived three rewrites: no internal vocabulary on screen, no
charts, real titles always, explanation once per group, buttons say what they
do.
"""

from .studio_ui import STUDIO_JS

BRUTUS_HTML = """<!DOCTYPE html>
<html lang="en" data-cite="antd-pro-list" data-shine-voice="adapted" data-shine-theme="dark">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Brutus</title>
  <script>(function(){try{var t=localStorage.getItem('brutus.theme');if(t==='light'||t==='dark')document.documentElement.dataset.theme=t;}catch(e){}})();</script>
  <link rel="stylesheet" href="/static/shine-tokens.css" />
  <style>
    :root{
      /* Declared, or the UA paints its widgets and scrollbars in light chrome
         against a dark page. Follows `data-theme` because that is what the
         toggle sets — `prefers-color-scheme` is deliberately not consulted. */
      color-scheme:dark;
      /* Ops short aliases → shine personal. No raw hex in this sheet. */
      --bg:var(--shine-color-bg);
      --panel:var(--shine-color-bg-subtle);
      --hover:var(--shine-color-stone-800);
      --line:var(--shine-color-border);
      --text:var(--shine-color-fg);
      --dim:var(--shine-color-fg-muted);
      --dim2:var(--shine-color-fg-muted);
      --blue:var(--shine-color-ember-300);
      --ok:var(--shine-color-success);
      --warn:var(--shine-color-warning);
      --bad:var(--shine-color-danger);
      --brand:var(--shine-color-primary);
      --on-brand:var(--shine-color-primary-fg);
      --on-solid:var(--shine-color-stone-50);
      --soft-ok:color-mix(in srgb, var(--shine-color-success) 18%, var(--shine-color-bg));
      --soft-warn:color-mix(in srgb, var(--shine-color-warning) 16%, var(--shine-color-bg));
      --soft-bad:color-mix(in srgb, var(--shine-color-danger) 18%, var(--shine-color-bg));
      --soft-bad-line:color-mix(in srgb, var(--shine-color-danger) 45%, var(--shine-color-bg));
      --soft-bad-fg:var(--shine-color-stone-200);
      --soft-info:color-mix(in srgb, var(--shine-color-ember-400) 22%, var(--shine-color-bg-subtle));
      --soft-info-fg:var(--shine-color-stone-200);
      --soft-cur:var(--shine-color-stone-800);
      --soft-cur-fg:var(--shine-color-stone-200);
      --soft-go:color-mix(in srgb, var(--shine-color-success) 55%, var(--shine-color-stone-950));
      --soft-warn-line:color-mix(in srgb, var(--shine-color-warning) 40%, var(--shine-color-bg));
      --soft-ask:color-mix(in srgb, var(--shine-color-warning) 10%, var(--shine-color-bg));
      --soft-ask-fg:var(--shine-color-stone-200);
      --tap:44px;  /* pointer hit-target floor — 44px is the WCAG 2.5.8 / HIG figure */
      --gut:var(--shine-space-8);
    }
    /* Dark-locked alias stones flip with the personal light block. */
    [data-theme="light"]{
      color-scheme:light;
      --hover:var(--shine-color-stone-200);
      --soft-info-fg:var(--shine-color-stone-800);
      --soft-bad-fg:var(--shine-color-stone-800);
      --soft-cur:var(--shine-color-stone-200);
      --soft-cur-fg:var(--shine-color-stone-800);
      --soft-ask-fg:var(--shine-color-stone-800);
      --on-solid:var(--shine-color-stone-950);
    }
    *{box-sizing:border-box}
    body{margin:0;background:var(--bg);color:var(--text);
      font-family:var(--shine-font-sans);font-size:var(--shine-text-sm);
      line-height:var(--shine-leading-normal)}
    .app{display:grid;grid-template-columns:minmax(0,1fr);grid-template-rows:auto minmax(0,1fr);min-height:100vh}
    .main{grid-row:2;padding:var(--shine-space-6) var(--shine-space-6) var(--shine-space-10);min-width:0;
      width:min(100%,112rem);margin:0 auto}
    /* NOTE: every max-width:900px override lives in ONE block at the end of this
       sheet. It used to sit here, above `.rail{display:flex}` — same specificity,
       earlier in source, so `.rail{display:none}` lost and the rail rendered as a
       full extra screen below the board on phones. Do not move it back up. */
    .sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-4px;overflow:hidden;
      clip:rect(0,0,0,0);white-space:nowrap;border:0}
    :focus-visible{outline:2px solid var(--brand);outline-offset:2px}
    button.linkish{background:none;border:0;padding:0;color:var(--blue);font:inherit;
      font-size:inherit;cursor:pointer;text-decoration:underline}
    button.linkish.dim{color:var(--dim2)}
    button.linkish:hover{color:var(--text)}

    /* ---------- command header ---------- */
    .rail{grid-row:1;z-index:20;border-bottom:1px solid var(--line);background:var(--panel);
      display:flex;align-items:center;gap:var(--shine-space-3);position:sticky;top:0;min-height:4.5rem;padding:0 var(--shine-space-6)}
    .rail h1{font-size:var(--shine-text-base);font-weight:700;margin:0;white-space:nowrap}
    .rail h1 span{color:var(--brand)}
    .rail .tag{color:var(--dim2);font-size:var(--shine-text-xs);margin:0;white-space:nowrap}
    .theme-wrap{margin-left:auto}
    .theme-wrap #theme-toggle{width:auto;padding:8px 12px;font-size:var(--shine-text-xs);min-height:var(--tap);
      background:transparent;border:1px solid var(--line);border-radius:var(--shine-radius-sm);color:var(--text)}
    .nav-block{min-width:0}
    .nav-block>.sec{display:none}
    .nav{display:flex;align-items:center;gap:var(--shine-space-1);overflow-x:auto}
    .nav .nav-item{display:inline-flex;align-items:center;gap:8px;white-space:nowrap;padding:8px 10px;
      color:var(--dim);text-decoration:none;font-size:var(--shine-text-xs);font-weight:650;border:1px solid transparent;
      border-radius:var(--shine-radius-sm);cursor:pointer;background:transparent;text-align:left;min-height:var(--tap)}
    .nav .nav-item:hover{color:var(--text);background:var(--hover)}
    .nav .nav-item.on{color:var(--text);border-color:var(--brand);background:var(--hover)}
    .nav .nav-item .n{font-size:var(--shine-text-xs);color:var(--dim2);
      font-variant-numeric:tabular-nums}
    .command-action{background:var(--brand);border:0;color:var(--on-brand);border-radius:var(--shine-radius-md);
      padding:8px 14px;min-height:var(--tap);font:inherit;font-size:var(--shine-text-xs);font-weight:750;white-space:nowrap;cursor:pointer}
    .command-action.secondary{background:transparent;border:1px solid var(--line);color:var(--text)}
    .command-action:hover{filter:brightness(1.08)}
    .bots-block,.rail-chat-wrap{display:none}
    .nav-more{position:relative}
    .nav-more>summary{display:inline-flex;align-items:center;min-height:var(--tap);padding:8px 10px;cursor:pointer;
      border:1px solid transparent;border-radius:var(--shine-radius-sm);color:var(--dim);font-size:var(--shine-text-xs);font-weight:650;list-style:none}
    .nav-more>summary::-webkit-details-marker{display:none}
    .nav-more[open]>summary{color:var(--text);border-color:var(--line);background:var(--hover)}
    .nav-more .tool-menu{position:absolute;right:0;top:calc(100% + var(--shine-space-2));z-index:30;display:grid;
      min-width:11rem;padding:var(--shine-space-2);border:1px solid var(--line);border-radius:var(--shine-radius-md);background:var(--panel);box-shadow:var(--shine-shadow-md)}
    .nav-more .nav-item{width:100%}
    .bots{padding:4px 16px 8px}
    .bot{display:flex;align-items:center;gap:8px;padding:4px 0;font-size:var(--shine-text-sm);color:var(--dim)}
    .dot{width:8px;height:8px;border-radius:50%;background:var(--dim2);flex:0 0 auto}
    .dot.ok{background:var(--ok)} .dot.bad{background:var(--bad)} .dot.warn{background:var(--warn)}
    .bot .who{color:var(--text)}
    .bot .st{margin-left:auto;font-size:var(--shine-text-xs);color:var(--dim2)}

    /* ---------- conversation workspace ---------- */
    .chat{flex:1;display:flex;flex-direction:column;border-top:4px solid var(--line);min-height:0}
    .chat .sec{padding-top:12px}
    #msgs{flex:1;overflow-y:auto;padding:4px 16px 8px;display:flex;flex-direction:column;gap:8px}
    .m{max-width:95%;padding:8px 12px;border-radius:var(--shine-radius-md);font-size:var(--shine-text-sm);
      white-space:pre-wrap;word-break:break-word}
    .m.me{align-self:flex-end;background:var(--soft-info);color:var(--soft-info-fg)}
    .m.bot{align-self:flex-start;background:var(--hover)}
    .m.sys{align-self:center;background:transparent;color:var(--dim2);font-size:var(--shine-text-xs)}
    .m .who{display:block;font-size:var(--shine-text-xs);color:var(--brand);margin-bottom:4px}
    /* The composer wraps: message field on its own row, controls beneath. In a
       300px rail, one row left the input at 58px once the controls met the
       44px tap floor. */
    .chatin{display:flex;flex-wrap:wrap;gap:8px;padding:8px 12px;
      border-top:4px solid var(--line);align-items:center}
    .chatin input{order:-1;flex:1 1 100%;background:var(--bg);border:1px solid var(--line);
      color:var(--text);border-radius:var(--shine-radius-md);padding:8px 12px;min-height:var(--tap);
      font-size:var(--shine-text-sm);min-width:0}
    .chatin>button:last-child{margin-left:auto}
    .chatin input:focus{outline:none}
    .chatin input:focus-visible{border-color:var(--brand)}
    .chatin button{background:var(--brand);border:0;color:var(--on-brand);border-radius:var(--shine-radius-md);
      padding:8px 16px;min-height:var(--tap);font-weight:700;font-size:var(--shine-text-sm);cursor:pointer}
    .chatin button.icon{background:transparent;border:1px solid var(--line);color:var(--dim);
      padding:8px 12px;min-width:var(--tap)}
    .chatin button.icon:hover{color:var(--text);border-color:var(--dim)}
    .chatin button.icon.on{color:var(--brand);border-color:var(--brand)}
    .chatin button.icon.rec{color:var(--on-solid);background:var(--bad);border-color:var(--bad)}
    .chatin button.icon:disabled{opacity:.35;cursor:not-allowed}

    /* ---------- shared ---------- */
    #head{font-size:var(--shine-text-lg);font-weight:600;margin:4px 0 4px}
    #sub{color:var(--dim);font-size:var(--shine-text-xs);margin-bottom:16px}
    #sub .ok{color:var(--ok)} #sub .bad{color:var(--bad)} #sub .warn{color:var(--warn)}
    #alarm{display:none;background:var(--soft-bad);border:1px solid var(--soft-bad-line);color:var(--soft-bad-fg);
      border-radius:var(--shine-radius-md);padding:12px 12px;margin:0 0 16px;font-size:var(--shine-text-sm);font-weight:600}
    .door-link{display:inline-flex;align-items:center;gap:8px;margin:0 0 12px;font-size:var(--shine-text-xs)}
    .door-link a{color:var(--blue);text-decoration:none;min-height:var(--tap);display:inline-flex;align-items:center}
    .door-link a:hover{text-decoration:underline}
    .streams{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 12px}
    .streams .chip{display:inline-flex;align-items:center;gap:4px;padding:4px 8px;border:1px solid var(--line);
      border-radius:var(--shine-radius-sm);font-size:var(--shine-text-xs);color:var(--dim);background:var(--panel);
      min-height:var(--tap);cursor:pointer;font:inherit}
    .streams .chip:hover{border-color:var(--blue);color:var(--text)}
    .streams .chip.hot{color:var(--warn);border-color:var(--soft-warn-line)}
    .brief{border:1px solid var(--line);border-radius:var(--shine-radius-md);background:var(--panel);
      margin:0 0 16px;overflow:hidden}
    .brief>summary{display:flex;align-items:center;gap:8px;padding:8px 12px;cursor:pointer;font-weight:650;
      font-size:var(--shine-text-sm);list-style:none;min-height:var(--tap)}
    .brief>summary::-webkit-details-marker{display:none}
    .brief>summary .tag{font-size:var(--shine-text-xs);color:var(--dim2);font-weight:600;letter-spacing:var(--shine-tracking-caps);
      text-transform:uppercase}
    .brief .body{padding:0 12px 12px;color:var(--dim);font-size:var(--shine-text-xs);white-space:pre-wrap;
      max-height:240px;overflow:auto;line-height:var(--shine-leading-normal)}
    .brief .body:empty::before{content:"Loading brief…";color:var(--dim2)}
    .act-card{border:1px solid var(--line);border-radius:var(--shine-radius-md);margin:8px 0;overflow:hidden;background:var(--panel)}
    .act-card.you{border-color:var(--soft-warn-line)}
    .act-card .act-h{display:flex;align-items:flex-start;gap:12px;padding:12px}
    .act-card .act-h .cnt{flex:0 0 34px;font-family:var(--shine-font-mono);font-size:var(--shine-text-sm);font-weight:700;
      line-height:var(--shine-leading-tight);color:var(--warn)}
    .act-card .act-h .meta{flex:1;min-width:0}
    .act-card .act-h .ttl{font-weight:650;margin:0 0 4px}
    .act-card .act-h .why{color:var(--dim);font-size:var(--shine-text-xs);margin:0;overflow-wrap:anywhere}
    .act-card .act-h .do{color:var(--text);font-size:var(--shine-text-xs);margin:4px 0 0;font-weight:550}
    .act-card .act-ops{display:flex;flex-wrap:wrap;gap:8px;padding:0 12px 12px}
    .act-card .act-ops button{background:var(--panel);border:1px solid var(--line);color:var(--text);
      border-radius:var(--shine-radius-sm);padding:8px 12px;min-height:var(--tap);font-size:var(--shine-text-xs);
      font-weight:650;cursor:pointer}
    .act-card .act-ops button.prim{background:var(--soft-go);border:0;color:var(--on-solid)}
    .act-card .act-ops button.alt{background:transparent}
    .act-card .act-ops button.warnish{border-color:var(--soft-warn-line);color:var(--warn)}
    .act-card .act-items{display:none;border-top:4px solid var(--line);padding:4px 8px 8px}
    .act-card.open .act-items{display:block}
    .act-card .ans-row{display:flex;gap:8px;padding:0 12px 12px;flex-wrap:wrap}
    .act-card .ans-row input{flex:1 1 220px;min-height:var(--tap);background:var(--bg);border:1px solid var(--line);
      border-radius:var(--shine-radius-sm);color:var(--text);padding:8px 12px;font:inherit}
    .act-card .ans-row input:focus-visible{border-color:var(--brand);outline:none}
    .sech{display:flex;align-items:center;gap:8px;padding:8px 4px 8px;border-bottom:4px solid var(--line);
      margin:16px 0 4px;position:sticky;top:0;z-index:2;background:var(--bg)}
    .sech:first-child{margin-top:0}
    .sech .name{font-size:var(--shine-text-xs);font-weight:700;letter-spacing:var(--shine-tracking-caps);text-transform:uppercase;color:var(--dim)}
    .sech.you .name{color:var(--warn)}
    .sech .n{font-size:var(--shine-text-xs);font-weight:700;color:var(--dim2)}
    .sech .right{margin-left:auto}
    .none{color:var(--dim2);font-size:var(--shine-text-sm);padding:12px 8px}
    .r{display:flex;align-items:center;gap:12px;padding:8px 8px;border-radius:var(--shine-radius-sm);border-bottom:4px solid var(--line)}
    .r:hover{background:var(--hover)} .r:last-child{border-bottom:0}
    /* The title is the content; the id and age are labels on it. Both metadata
       columns shrink before the title does. */
    .r .id{flex:0 1 78px;min-width:52px;font-family:var(--shine-font-mono);font-size:var(--shine-text-xs);font-weight:600;
      line-height:var(--shine-leading-tight);
      color:var(--blue);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .r .t{flex:1 1 auto;min-width:0;font-weight:550;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .r .a{flex:0 1 52px;text-align:right;color:var(--dim2);font-size:var(--shine-text-xs);font-variant-numeric:tabular-nums}
    .r .b{flex:0 0 auto;display:flex;gap:8px;align-items:center}
    .r button{background:var(--panel);border:1px solid var(--line);color:var(--text);
      border-radius:var(--shine-radius-sm);padding:4px 8px;min-height:var(--tap);font-size:var(--shine-text-xs);font-weight:600;cursor:pointer}
    .r button:hover{border-color:var(--blue)}
    .r a{color:var(--blue);font-size:var(--shine-text-xs);text-decoration:none;display:inline-flex;
      align-items:center;min-height:var(--tap);padding:0 4px}
    .r a:hover{text-decoration:underline}
    .grp{border:1px solid var(--line);border-radius:var(--shine-radius-md);margin:8px 0;overflow:hidden}
    .grp-h{display:flex;align-items:center;gap:12px;width:100%;padding:8px 12px;cursor:pointer;
      background:var(--panel);border:0;color:inherit;font:inherit;text-align:left}
    .grp-h:hover{background:var(--hover)}
    .grp-h .cnt{flex:0 0 34px;font-family:var(--shine-font-mono);font-size:var(--shine-text-sm);font-weight:700;
      line-height:var(--shine-leading-tight);color:var(--warn)}
    .grp-h .rsn{flex:1;font-weight:600}
    .grp-h .car{color:var(--dim2);font-size:var(--shine-text-xs)}
    .grp .why{padding:0 var(--shine-space-3) var(--shine-space-3) var(--shine-space-7);color:var(--dim);font-size:var(--shine-text-xs);overflow-wrap:anywhere}
    .grp .act{padding:0 var(--shine-space-3) var(--shine-space-3) var(--shine-space-7)}
    .grp .act button{background:var(--soft-go);border:0;color:var(--on-solid);border-radius:var(--shine-radius-sm);padding:8px 12px;
      min-height:var(--tap);font-size:var(--shine-text-xs);font-weight:650;cursor:pointer}
    .grp .rows{display:none;padding:4px 8px 8px;border-top:4px solid var(--line)}
    .grp.open .rows{display:block}
    /* --gut is the card indent (set in :root). A fixed 89px pushed long
       unbreakable tokens past the viewport on phones — mobile zeroes it. */
    .q{background:var(--soft-ask);border-left:4px solid var(--warn);border-radius:0 var(--shine-radius-sm) var(--shine-radius-sm) 0;
      padding:8px 12px;margin:4px 0 8px var(--gut);font-size:var(--shine-text-sm);color:var(--soft-ask-fg);
      overflow-wrap:anywhere}
    .q .more{display:inline-block;margin-top:8px}
    .q button.linkish{color:var(--warn);font-size:var(--shine-text-xs)}
    .q button.linkish:hover{color:var(--on-solid)}
    .ans{display:flex;gap:8px;margin:0 0 12px var(--gut);flex-wrap:wrap}
    .ans input{flex:1;min-width:210px;background:var(--bg);border:1px solid var(--line);
      color:var(--text);border-radius:var(--shine-radius-sm);padding:8px 8px;min-height:var(--tap);font-size:var(--shine-text-sm)}
    .ans button{background:var(--soft-go);border:0;color:var(--on-solid);border-radius:var(--shine-radius-sm);padding:8px 12px;
      min-height:var(--tap);font-size:var(--shine-text-sm);font-weight:650;cursor:pointer}
    .ans button.alt{background:var(--panel);border:1px solid var(--line);color:var(--text)}
    /* Start over restarts real work — it must not read as a peer of Reject. */
    .ans button.warnish{background:transparent;border:1px solid var(--soft-warn-line);color:var(--warn)}
    .ans button.warnish:hover{border-color:var(--warn)}

    /* list toolbars */
    .list-bar,.filt{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:0 0 12px}
    .list-bar input,.list-bar select,.filt input,.filt select{background:var(--panel);
      border:1px solid var(--line);color:var(--text);border-radius:var(--shine-radius-sm);padding:8px 8px;
      min-height:var(--tap);font-size:var(--shine-text-sm)}
    .list-bar input,.filt input{min-width:220px;flex:1}
    .list-bar input:focus,.filt input:focus,.list-bar select:focus,.filt select:focus{outline:none}
    .list-bar input:focus-visible,.filt input:focus-visible,.list-bar select:focus-visible,
    .filt select:focus-visible{border-color:var(--brand)}
    .list-bar button,.filt button{background:var(--panel);border:1px solid var(--line);
      color:var(--text);border-radius:var(--shine-radius-sm);padding:8px 12px;min-height:var(--tap);
      font-size:var(--shine-text-sm);font-weight:600;cursor:pointer}
    .list-bar button:hover,.filt button:hover{border-color:var(--blue)}
    .list-bar .dir{font-variant-numeric:tabular-nums;min-width:var(--tap)}
    .list-bar .count{color:var(--dim);font-size:var(--shine-text-xs);margin-left:auto}
    /* one honest "showing N of M" pager instead of dumping every row */
    .more-bar{display:flex;align-items:center;gap:8px;padding:12px 8px;color:var(--dim);font-size:var(--shine-text-sm)}
    .more-bar button{background:var(--panel);border:1px solid var(--line);color:var(--text);
      border-radius:var(--shine-radius-sm);padding:8px 12px;min-height:var(--tap);font-size:var(--shine-text-sm);font-weight:600;cursor:pointer}
    .more-bar button:hover{border-color:var(--blue)}

    /* ---------- Nucleus command center: antd-pro-list region graph ---------- */
    .nucleus-shell{display:grid;gap:var(--shine-space-4)}
    .nucleus-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:var(--shine-space-3)}
    .nucleus-metric{background:var(--panel);border:1px solid var(--line);border-radius:var(--shine-radius-md);
      padding:var(--shine-space-4);min-width:0}
    .nucleus-metric .value{display:block;font-family:var(--shine-font-mono);font-size:var(--shine-text-xl);
      font-weight:700;line-height:var(--shine-leading-tight)}
    .nucleus-metric .label{display:block;color:var(--dim);font-size:var(--shine-text-xs);margin-top:var(--shine-space-1)}
    .nucleus-source{display:flex;gap:var(--shine-space-2);flex-wrap:wrap;align-items:center;color:var(--dim);
      font-size:var(--shine-text-xs)}
    .nucleus-source .source{display:inline-flex;align-items:center;gap:var(--shine-space-1);padding:var(--shine-space-1) var(--shine-space-2);
      border:1px solid var(--line);border-radius:var(--shine-radius-sm);background:var(--panel)}
    .nucleus-toolbar{display:flex;gap:var(--shine-space-2);align-items:center;flex-wrap:wrap;padding:var(--shine-space-3);
      background:var(--panel);border:1px solid var(--line);border-radius:var(--shine-radius-md)}
    .nucleus-toolbar input,.nucleus-toolbar select{background:var(--bg);border:1px solid var(--line);color:var(--text);
      border-radius:var(--shine-radius-sm);padding:var(--shine-space-2) var(--shine-space-3);min-height:var(--tap);font:inherit}
    .nucleus-toolbar input{flex:1 1 18rem;min-width:12rem}
    .nucleus-toolbar button,.nucleus-detail button,.nucleus-table button{background:var(--panel);border:1px solid var(--line);
      color:var(--text);border-radius:var(--shine-radius-sm);padding:var(--shine-space-2) var(--shine-space-3);
      min-height:var(--tap);font:inherit;font-size:var(--shine-text-xs);font-weight:650;cursor:pointer}
    .nucleus-toolbar button.prim,.nucleus-detail button.prim{background:var(--brand);border-color:var(--brand);color:var(--on-brand)}
    .nucleus-toolbar button:disabled{opacity:.45;cursor:not-allowed}
    .nucleus-cols{position:relative}
    .nucleus-cols>summary{list-style:none;cursor:pointer;min-height:var(--tap);display:flex;align-items:center;
      padding:var(--shine-space-2) var(--shine-space-3);border:1px solid var(--line);border-radius:var(--shine-radius-sm)}
    .nucleus-cols>summary::-webkit-details-marker{display:none}
    .nucleus-cols .menu{position:absolute;right:0;top:100%;z-index:5;min-width:12rem;background:var(--panel);
      border:1px solid var(--line);border-radius:var(--shine-radius-md);padding:var(--shine-space-3);box-shadow:var(--shine-shadow-md)}
    .nucleus-cols:not([open]) .menu{display:none}
    .nucleus-cols label{display:flex;gap:var(--shine-space-2);align-items:center;min-height:var(--tap);color:var(--text);opacity:1}
    .nucleus-grid{overflow:auto;border:1px solid var(--line);border-radius:var(--shine-radius-md);background:var(--panel)}
    .nucleus-table{width:100%;border-collapse:collapse;table-layout:fixed;min-width:64rem}
    .nucleus-table th,.nucleus-table td{padding:var(--shine-space-3);border-bottom:1px solid var(--line);text-align:left;
      vertical-align:top;overflow:hidden;text-overflow:ellipsis}
    .nucleus-table th{position:sticky;top:0;z-index:1;color:var(--dim);font-size:var(--shine-text-xs);letter-spacing:var(--shine-tracking-caps);
      text-transform:uppercase;background:var(--panel)}
    .nucleus-table th button{min-height:var(--tap);padding:0;border:0;background:transparent;text-transform:inherit;letter-spacing:inherit;color:inherit}
    .nucleus-table tr{cursor:pointer}
    .nucleus-table tbody tr:hover,.nucleus-table tbody tr.selected{background:var(--hover)}
    .nucleus-table .project-cell strong{display:block;color:var(--text);font-size:var(--shine-text-sm)}
    .nucleus-table .project-cell span,.nucleus-table .cell-sub{display:block;color:var(--dim);font-size:var(--shine-text-xs);
      white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:var(--shine-space-1)}
    .nucleus-table .num{font-family:var(--shine-font-mono);font-variant-numeric:tabular-nums}
    .nucleus-table .status{display:inline-flex;padding:var(--shine-space-1) var(--shine-space-2);border-radius:var(--shine-radius-sm);
      background:var(--soft-cur);color:var(--soft-cur-fg);font-size:var(--shine-text-xs);font-weight:700}
    .nucleus-table .status.needs_you,.nucleus-table .status.at_risk{background:var(--soft-warn);color:var(--warn)}
    .nucleus-table .resize{position:absolute;right:0;top:0;bottom:0;width:var(--shine-space-2);cursor:col-resize}
    .nucleus-pager{display:flex;align-items:center;gap:var(--shine-space-2);padding:var(--shine-space-3);color:var(--dim);
      font-size:var(--shine-text-xs)}
    .nucleus-pager button,.nucleus-pager select{background:var(--panel);border:1px solid var(--line);color:var(--text);
      border-radius:var(--shine-radius-sm);padding:var(--shine-space-2);min-height:var(--tap);font-size:var(--shine-text-xs)}
    .nucleus-pager button:disabled{opacity:.45}
    .nucleus-shell>h2.sr-only{font-size:var(--shine-text-sm)}
    .nucleus-pager .range{margin-right:auto;font-variant-numeric:tabular-nums}
    .nucleus-detail{border:1px solid var(--line);border-radius:var(--shine-radius-md);background:var(--panel);
      display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:var(--shine-space-4);padding:var(--shine-space-4)}
    .nucleus-detail header{grid-column:1/-1;display:flex;gap:var(--shine-space-3);align-items:flex-start;flex-wrap:wrap}
    .nucleus-detail header .copy{flex:1;min-width:14rem}
    .nucleus-detail h2{margin:0;font-size:var(--shine-text-base)}
    .nucleus-detail p{margin:var(--shine-space-1) 0 0;color:var(--dim);font-size:var(--shine-text-xs)}
    .nucleus-detail h3{font-size:var(--shine-text-xs);text-transform:uppercase;letter-spacing:var(--shine-tracking-caps);color:var(--dim)}
    .nucleus-detail ul{list-style:none;padding:0;margin:0;display:grid;gap:var(--shine-space-2)}
    .nucleus-detail li{padding:var(--shine-space-2);border:1px solid var(--line);border-radius:var(--shine-radius-sm);font-size:var(--shine-text-xs)}
    .nucleus-detail .native-id{font-family:var(--shine-font-mono);color:var(--blue)}
    .skel{color:var(--dim2);font-size:var(--shine-text-sm);padding:12px 8px;animation:skel 1.2s ease-in-out infinite}
    @keyframes skel{0%,100%{opacity:.45}50%{opacity:1}}
    @media(prefers-reduced-motion:reduce){
      *{animation-duration:.01ms !important;animation-iteration-count:1 !important;
        transition-duration:.01ms !important;scroll-behavior:auto !important}
      .skel{opacity:.7}
    }
    .inline-err{color:var(--soft-bad-fg);background:var(--soft-bad);border:1px solid var(--soft-bad-line);border-radius:var(--shine-radius-md);
      padding:12px 12px;margin:8px 0;font-size:var(--shine-text-sm);display:none}
    .flag.live{background:var(--soft-ok);color:var(--ok)}
    .flag.kept{background:var(--soft-info);color:var(--soft-info-fg)}
    .flag.cur{background:var(--soft-cur);color:var(--soft-cur-fg)}
    .flag.cla{background:var(--soft-warn);color:var(--warn)}

    /* projects */
    .p{display:flex;align-items:center;gap:12px;padding:8px;border-bottom:4px solid var(--line);border-radius:var(--shine-radius-sm)}
    .p:hover{background:var(--hover)}
    .p .b{flex:0 0 auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
    .p button{background:var(--panel);border:1px solid var(--line);color:var(--text);
      border-radius:var(--shine-radius-sm);padding:4px 8px;min-height:var(--tap);font-size:var(--shine-text-xs);
      font-weight:600;cursor:pointer}
    .p button:hover{border-color:var(--blue)}
    .p a{display:inline-flex;align-items:center;min-height:var(--tap)}
    .p .nm{flex:0 1 210px;min-width:110px;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .p .br{flex:0 0 auto;font-family:var(--shine-font-mono);font-size:var(--shine-text-xs);color:var(--dim2);
      background:var(--panel);border:1px solid var(--line);border-radius:var(--shine-radius-sm);padding:4px 8px;
      max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .p .cm{flex:1;min-width:0;color:var(--dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:var(--shine-text-sm)}
    .p .a{flex:0 0 62px;text-align:right;color:var(--dim2);font-size:var(--shine-text-xs)}
    .flag{font-size:var(--shine-text-xs);font-weight:700;border-radius:var(--shine-radius-sm);padding:4px 8px}
    .flag.dirty{background:var(--soft-warn);color:var(--warn)}
    .flag.push{background:var(--soft-bad);color:var(--soft-bad-fg)}

    /* notes */
    .cap{display:flex;gap:8px;margin:4px 0 16px}
    .cap input{flex:1;background:var(--panel);border:1px solid var(--line);color:var(--text);
      border-radius:var(--shine-radius-md);padding:12px 12px;font-size:var(--shine-text-sm)}
    .cap input:focus{outline:none}
    .cap input:focus-visible{border-color:var(--brand)}
    .cap button{background:var(--brand);border:0;color:var(--on-brand);border-radius:var(--shine-radius-md);padding:12px 16px;
      min-height:var(--tap);font-weight:700;cursor:pointer}
    .cap button.owner-auth{background:var(--panel);border:1px solid var(--line);color:var(--text)}
    .cap button.owner-auth:hover{border-color:var(--brand)}
    /* the board scrolls sideways, so say so and make the edge visible */
    .kanban-wrap{position:relative}
    .kanban{display:flex;gap:8px;align-items:flex-start;overflow-x:auto;padding-bottom:8px;
      scrollbar-color:var(--line) transparent}
    .kanban-hint{color:var(--dim);font-size:var(--shine-text-xs);padding:0 4px 8px;display:none}
    .kanban-wrap.clipped .kanban-hint{display:block}
    .kanban-wrap.clipped::after{content:"";position:absolute;top:0;right:0;bottom:8px;width:var(--shine-space-5);
      pointer-events:none;background:linear-gradient(90deg,transparent,var(--bg))}
    .lane{flex:0 0 260px;background:var(--panel);border:1px solid var(--line);border-radius:var(--shine-radius-md);min-height:120px}
    .lane-h{padding:8px 12px;font-size:var(--shine-text-xs);font-weight:700;letter-spacing:var(--shine-tracking-caps);text-transform:uppercase;
      color:var(--dim);border-bottom:4px solid var(--line);display:flex;justify-content:space-between}
    .lane-h .n{color:var(--dim2)}
    .lane-rows{padding:8px}
    .todo{display:flex;flex-direction:column;gap:8px;padding:8px;border:1px solid var(--line);border-radius:var(--shine-radius-md);background:var(--bg);margin-bottom:8px}
    .todo:hover{background:var(--hover);border-color:var(--blue)}
    .todo .tx{font-size:var(--shine-text-sm);line-height:var(--shine-leading-snug);overflow-wrap:anywhere}
    .todo .tags{display:flex;gap:4px;flex-wrap:wrap}
    .todo .tag{font-size:var(--shine-text-xs);font-weight:600;background:var(--soft-info);color:var(--soft-info-fg);border-radius:var(--shine-radius-sm);padding:4px 8px}
    .todo .b{display:flex;gap:4px;flex-wrap:wrap;align-items:center}
    .todo button{background:var(--panel);border:1px solid var(--line);color:var(--text);
      border-radius:var(--shine-radius-sm);padding:4px 8px;min-height:var(--tap);font-size:var(--shine-text-xs);font-weight:600;cursor:pointer}
    .todo button:hover{border-color:var(--blue)}
    .todo .pt{font-size:var(--shine-text-xs);color:var(--ok);font-weight:700}
    /* Six peer buttons per card became one primary + a disclosure. Move-lane and
       act-on-note are different jobs and no longer look identical. */
    .todo button.prim{background:var(--soft-go);border-color:var(--soft-go);color:var(--on-solid)}
    .todo button.prim:hover{border-color:var(--ok)}
    .todo button.more{color:var(--dim)}
    .tmenu{display:flex;flex-direction:column;gap:4px;margin-top:4px;padding:8px;
      background:var(--panel);border:1px solid var(--line);border-radius:var(--shine-radius-md)}
    .tmenu .lbl{font-size:var(--shine-text-xs);font-weight:700;letter-spacing:var(--shine-tracking-caps);text-transform:uppercase;color:var(--dim)}
    .tmenu .grp2{display:flex;gap:4px;flex-wrap:wrap}
    .tmenu button.del{color:var(--bad);border-color:var(--soft-bad-line)}
    .tmenu button.del:hover{border-color:var(--bad)}
    .notes-stack{display:grid;gap:var(--shine-space-5)}
    .notes-lane{border:1px solid var(--line);border-radius:var(--shine-radius-md);background:var(--panel);overflow:hidden}
    .notes-lane>header{display:flex;justify-content:space-between;align-items:center;padding:var(--shine-space-3) var(--shine-space-4);
      border-bottom:1px solid var(--line);font-size:var(--shine-text-xs);font-weight:750;letter-spacing:var(--shine-tracking-caps);
      text-transform:uppercase;color:var(--dim)}
    .notes-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(18rem,1fr));gap:var(--shine-space-3);padding:var(--shine-space-4)}

    /* Conversation always opens as a real workspace, never a widget. */
    .chat .sec{display:flex;align-items:center}
    .chat .sec .grow{flex:1}
    .chat .sec button{background:transparent;border:1px solid var(--line);color:var(--dim);
      border-radius:var(--shine-radius-sm);padding:4px 8px;font-size:var(--shine-text-xs);font-weight:700;cursor:pointer;
      text-transform:none;letter-spacing:var(--shine-tracking-base)}
    .chat .sec button:hover{color:var(--text)}
    .bot-sum{display:none;align-items:center;gap:8px;padding:4px 16px 8px;font-size:var(--shine-text-xs);color:var(--dim)}
    @media(min-width:901px){
      .rail-chat-wrap{display:none}
      body.chatbig #chatdock{position:fixed;inset:0;z-index:70;background:var(--bg);border:0;
        padding:var(--shine-space-8) max(var(--shine-space-8),calc((100vw - 72rem)/2));display:flex}
      body.chatbig #chatdock .sec{font-size:var(--shine-text-base);padding:0 0 var(--shine-space-4)}
      body.chatbig #msgs{padding:var(--shine-space-4) 0;gap:var(--shine-space-3)}
      body.chatbig .m{font-size:var(--shine-text-base);max-width:76%;line-height:var(--shine-leading-normal);padding:var(--shine-space-4)}
      body.chatbig .chatin{padding:var(--shine-space-4) 0 0;border-top:1px solid var(--line)}
      body.chatbig .chatin input{font-size:var(--shine-text-base);padding:var(--shine-space-4)}
      body.chatbig .chatin button{font-size:var(--shine-text-sm);padding:var(--shine-space-3) var(--shine-space-5)}
    }
    /* mobile shell */
    .mob-tabs{display:none;position:fixed;bottom:0;left:0;right:0;z-index:40;
      background:var(--panel);border-top:4px solid var(--line);padding:4px 0 env(safe-area-inset-bottom,0)}
    .rail-chat-wrap{display:none}
    .mob-tab{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;
      padding:8px 4px;min-height:var(--tap);background:transparent;border:0;color:var(--dim);
      font-size:var(--shine-text-xs);font-weight:600;cursor:pointer}
    .mob-tab.on{color:var(--brand)}
    .mob-scrim{display:none;position:fixed;inset:0;background:color-mix(in srgb, var(--shine-color-stone-950) 55%, transparent);z-index:44}
    .mob-scrim.open{display:block}
    .mob-sheet{display:none;position:fixed;left:0;right:0;bottom:56px;z-index:45;
      background:var(--panel);border-top:4px solid var(--line);border-radius:var(--shine-radius-lg) var(--shine-radius-lg) 0 0;
      max-height:calc(85vh - 56px);overflow-y:auto;padding:var(--shine-space-3) var(--shine-space-4) var(--shine-space-5)}
    .mob-sheet.open{display:flex;flex-direction:column}
    #chat-sheet.open{overflow:hidden;padding-bottom:0}
    #chat-sheet-slot{flex:1;min-height:240px;display:flex;flex-direction:column}
    #chat-sheet .chat{flex:1;min-height:240px;border-top:0;height:100%}
    .mob-sheet-h{display:flex;align-items:center;margin-bottom:12px}
    .mob-sheet-h h2{flex:1;font-size:var(--shine-text-base);margin:0}
    .mob-sheet-h button{background:transparent;border:1px solid var(--line);color:var(--text);
      border-radius:var(--shine-radius-sm);padding:4px 12px;min-height:var(--tap);font-size:var(--shine-text-xs);cursor:pointer}
    .mob-more-nav{display:flex;flex-direction:column;gap:4px;margin:8px 0}
    .mob-more-nav .nav-item{display:flex;align-items:center;width:100%;padding:8px 12px;
      min-height:var(--tap);border-left:4px solid transparent;
      background:transparent;border-top:0;border-right:0;border-bottom:0;color:var(--dim);
      font-size:var(--shine-text-sm);font-weight:600;text-align:left;cursor:pointer}
    .mob-more-nav .nav-item.on{color:var(--text);border-left-color:var(--brand);background:var(--hover)}
    .mob-more-nav .nav-item .n{margin-left:auto;font-size:var(--shine-text-xs);color:var(--dim2)}
    #confirm-overlay{display:none;position:fixed;inset:0;z-index:60;background:color-mix(in srgb, var(--shine-color-stone-950) 65%, transparent);
      align-items:center;justify-content:center;padding:16px}
    #confirm-overlay.open{display:flex}
    .confirm-box{background:var(--panel);border:1px solid var(--line);border-radius:var(--shine-radius-lg);
      padding:16px 16px;max-width:420px;width:100%;box-shadow:var(--shine-shadow-lg)}
    .confirm-box h3{margin:0 0 8px;font-size:var(--shine-text-base)}
    .confirm-box p{margin:0 0 16px;color:var(--dim);font-size:var(--shine-text-sm);line-height:var(--shine-leading-normal)}
    .confirm-field{display:none;margin-bottom:16px}
    .confirm-field.open{display:block}
    .confirm-field label{display:block;margin-bottom:8px;color:var(--text);font-size:var(--shine-text-sm);font-weight:600}
    .confirm-input-row{display:flex;align-items:stretch;gap:8px}
    .confirm-box input{width:100%;background:var(--bg);border:1px solid var(--line);color:var(--text);
      border-radius:var(--shine-radius-md);padding:8px 12px;font-size:var(--shine-text-sm);margin:0}
    .confirm-box input:focus{outline:none}
    .confirm-box input:focus-visible{border-color:var(--brand)}
    .confirm-box input[aria-invalid="true"]{border-color:var(--bad)}
    .confirm-input-row button{flex:0 0 auto;background:var(--panel);border:1px solid var(--line);color:var(--text);
      border-radius:var(--shine-radius-md);padding:8px 12px;min-height:var(--tap);font-size:var(--shine-text-xs);font-weight:600;cursor:pointer}
    .confirm-input-row button:hover{border-color:var(--brand)}
    .confirm-error{display:none;margin-top:8px;color:var(--soft-bad-fg);font-size:var(--shine-text-xs);line-height:var(--shine-leading-normal)}
    .confirm-error.on{display:block}
    .confirm-actions{display:flex;gap:8px;justify-content:flex-end}
    .confirm-actions button{border-radius:var(--shine-radius-md);padding:8px 16px;min-height:var(--tap);
      font-size:var(--shine-text-sm);font-weight:600;cursor:pointer}
    .confirm-actions .cancel{background:var(--panel);border:1px solid var(--line);color:var(--text)}
    .confirm-actions .ok{background:var(--brand);border:0;color:var(--on-brand)}
    .confirm-actions .ok.danger{background:var(--bad);color:var(--on-solid)}
    /* scroll lock while a modal owns the screen */
    body.modal-open{overflow:hidden}
    #toast{position:fixed;left:50%;transform:translateX(-50%);bottom:24px;background:var(--hover);
      border:1px solid var(--line);border-radius:var(--shine-radius-md);padding:8px 16px;font-size:var(--shine-text-sm);
      display:none;align-items:center;gap:12px;max-width:60vw;
      box-shadow:var(--shine-shadow-md);z-index:50}
    #toast.bad{border-color:var(--soft-bad-line);color:var(--soft-bad-fg);background:var(--soft-bad)}
    #toast .toast-msg{flex:1;min-width:0}
    #toast .toast-dismiss{background:transparent;border:1px solid var(--line);color:inherit;
      border-radius:var(--shine-radius-sm);padding:4px 8px;min-height:var(--tap);font-size:var(--shine-text-xs);font-weight:600;cursor:pointer}
    #toast .toast-dismiss:hover{border-color:var(--brand)}
    #alarm .alarm-act{margin-left:8px;color:var(--soft-bad-fg)}

    /* ============ mobile: the ONLY max-width:900px block ============
       It must stay last. Anything above it that also targets .rail / .mob-tabs
       will win on source order and silently kill the phone layout. */
    @media(max-width:900px){
      :root{--gut:0px}
      .app{grid-template-columns:1fr;padding-bottom:56px}
      .rail{display:none}
      .rail-chat-wrap{flex:1;display:flex;flex-direction:column;min-height:0}
      .main{padding:var(--shine-space-4) var(--shine-space-4) var(--shine-space-8)}
      .mob-tabs{display:flex}
      #toast{bottom:var(--shine-space-8);max-width:90vw}
      /* stack rows so the title is never squeezed to a stub by the id + age */
      .r{flex-wrap:wrap;gap:4px 8px;padding:8px 4px}
      .r .id{flex:0 0 auto}
      .r .t{flex:1 1 100%;order:3;white-space:normal;overflow:visible;overflow-wrap:anywhere}
      .r .a{flex:0 0 auto;margin-left:auto}
      .r .b{flex:1 1 100%;order:4}
      .grp .why,.grp .act{padding-left:12px}
      .q{border-radius:var(--shine-radius-sm);border-left-width:4px}
      .ans input{min-width:0}
      .lane{flex:0 0 84vw}
      .nucleus-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}
      .nucleus-detail{grid-template-columns:1fr}
      .nucleus-detail header{grid-column:1}
      .kanban-wrap.clipped::after{display:none}
    }
  </style>
</head>
<body>
<div class="app">
  <main class="main" aria-label="Work surface">
    <div id="head">Loading…</div>
    <div id="sub"></div>
    <div id="alarm" role="alert"></div>
    <div id="page"></div>
  </main>

  <aside class="rail" aria-label="Command center navigation">
    <h1><span>Brutus</span></h1>
    <p class="tag">the work that needs you</p>
    <button type="button" class="command-action" onclick="openCapture()">Capture work</button>
    <button type="button" class="command-action secondary" onclick="openCommandChat()">Ask Brutus</button>
    <div class="theme-wrap">
      <button type="button" id="theme-toggle" class="icon" aria-pressed="false" title="Toggle light/dark theme"
        aria-label="Toggle light or dark theme"><span class="label">Dark</span></button>
    </div>

    <nav class="nav-block" aria-label="Pages">
    <div class="sec">Pages</div>
    <div class="nav">
      <button type="button" class="nav-item" id="nav-nucleus" onclick="go('nucleus')">Nucleus <span class="n" id="n-nucleus"></span></button>
      <button type="button" class="nav-item" id="nav-work" onclick="go('work')">Work <span class="n" id="n-work"></span></button>
      <button type="button" class="nav-item" id="nav-canon" onclick="go('canon')">Inbox <span class="n" id="n-canon"></span></button>
      <button type="button" class="nav-item" id="nav-agents" onclick="go('agents')">Agents <span class="n" id="n-agents"></span></button>
      <button type="button" class="nav-item" id="nav-studio" onclick="go('studio')">Studio</button>
      <button type="button" class="nav-item" id="nav-projects" onclick="go('projects')">Projects <span class="n" id="n-projects"></span></button>
      <button type="button" class="nav-item" id="nav-notes" onclick="go('notes')">Notes <span class="n" id="n-notes"></span></button>
      <details class="nav-more"><summary>Tools</summary><div class="tool-menu">
        <button type="button" class="nav-item" id="nav-chatbots" onclick="go('chatbots')">Sites <span class="n" id="n-chatbots"></span></button>
        <button type="button" class="nav-item" id="nav-avatar" onclick="go('avatar')">Avatar</button>
        <button type="button" class="nav-item" id="nav-demomaker" onclick="go('demomaker')">Demos</button>
      </div></details>
    </div>
    </nav>

    <section class="bots-block" aria-label="Bots">
    <div class="sec">Bots</div>
    <div class="bots" id="bots"></div>
    </section>

    <div class="rail-chat-wrap" id="rail-chat-slot"></div>
  </aside>
</div>

<section id="chatdock" class="chat" aria-label="Chat with Brutus">
  <div class="sec"><span>Chat with Brutus</span><span class="grow"></span>
    <button type="button" onclick="bigChat()" id="bigbtn">Close</button></div>
  <div id="msgs"></div>
  <div class="chatin">
    <button type="button" class="icon" id="livebtn" title="Live talk — just speak, no record/send"
            onclick="toggleLive()" disabled>Live</button>
    <button type="button" class="icon" id="speakbtn" title="Read replies out loud"
            onclick="toggleSpeak()" disabled>Speak</button>
    <label for="chatbox" class="sr-only">Message Brutus</label>
    <input id="chatbox" placeholder="Talk to Brutus…" aria-label="Talk to Brutus"
           onkeydown="if(event.key==='Enter')sendChat()" />
    <button type="button" onclick="sendChat()">Send</button>
  </div>
</section>

<nav id="mob-tabs" class="mob-tabs" aria-label="Main navigation">
  <button type="button" class="mob-tab" data-page="nucleus" onclick="goMob('nucleus')">Nucleus</button>
  <button type="button" class="mob-tab" data-page="agents" onclick="goMob('agents')">Agents</button>
  <button type="button" class="mob-tab" data-page="notes" onclick="goMob('notes')">Notes</button>
  <button type="button" class="mob-tab" data-page="chat" onclick="openMobSheet('chat')">Chat</button>
  <button type="button" class="mob-tab" data-page="more" onclick="openMobSheet('more')">More</button>
</nav>
<div id="mob-scrim" class="mob-scrim" onclick="closeMobSheets()"></div>
<div id="chat-sheet" class="mob-sheet" role="dialog" aria-label="Chat" aria-modal="true">
  <div class="mob-sheet-h"><h2>Chat with Brutus</h2>
    <button type="button" onclick="closeMobSheets()">Close</button></div>
  <div id="chat-sheet-slot"></div>
</div>
<div id="more-sheet" class="mob-sheet" role="dialog" aria-label="More pages" aria-modal="true">
  <div class="mob-sheet-h"><h2>More</h2>
    <button type="button" onclick="closeMobSheets()">Close</button></div>
  <div class="mob-more-nav">
    <button type="button" class="nav-item" id="mob-nav-work" onclick="goFromMore('work')">Work <span class="n" id="mob-n-work"></span></button>
    <button type="button" class="nav-item" id="mob-nav-canon" onclick="goFromMore('canon')">Inbox <span class="n" id="mob-n-canon"></span></button>
    <button type="button" class="nav-item" id="mob-nav-chatbots" onclick="goFromMore('chatbots')">Chatbots <span class="n" id="mob-n-chatbots"></span></button>
    <button type="button" class="nav-item" id="mob-nav-avatar" onclick="goFromMore('avatar')">Avatar</button>
    <button type="button" class="nav-item" id="mob-nav-demomaker" onclick="goFromMore('demomaker')">Demo Maker</button>
    <button type="button" class="nav-item" id="mob-nav-studio" onclick="goFromMore('studio')">Studio runs</button>
    <button type="button" class="nav-item" id="mob-nav-projects" onclick="goFromMore('projects')">Projects <span class="n" id="mob-n-projects"></span></button>
  </div>
  <div class="sec">Bots</div>
  <div class="bots" id="mob-bots"></div>
</div>

<div id="confirm-overlay" role="dialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-body">
  <div class="confirm-box">
    <h3 id="confirm-title"></h3>
    <p id="confirm-body"></p>
    <div id="confirm-field" class="confirm-field">
      <label id="confirm-input-label" for="confirm-input">Value</label>
      <div class="confirm-input-row">
        <input type="text" id="confirm-input" autocomplete="off" autocapitalize="none" spellcheck="false"
          aria-describedby="confirm-body confirm-error" />
        <button type="button" id="confirm-toggle" aria-controls="confirm-input" hidden>Show token</button>
      </div>
      <div id="confirm-error" class="confirm-error" role="alert"></div>
    </div>
    <div class="confirm-actions">
      <button type="button" class="cancel" id="confirm-cancel">Cancel</button>
      <button type="button" class="ok" id="confirm-ok">Confirm</button>
    </div>
  </div>
</div>
<div id="toast" role="status" aria-live="polite" tabindex="0"><span class="toast-msg"></span><button type="button" class="toast-dismiss" aria-label="Dismiss notification">Dismiss</button></div>

<!-- Phase D tests: 375px keyboard nav, poll input survival, confirm flows -->

<script>
const el = i => document.getElementById(i);
const esc = s => String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let C=[{k:'sys',t:'Ask about tickets, status, anything on the board.'}];
let operatorSession=localStorage.getItem('brutus.operator.session')||'';
const PAGES=['nucleus','work','canon','chatbots','avatar','demomaker','agents','projects','notes','studio'];
// Page lives in the URL hash so Back works and a page can be linked/bookmarked.
// localStorage is only the fallback for a bare "/".
function pageFromHash(){let h=location.hash||'';
  if(h.charAt(0)==='#')h=h.slice(1);
  if(h.charAt(0)==='/')h=h.slice(1);
  return PAGES.indexOf(h)>=0?h:'';}
let B={}, N={summary:{},projects:[],source_status:{}}, P=[], T=[], L=[], S=[], AG=[], AGcounts={}, Capped=[],
    Canon={inbox:[],today:[],review:[],execution_cards:[]},
    Brief=null, briefOpen=false, actionOpen=new Set(),
    page=pageFromHash()||'nucleus';
if(PAGES.indexOf(page)<0)page='nucleus';
let AGfilt={surface:'',q:'',showHidden:false};
let NF={q:'',status:'',source:'',page:1,size:20,sort:'attention_score',dir:'desc',selected:new Set(),cols:{tickets:true,threads:true,risk:true}},NSEL='';
let probes=false, busy=false, open=new Set(), chatBusy=false;
let alarmChatKey='';
let mobSheetOpen=null;
let confirmPending=null, tagEditId=null, confirmKeepOpen=false;
// Every data view needs all four: loading, empty, filtered-empty, error. The
// pages added after this pattern landed (sites/avatar/demomaker)
// were mapping a failed fetch onto an empty list, so "cannot reach Brutus"
// rendered as "you have no chatbots".
let loadSt={nucleus:{s:'loading',err:''},board:{s:'loading',err:''},projects:{s:'loading',err:''},todos:{s:'loading',err:''},
  agents:{s:'loading',err:''},lessons:{s:'loading',err:''},
  sites:{s:'loading',err:''},avatar:{s:'loading',err:''},canon:{s:'loading',err:''}};
const PAGE_SIZE=25;   // lists are paged, not dumped
function listDef(){return{q:'',sort:'age',dir:'desc',shown:PAGE_SIZE};}
function loadList(key){try{const s=Object.assign(listDef(),JSON.parse(localStorage.getItem('brutus.list.'+key)||'{}'));
    s.shown=PAGE_SIZE;return s;}
  catch(e){return listDef();}}
const LIST={nucleus:loadList('nucleus'),work:loadList('work'),projects:loadList('projects'),
  agents:loadList('agents')};
function saveList(key){const s=LIST[key];
  localStorage.setItem('brutus.list.'+key,JSON.stringify({q:s.q,sort:s.sort,dir:s.dir}));}
// Per-section "shown" counters, separate from the toolbar state.
const SHOWN={};
function showMore(sk){SHOWN[sk]=(SHOWN[sk]||PAGE_SIZE)+PAGE_SIZE;render();}
window.showMore=showMore;
function resetShown(prefix){Object.keys(SHOWN).forEach(k=>{
  if(!prefix||k.indexOf(prefix)===0)delete SHOWN[k];});}
// "Showing 25 of 50" beats silently rendering all 50 or silently hiding 25.
function pageSlice(sk,rows,label){
  const n=Math.min(SHOWN[sk]||PAGE_SIZE,rows.length);
  const bar=rows.length>n
    ? `<div class="more-bar"><span>Showing ${n} of ${rows.length} ${esc(label||'rows')}</span>
       <button type="button" onclick="showMore('${sk}')">Show ${Math.min(PAGE_SIZE,rows.length-n)} more</button></div>`
    : (rows.length>PAGE_SIZE?`<div class="more-bar"><span>All ${rows.length} ${esc(label||'rows')} shown</span></div>`:'');
  return {rows:rows.slice(0,n),bar};}

function armToastHide(t){
  clearTimeout(t._h);
  t._h=setTimeout(()=>{if(!t._paused)t.style.display='none';},5000);
}
function toast(m,bad){
  const t=el('toast');if(!t)return;
  t.className=bad?'bad':'';
  const msg=t.querySelector('.toast-msg');
  if(msg)msg.textContent=m; else t.textContent=m;
  t.style.display='flex';
  t._paused=false;
  armToastHide(t);
}
(function(){const t=el('toast');if(!t)return;
  const pause=()=>{t._paused=true;clearTimeout(t._h);};
  const resume=()=>{t._paused=false;armToastHide(t);};
  t.addEventListener('mouseenter',pause);
  t.addEventListener('mouseleave',resume);
  t.addEventListener('focusin',pause);
  t.addEventListener('focusout',resume);
  t.querySelector('.toast-dismiss')?.addEventListener('click',()=>{
    clearTimeout(t._h);t.style.display='none';t._paused=false;
  });
})();
function ownerHeaders(){const t=sessionStorage.getItem('brutus.ownerCsrf')||'';
  return t?{'X-Brutus-CSRF':t}:{};}
function confirmError(msg){const n=el('confirm-error'),inp=el('confirm-input');
  n.textContent=msg||'';n.classList.toggle('on',Boolean(msg));inp.setAttribute('aria-invalid',msg?'true':'false');}
window.authenticateCanon=()=>{const inp=el('confirm-input'),field=el('confirm-field'),toggle=el('confirm-toggle');
  inp.type='password';inp.value='';inp.placeholder='Paste owner token';field.classList.add('open');
  el('confirm-input-label').textContent='Owner token';toggle.hidden=false;toggle.textContent='Show token';
  confirmError('');clearInlineError('canon-err');el('confirm-title').textContent='Unlock owner actions';
  el('confirm-body').textContent='In a terminal, run `brutus owner-token`, then paste the local token. This browser stays unlocked for up to 8 hours.';
  const ok=el('confirm-ok');ok.textContent='Unlock';ok.className='ok';confirmKeepOpen=true;
  confirmPending=async()=>{const t=inp.value.trim();
    if(!t){confirmError('Paste the owner token to continue.');inp.focus();return;}
    confirmError('');await withBusy(ok,async()=>{try{
      const r=await fetch('/api/auth/session',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({token:t})});let d={};try{d=await r.json()}catch(e){}
      if(!r.ok){confirmError(d.detail||('HTTP '+r.status));inp.focus();return;}
      sessionStorage.setItem('brutus.ownerCsrf',d.csrf);closeConfirm();toast('Owner actions unlocked for 8 hours.');
    }catch(e){confirmError('Could not unlock owner actions. Try again.');inp.focus();}},'Unlocking…');};
  const ov=el('confirm-overlay');ov.style.display='flex';ov.classList.add('open');openModal(ov,inp);};
async function post(u,b,method){const r=await fetch(u,{method:method||'POST',
  headers:{'Content-Type':'application/json',...ownerHeaders()},body:JSON.stringify(b||{})});
  let d={};try{d=await r.json()}catch(e){}
  if(!r.ok)throw new Error(d.detail||('HTTP '+r.status));return d;}
const linkFor = r => r.link?`<a href="${esc(r.link)}" target="_blank" rel="noopener">Linear</a>`:'';

function showInlineError(id,msg){const n=el(id);if(!n)return;n.textContent=msg;n.style.display='block';}
function clearInlineError(id){const n=el(id);if(!n)return;n.textContent='';n.style.display='none';}
function triadWrap(st,retryFn,inner){
  if(st.s==='loading')return '<div class="skel">Loading…</div>';
  if(st.s==='error')return `<div class="inline-err" style="display:block">${esc(st.err||'Could not load.')}</div>
    <button type="button" onclick="${retryFn}()">Retry</button>`;
  return typeof inner==='function'?inner():inner;}
async function withBusy(btn,fn,label){
  const orig=btn?btn.textContent:'';
  if(btn){btn.disabled=true;btn.setAttribute('aria-busy','true');if(label)btn.textContent=label;}
  try{return await fn();}finally{if(btn){btn.disabled=false;btn.removeAttribute('aria-busy');btn.textContent=orig;}}}
/* ---------------- modal plumbing ----------------
   Tab used to walk straight out of the confirm dialog into the ~80 controls on
   the board behind the scrim, the background scrolled, and closing left focus
   on a display:none button. One shared open/close now handles focus trap,
   focus restore, an inert background and a scroll lock for every overlay. */
const FOCUSABLE='button:not([disabled]),input:not([disabled]),select:not([disabled]),'+
  'textarea:not([disabled]),a[href],[tabindex]:not([tabindex="-1"])';
const BG_LAYERS=['.app','#mob-tabs','#chat-sheet','#more-sheet','#confirm-overlay'];
let modalStack=[];
function focusables(root){
  return Array.prototype.slice.call(root.querySelectorAll(FOCUSABLE))
    .filter(n=>n.offsetParent!==null||n===document.activeElement);}
function onTrapKey(e){
  const top=modalStack[modalStack.length-1];
  if(e.key!=='Tab'||!top)return;
  const f=focusables(top.node);
  if(!f.length){e.preventDefault();return;}
  const first=f[0],last=f[f.length-1];
  if(!top.node.contains(document.activeElement)){e.preventDefault();first.focus();return;}
  if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
  else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}
function setBackgroundInert(activeNode){
  BG_LAYERS.forEach(sel=>{
    const n=document.querySelector(sel);
    if(!n||n===activeNode)return;
    if(activeNode&&n.contains(activeNode))return;  // never inert an ancestor
    n.setAttribute('aria-hidden','true');
    try{n.inert=true;}catch(e){}
  });}
function clearBackgroundInert(){
  BG_LAYERS.forEach(sel=>{const n=document.querySelector(sel);if(!n)return;
    n.removeAttribute('aria-hidden');try{n.inert=false;}catch(e){}});}
function openModal(node,initial){
  if(modalStack.some(m=>m.node===node)){const t=initial||focusables(node)[0];if(t)t.focus();return;}
  modalStack.push({node,restore:document.activeElement});
  document.body.classList.add('modal-open');
  clearBackgroundInert();setBackgroundInert(node);
  if(modalStack.length===1)document.addEventListener('keydown',onTrapKey,true);
  const t=initial||focusables(node)[0];
  if(t)t.focus();}
function closeModal(node){
  const i=modalStack.map(m=>m.node).lastIndexOf(node);
  if(i<0)return;
  const entry=modalStack.splice(i,1)[0];
  clearBackgroundInert();
  if(modalStack.length){setBackgroundInert(modalStack[modalStack.length-1].node);}
  else{document.removeEventListener('keydown',onTrapKey,true);
    document.body.classList.remove('modal-open');}
  const r=entry.restore;
  if(r&&r.isConnected&&r.offsetParent!==null){try{r.focus();}catch(e){}}
  else if(!modalStack.length){const h=el('head');if(h){h.setAttribute('tabindex','-1');h.focus();}}}

function closeConfirm(){const ov=el('confirm-overlay');ov.classList.remove('open');ov.style.display='none';
  confirmPending=null;tagEditId=null;confirmKeepOpen=false;el('confirm-field').classList.remove('open');
  const inp=el('confirm-input');inp.type='text';inp.value='';inp.placeholder='';inp.removeAttribute('aria-invalid');
  el('confirm-toggle').hidden=true;el('confirm-error').textContent='';el('confirm-error').classList.remove('on');
  closeModal(ov);}
function confirmAction({title,body,confirmLabel,danger,onConfirm}){
  el('confirm-title').textContent=title;el('confirm-body').textContent=body;
  const ok=el('confirm-ok');ok.textContent=confirmLabel||'Confirm';
  ok.className='ok'+(danger?' danger':'');el('confirm-field').classList.remove('open');
  confirmPending=onConfirm;tagEditId=null;
  const ov=el('confirm-overlay');ov.style.display='flex';ov.classList.add('open');
  openModal(ov,el('confirm-cancel'));}
function promptTags(id,current){
  tagEditId=id;confirmPending=async()=>{
    const tags=(el('confirm-input').value||'').trim();
    closeConfirm();await doEditTags(id,tags);};
  el('confirm-title').textContent='Edit tags';
  el('confirm-body').textContent='Comma-separated tags for this note.';
  const inp=el('confirm-input');el('confirm-field').classList.add('open');inp.type='text';inp.value=current||'';
  inp.placeholder='Add comma-separated tags';el('confirm-input-label').textContent='Tags';el('confirm-toggle').hidden=true;
  el('confirm-ok').textContent='Save';el('confirm-ok').className='ok';
  const ov=el('confirm-overlay');ov.style.display='flex';ov.classList.add('open');
  openModal(ov,inp);}

function filterRows(rows,q,fields){
  if(!q)return rows;const lq=q.toLowerCase();
  return rows.filter(r=>fields.some(f=>String(r[f]||'').toLowerCase().includes(lq)));}
/* Ages arrive as BOTH a phrase ("17h") and a number (age_minutes). Sorting the
   phrase string-wise ordered 11d, 17h, 2h, 3w, 5d — so always sort the number
   and fall back to parsing the phrase for rows that predate the numeric field. */
const AGE_UNIT={m:1,h:60,d:1440,w:10080};
function ageMins(r){
  if(r.age_minutes!==null&&r.age_minutes!==undefined&&r.age_minutes!=='')return Number(r.age_minutes);
  if(r.last_commit_epoch)return (Date.now()/1000-Number(r.last_commit_epoch))/60;
  const p=String(r.age||'').trim();
  const num=parseFloat(p);
  if(!isNaN(num)){const u=p.replace(/[0-9.\\s]/g,'').charAt(0).toLowerCase();
    if(AGE_UNIT[u])return num*AGE_UNIT[u];}
  return null;}
function sortRows(rows,sort,keys,dir){
  const k=keys||{age:'age',ticket:'ticket',title:'title'};
  const f=k[sort]||k.age;
  const byAge=(sort==='age')||f==='age'||f==='age_minutes'||f==='last_commit_epoch';
  const copy=[...rows];
  copy.sort((a,b)=>{
    if(byAge){
      const av=ageMins(a),bv=ageMins(b);
      if(av===null&&bv===null)return String(a.ticket||a.name||'').localeCompare(String(b.ticket||b.name||''));
      if(av===null)return 1;
      if(bv===null)return -1;
      if(av!==bv)return av-bv;   // ascending = youngest first
      return String(a.ticket||a.name||'').localeCompare(String(b.ticket||b.name||''));
    }
    return String(a[f]||'').localeCompare(String(b[f]||''),undefined,{numeric:true,sensitivity:'base'});
  });
  return (dir==='desc')?copy.reverse():copy;}
/* The control has to say which way it is pointing, or it is not a sort. */
const DIR_LABEL={age:{desc:'Oldest first',asc:'Newest first'},
  other:{asc:'A → Z',desc:'Z → A'}};
function dirLabel(sort,dir){const m=(sort==='age')?DIR_LABEL.age:DIR_LABEL.other;return m[dir]||m.asc;}
function listToolbarHtml(key,placeholder,opts){
  const s=LIST[key]||listDef();
  const options=opts||[['age','age'],['ticket','ticket'],['title','title']];
  const dir=s.dir||'desc';
  return `<div class="list-bar">
    <label for="q-${key}" class="sr-only">${esc(placeholder||'Search')}</label>
    <input id="q-${key}" type="search" placeholder="${esc(placeholder||'Search…')}" value="${esc(s.q||'')}"
      oninput="LIST['${key}'].q=this.value;resetShown('${key}');saveList('${key}');render()" />
    <label for="s-${key}" class="sr-only">Sort by</label>
    <select id="s-${key}" onchange="LIST['${key}'].sort=this.value;saveList('${key}');render()">
      ${options.map(o=>`<option value="${esc(o[0])}"${s.sort===o[0]?' selected':''}>Sort by ${esc(o[1])}</option>`).join('')}
    </select>
    <button type="button" class="dir" aria-label="Sort direction: ${esc(dirLabel(s.sort,dir))}"
      onclick="LIST['${key}'].dir='${dir==='asc'?'desc':'asc'}';saveList('${key}');render()"
      >${dir==='asc'?'↑':'↓'} ${esc(dirLabel(s.sort,dir))}</button>
  </div>`;}

function isMobile(){return window.matchMedia('(max-width:900px)').matches;}
function placeChatdock(){
  const dock=el('chatdock');if(!dock)return;
  const slot=(isMobile()&&mobSheetOpen==='chat')?el('chat-sheet-slot'):el('rail-chat-slot');
  if(slot&&dock.parentNode!==slot)slot.appendChild(dock);}
function openMobSheet(which){
  const prev=mobSheetOpen;
  if(prev&&prev!==which)closeMobSheets();
  mobSheetOpen=which;placeChatdock();
  el('chat-sheet').classList.toggle('open',which==='chat');
  el('more-sheet').classList.toggle('open',which==='more');
  el('mob-scrim').classList.toggle('open',true);
  syncMobTabs();
  // Sheets carry role=dialog behind a scrim, so they get real modal behaviour:
  // focus starts inside, Tab stays inside, focus returns to the tab that opened.
  const node=el(which==='chat'?'chat-sheet':'more-sheet');
  node.setAttribute('aria-modal','true');
  openModal(node,which==='chat'?el('chatbox'):focusables(node)[0]);}
function closeMobSheets(){
  const was=mobSheetOpen;
  mobSheetOpen=null;
  ['chat-sheet','more-sheet'].forEach(id=>{const n=el(id);
    n.classList.remove('open');n.removeAttribute('aria-modal');closeModal(n);});
  el('mob-scrim').classList.remove('open');
  placeChatdock();
  if(was)syncMobTabs();else setNav();}
function goMob(p){closeMobSheets();go(p);}
function goFromMore(p){closeMobSheets();go(p);}

/* ---------------- navigation ---------------- */
function go(p){
  if(PAGES.indexOf(p)<0)p='nucleus';
  page=p;localStorage.setItem('brutus.page',p);
  // hash drives history: Back returns to the previous page instead of leaving
  if(pageFromHash()!==p)location.hash='#/'+p;
  closeMobSheets();render();}
window.openCapture=()=>{
  go('canon');
  requestAnimationFrame(()=>{const input=el('canoncap');if(input)input.focus();});
};
window.addEventListener('hashchange',()=>{
  const p=pageFromHash();
  if(p&&p!==page){page=p;localStorage.setItem('brutus.page',p);render();}});
function setNav(){
  const pages=PAGES;
  const primary=new Set(['nucleus','agents','notes']);
  for(const p of pages){
    const n=el('nav-'+p);if(!n)continue;
    const on=p===page;
    n.className='nav-item'+(on?' on':'');
    if(on)n.setAttribute('aria-current','page');else n.removeAttribute('aria-current');
    const mn=el('mob-nav-'+p);if(mn){mn.className='nav-item'+(on?' on':'');
      if(on)mn.setAttribute('aria-current','page');else mn.removeAttribute('aria-current');}
  }
  document.querySelectorAll('.mob-tab').forEach(t=>{
    const dp=t.getAttribute('data-page');
    let on=false;
    if(dp==='chat')on=mobSheetOpen==='chat';
    else if(dp==='more')on=mobSheetOpen==='more'||(!mobSheetOpen&&!primary.has(page));
    else on=page===dp&&!mobSheetOpen;
    t.classList.toggle('on',on);
  });
  placeChatdock();}
function syncMobTabs(){setNav();}
let voice={enabled:false,whisper:false,tts:false};
let speakOn=localStorage.getItem('brutus.speak')!=='0';
let liveOn=false, speakingOut=false, speakAudio=null;
let recognition=null, liveRestartTimer=null;
// Whisper live fallback (VAD) — only used when browser speech recognition is missing
let vadStream=null, vadCtx=null, vadAnalyser=null, vadRec=null, vadChunks=[];
let vadSpeaking=false, vadSilentMs=0, vadRaf=0;

/* ---------------- work page ---------------- */
let whyOpen=new Set();
window.toggleWhy=t=>{whyOpen.has(t)?whyOpen.delete(t):whyOpen.add(t);render();
  const b=document.querySelector('[aria-controls="why-'+t+'"]');if(b)b.focus();};
function rowsHtml(rows,btn){if(!rows.length)return '<div class="none">Nothing here.</div>';
  return rows.map(r=>{
    const sig=r.signal?`<div class="dim" style="margin-top:4px">${esc(r.signal)}</div>`:'';
    return `<div class="r"><span class="id">${esc(r.ticket)}</span>
    <span class="t" title="${esc(r.signal?r.title+' — '+r.signal:r.title)}">${esc(r.title)}${sig}</span>
    <span class="a">${esc(r.age||'')}</span>
    <span class="b">${btn?btn(r):''}${linkFor(r)}</span></div>`;
  }).join('');}

function actionItemCount(a){
  const ids=a.thread_ids||[];
  if(ids.length)return ids.length;
  const items=a.items||[];
  return items.length||1;
}
function actionIdsJson(a){
  return JSON.stringify(a.thread_ids||[]).replace(/"/g,'&quot;');
}
function actionTicketIdsJson(a){
  const tix=(a.ticket_ids||(a.items||[]).map(i=>i.external_id).filter(Boolean));
  return JSON.stringify(tix).replace(/"/g,'&quot;');
}
function actionPathsJson(a){
  const paths=a.paths||(a.path?[a.path]:[]);
  return JSON.stringify(paths).replace(/"/g,'&quot;');
}
function focusActionHtml(a){
  const n=actionItemCount(a);
  const verb=a.recommended_verb||'none';
  const info=!!a.informational;
  const open=actionOpen.has(a.id);
  const why=a.why||a.what||'';
  const doit=a.do||'';
  let ops='';
  if(verb==='answer_input'){
    const tid=esc(a.ticket_id||'');
    ops=`<div class="ans-row">
      <label for="i-${tid}" class="sr-only">Your answer for ${esc(a.title||tid)}</label>
      <input type="text" id="i-${tid}" placeholder="Your answer…"
             onkeydown="if(event.key==='Enter')answer('${tid}')" />
      <button type="button" class="prim" onclick="answer('${tid}')">Send answer</button>
      ${/alias|sandbox|which org/i.test(a.question||a.why||'')?
        `<button type="button" class="alt" onclick="answer('${tid}','partial')">Use test sandbox</button>`:''}
    </div>`;
  }else if(verb==='decide_gate'){
    const ids=actionIdsJson(a);
    const aid=JSON.stringify(a.id||'').replace(/"/g,'&quot;');
    ops=`<div class="act-ops">
      <button type="button" class="prim" onclick='decide(${ids},false,${aid})'>Approve ${n}</button>
      <button type="button" class="alt" onclick='decide(${ids},true,${aid})'>Reject ${n}</button>
      <button type="button" class="warnish" onclick='restart(${ids})'>Start over</button>
      ${n>1?`<button type="button" class="alt" onclick="toggleAction('${esc(a.id)}')">${open?'Hide':'Show'} ${n} tickets</button>`:''}
    </div>`;
  }else if(verb==='requeue_stale'){
    const ids=actionIdsJson(a);
    ops=`<div class="act-ops">
      <button type="button" class="prim" onclick='restart(${ids})'>Start all ${n} over</button>
      <button type="button" class="alt" onclick="toggleAction('${esc(a.id)}')">${open?'Hide':'Show'} tickets</button>
    </div>`;
  }else if(verb==='batch_frontier'){
    const paths=actionPathsJson(a);
    const ids=actionIdsJson(a);
    ops=`<div class="act-ops">
      <button type="button" class="prim" onclick='frontierApply(${paths})'>Send ${n} to bots</button>
      ${(a.thread_ids||[]).length?`<button type="button" class="warnish" onclick='restart(${ids})'>Start over</button>`:''}
      <button type="button" class="alt" onclick="toggleAction('${esc(a.id)}')">${open?'Hide':'Show'} tickets</button>
    </div>`;
  }else if(verb==='open_cursor'){
    const paths=actionPathsJson(a);
    ops=`<div class="act-ops">
      <button type="button" class="prim" onclick='cursorApply(${paths})'>Mark Cursor work applied</button>
      <button type="button" class="alt" onclick="toggleAction('${esc(a.id)}')">${open?'Hide':'Show'} brief</button>
    </div>`;
  }else if(!info){
    ops=`<div class="act-ops"><button type="button" class="alt" onclick="toggleAction('${esc(a.id)}')">${open?'Hide':'Show'} details</button></div>`;
  }else if((a.items||[]).length||(a.deferred_titles||[]).length){
    ops=`<div class="act-ops"><button type="button" class="alt" onclick="toggleAction('${esc(a.id)}')">${open?'Hide':'Show'}</button></div>`;
  }
  const itemRows=(a.items||[]).map(it=>{
    const ticket=esc(it.external_id||'');
    const title=esc(it.title||'');
    const href=(it.links&&it.links[0]&&it.links[0].href)||'';
    return `<div class="r"><span class="id">${ticket}</span>
      <span class="t" title="${title}">${title}</span>
      <span class="a">${esc(it.age_minutes!=null?Math.round(it.age_minutes)+'m':'')}</span>
      <span class="b">${href?`<a href="${esc(href)}" target="_blank" rel="noopener">Linear</a>`:''}</span></div>`;
  }).join('');
  const deferred=((a.deferred_titles||[]).map(t=>`<div class="r"><span class="t">${esc(t)}</span></div>`).join(''))||'';
  return `<div class="act-card ${info?'':'you'} ${open?'open':''}" data-act="${esc(a.id)}">
    <div class="act-h">
      <span class="cnt">${info?'·':n}</span>
      <div class="meta">
        <p class="ttl">${esc(a.title||'Action')}</p>
        ${why?`<p class="why">${esc(why)}</p>`:''}
        ${doit?`<p class="do">${esc(doit)}</p>`:''}
      </div>
    </div>
    ${ops}
    <div class="act-items">${itemRows||deferred||'<div class="none">No ticket list.</div>'}</div>
  </div>`;
}
function focusActionsHtml(actions,q){
  let list=actions||[];
  if(q){
    const qq=q.toLowerCase();
    list=list.filter(a=>[a.title,a.why,a.what,a.do,a.reason_label,(a.items||[]).map(i=>i.external_id+' '+(i.title||'')).join(' ')]
      .join(' ').toLowerCase().includes(qq));
  }
  if(!list.length)return (actions&&actions.length)?'<div class="none">Nothing matches.</div>':
    '<div class="none">Nothing needs you. The bots have it.</div>';
  return list.map(focusActionHtml).join('');
}
window.toggleAction=id=>{actionOpen.has(id)?actionOpen.delete(id):actionOpen.add(id);render();};
function cappedHtml(rows,q){
  let list=rows||[];
  if(q)list=filterRows(list,q,['ticket','title','notes','action']);
  if(!list.length)return (rows&&rows.length)?'<div class="none">Nothing matches.</div>':'';
  return list.map(r=>{
    const t=esc(r.ticket);
    const act=esc(r.action||'investigate');
    return `<div class="r"><span class="id">${t}</span>
      <span class="t" title="${esc(r.title)}">${esc(r.title)}</span>
      <span class="a">${esc(String(r.attempts||'?'))}×</span>
      <span class="b"><button type="button" class="alt" onclick="uncap('${t}','${act}')">Reset attempts</button>
        ${r.link?linkFor(r):''}</span></div>
      <div class="q dim">${esc(r.notes||r.run_state||'')}</div>`;
  }).join('');}

function stuckHtml(groups,q){
  let filtered=groups;
  if(q)filtered=groups.filter(g=>(g.reason||'').toLowerCase().includes(q.toLowerCase())||
    (g.why||'').toLowerCase().includes(q.toLowerCase())||
    (g.rows||[]).some(r=>['ticket','title'].some(f=>String(r[f]||'').toLowerCase().includes(q.toLowerCase()))));
  if(!filtered.length)return q?'<div class="none">Nothing matches.</div>':'<div class="none">Nothing stuck.</div>';
  return filtered.map((g,i)=>{
    const id='g'+i,isOpen=open.has(g.reason);
    const steer=g.unstick==='steer';
    const ids=JSON.stringify(steer?g.tickets:g.thread_ids).replace(/"/g,'&quot;');
    const rid=id+'-rows';
    return `<div class="grp ${isOpen?'open':''}" id="${id}">
      <button type="button" class="grp-h" aria-expanded="${isOpen?'true':'false'}" aria-controls="${rid}"
        onclick="toggle('${esc(g.reason)}')">
        <span class="cnt">${g.count}</span><span class="rsn">${esc(g.reason)}</span>
        <span class="car">${isOpen?'hide':'show'} tickets</span></button>
      <div class="why">${esc(g.why)}</div>
      <div class="act"><button type="button" onclick="${steer?'steerRestart':'restart'}(${ids})">Start all ${g.count} over</button></div>
      <div class="rows" id="${rid}">${rowsHtml(sortRows(g.rows||[],LIST.work.sort,null,LIST.work.dir))}</div></div>`;
  }).join('');}

function sec(name,n,inner,you,right){return `<div><div class="sech ${you?'you':''}">
  <span class="name">${esc(name)}</span><span class="n">${n??''}</span>
  ${right?`<span class="right">${right}</span>`:''}</div>${inner}</div>`;}

/* The alarm reaches Justin on two channels (banner + chat dock, both by design).
   They no longer say the identical sentence twice on one screen, and "nothing
   finished while 0 are with the bots" now reads as the empty pipe it is. */
function alarmText(a){
  const w=a.window_hours||6, f=a.in_flight||0;
  if(a.done_total===0)return{
    short:'No ticket has ever finished. Work goes in and nothing comes out.',
    long:'Factory alarm: no ticket has ever finished. Work goes in and nothing comes out — that is a fault, not a quiet day.'};
  if(!f)return{
    short:`Nothing finished in over ${w}h, and nothing is with the bots.`,
    long:`Factory alarm: nothing has finished in over ${w}h and nothing is currently with the bots — the pipe is empty, so work is not being picked up.`};
  return{
    short:`Nothing finished in over ${w}h while ${f} ${f===1?'is':'are'} with the bots.`,
    long:`Factory alarm: nothing has finished in over ${w}h while ${f} ${f===1?'is':'are'} still with the bots — they are running but not landing.`};}

function streamsHtml(){
  const drafts=T.filter(t=>t.status!=='done'&&(t.stage==='Refining'||t.stage==='Captured'||(!t.stage&&(t.lane||'Inbox')==='Inbox'))).length;
  const agents=(AGcounts&&AGcounts.total)||0;
  const risk=P.filter(p=>p.at_risk).length;
  const down=S.filter(x=>x.live===false).length;
  const chip=(label,n,page,hot)=>`<button type="button" class="chip ${hot&&n?'hot':''}" onclick="go('${page}')">${esc(label)}${n?` · ${n}`:''}</button>`;
  return `<div class="streams" aria-label="Cross-stream status">
    ${chip('Drafts',drafts,'notes',drafts>20)}
    ${chip('Agents',agents,'agents',agents>20)}
    ${chip('Repos at risk',risk,'projects',!!risk)}
    ${chip('Sites down',down,'chatbots',!!down)}
    <a class="chip" href="/session">Queue →</a>
  </div>`;
}
function briefHtml(){
  const b=Brief||{};
  const body=b.markdown||b.text||b.error||'';
  const open=briefOpen?' open':'';
  return `<details class="brief"${open} id="brief-box" ontoggle="briefOpen=this.open">
    <summary><span class="tag">Morning brief</span>
      <span>${b.ok===false?'Studio unreachable — retry':(body?'Today’s gates & ready work':'Tap to load')}</span></summary>
    <div class="body" id="brief-body">${body?esc(body):''}</div>
  </details>`;
}
function workHtml(){
  return triadWrap(loadSt.board,'loadBoard',()=>{
    const c=B.counts||{};const q=(LIST.work.q||'').trim();
    const actions=B.actions||[];
    const touchable=c.needs_you!=null?c.needs_you:(B.justin_touchable_count||0);
    let h=`<div class="door-link"><a href="/session">Open the capture queue</a>
      <span class="dim">·</span><span class="dim">This page is decisions. Capture lives on /session.</span></div>`;
    h+=streamsHtml();
    h+=briefHtml();
    h+=listToolbarHtml('work','Search work…');
    h+=`<div id="work-err" class="inline-err"></div>`;
    // Focus actions are the product. Per-ticket needs_you rows stay available
    // only when the analyzer sent nothing (degraded Studio).
    if(actions.length){
      h+=sec('Needs you',touchable,focusActionsHtml(actions,q),true);
    }else{
      h+=sec('Needs you',c.needs_you_items||c.needs_you||0,
        '<div class="none">Nothing needs you. The bots have it.</div>',true);
    }
    if(B.stuck_total)h+=sec(`Stuck — ${B.stuck.length} decision${B.stuck.length!==1?'s':''}`,
      B.stuck_total,stuckHtml(B.stuck||[],q));
    if((Capped||[]).length)h+=sec('Capped attempts (manual uncap)',Capped.length,cappedHtml(Capped,q));
    const sortIt=rows=>sortRows(rows,LIST.work.sort,null,LIST.work.dir);
    const working=sortIt(filterRows(B.working||[],q,['ticket','title']));
    const queued=sortIt(filterRows(B.queued||[],q,['ticket','title']));
    if(working.length){const pg=pageSlice('work.working',working,'in flight');
      h+=sec('With the bots',working.length,rowsHtml(pg.rows)+pg.bar);}
    else if(q&&(B.working||[]).length)h+=sec('With the bots',0,'<div class="none">Nothing matches.</div>');
    if(queued.length){const pg=pageSlice('work.queued',queued,'queued');
      h+=sec('Queued',queued.length,rowsHtml(pg.rows)+pg.bar);}
    else if(q&&(B.queued||[]).length)h+=sec('Queued',0,'<div class="none">Nothing matches.</div>');
    h+=`<div class="none" style="margin-top:16px">
      <button type="button" class="linkish dim" onclick="toggleProbes()">${probes?'Hide':'Show'} bot self-tests</button>
      &nbsp;·&nbsp; <button type="button" class="linkish dim" onclick="checkNow()">Check now</button>
      &nbsp;·&nbsp; <button type="button" class="linkish dim" onclick="loadBrief(true)">Refresh brief</button></div>`;
    return h;});}

/* ---------------- Nucleus command center ---------------- */
function nucleusRows(){
  const q=(NF.q||'').trim().toLowerCase();
  let rows=(N.projects||[]).filter(p=>!p.archived);
  if(q)rows=rows.filter(p=>(`${p.id} ${p.name} ${p.objective||''} ${(p.attention_reasons||[]).join(' ')} `+
    `${(p.tickets||[]).map(t=>`${t.ticket} ${t.title}`).join(' ')} ${(p.threads||[]).map(t=>t.title).join(' ')}`).toLowerCase().includes(q));
  if(NF.status)rows=rows.filter(p=>p.status===NF.status);
  if(NF.source==='linear')rows=rows.filter(p=>(p.ticket_count||0)>0);
  else if(NF.source)rows=rows.filter(p=>((p.thread_counts||{})[NF.source]||0)>0);
  return orderedGridRows(rows,NF.sort,NF.dir);
}
function nucleusSort(key){if(NF.sort===key)NF.dir=NF.dir==='asc'?'desc':'asc';else{NF.sort=key;NF.dir=key==='name'?'asc':'desc';}NF.page=1;nucleusApplyGrid();}
function nucleusSortLabel(key,label){const on=NF.sort===key,order=on?(NF.dir==='asc'?'ascending':'descending'):'none';
  return `<button type="button" onclick="nucleusSort('${key}')">${esc(label)}${on?(NF.dir==='asc'?' ↑':' ↓'):''}</button><span class="sr-only">${order}</span>`;}
function nucleusFilter(){NF.q=(el('nucleus-q').value||'').trim();NF.status=el('nucleus-status').value;NF.source=el('nucleus-source').value;NF.page=1;nucleusApplyGrid();}
function nucleusLiveFilter(input){NF.q=input.value;NF.page=1;nucleusApplyGrid();}
function nucleusClear(){NF.q='';NF.status='';NF.source='';NF.page=1;render();}
function nucleusToggleCol(key,on){NF.cols[key]=!!on;render();}
function nucleusSelect(id,on){if(on)NF.selected.add(id);else NF.selected.delete(id);render();}
function nucleusSelectPage(on){const rows=nucleusRows().slice((NF.page-1)*NF.size,NF.page*NF.size);rows.forEach(p=>on?NF.selected.add(p.id):NF.selected.delete(p.id));render();}
function nucleusResizeKey(ev){if(ev.key!=='ArrowLeft'&&ev.key!=='ArrowRight')return;ev.preventDefault();const th=ev.currentTarget.closest('th');
  const next=Math.max(96,th.getBoundingClientRect().width+(ev.key==='ArrowRight'?16:-16));th.style.width=next+'px';ev.currentTarget.setAttribute('aria-valuenow',String(Math.round(next)));}
function nucleusResizeStart(ev){ev.preventDefault();const th=ev.currentTarget.closest('th'),start=ev.clientX,width=th.getBoundingClientRect().width;
  const handle=ev.currentTarget;const move=e=>{const next=Math.max(96,width+e.clientX-start);th.style.width=next+'px';handle.setAttribute('aria-valuenow',String(Math.round(next)));};const stop=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',stop);};
  window.addEventListener('pointermove',move);window.addEventListener('pointerup',stop);}
function nucleusRowHtml(p){return `<tr class="${p.id===NSEL?'selected':''}" onclick="NSEL='${esc(p.id)}';render()">
    <td onclick="event.stopPropagation()"><input type="checkbox" aria-label="Select ${esc(p.name)}" ${NF.selected.has(p.id)?'checked':''} onchange="nucleusSelect('${esc(p.id)}',this.checked)"></td>
    <td class="project-cell"><strong>${p.pinned?'★ ':''}${esc(p.name)}</strong><span title="${esc(p.id)}">${esc(p.id)}</span></td>
    <td><span class="status ${esc(p.status)}">${esc((p.status||'quiet').replace('_',' '))}</span><span class="cell-sub">${esc((p.attention_reasons||[])[0]||'No immediate action')}</span></td>
    ${NF.cols.tickets?`<td class="num">${p.ticket_count||0}<span class="cell-sub">${p.needs_you_count||0} need you · ${p.active_ticket_count||0} active</span></td>`:''}
    ${NF.cols.threads?`<td class="num">${p.thread_count||0}<span class="cell-sub">Cdx ${(p.thread_counts||{}).codex||0} · Cur ${(p.thread_counts||{}).cursor||0} · Cla ${(p.thread_counts||{}).claude||0}</span></td>`:''}
    ${NF.cols.risk?`<td class="num">${p.dirty||0}/${p.unpushed||0}<span class="cell-sub">unsaved / unpushed</span></td>`:''}
    <td onclick="event.stopPropagation()"><button type="button" data-row-action onclick="nucleusAsk('${esc(p.id)}')">Ask</button></td></tr>`;}
function nucleusApplyGrid(){const body=el('nucleus-body');if(!body){render();return;}const rows=nucleusRows(),pages=Math.max(1,Math.ceil(rows.length/NF.size));
  if(NF.page>pages)NF.page=pages;const start=(NF.page-1)*NF.size,shown=rows.slice(start,start+NF.size);body.innerHTML=shown.map(nucleusRowHtml).join('');
  const empty=el('nucleus-empty');if(empty){empty.hidden=shown.length>0;empty.textContent=(NF.q||NF.status||NF.source)?'No projects match these filters. Clear filters to restore the full population.':'No project population was returned.';}
  const range=el('nucleus-range');if(range){range.textContent=rows.length?`${start+1}–${Math.min(start+NF.size,rows.length)} of ${rows.length}`:'0 of 0';range.dataset.total=String(rows.length);}
  const pageLabel=el('nucleus-page-label');if(pageLabel)pageLabel.textContent=`Page ${NF.page} of ${pages}`;
  const prev=el('nucleus-prev'),next=el('nucleus-next');if(prev)prev.disabled=NF.page<=1;if(next)next.disabled=NF.page>=pages;
  document.querySelectorAll('#nucleus-table th[data-sort-key]').forEach(th=>th.setAttribute('aria-sort',th.dataset.sortKey===NF.sort?(NF.dir==='asc'?'ascending':'descending'):'none'));}
function nucleusPage(delta){NF.page+=delta;nucleusApplyGrid();}
async function nucleusBatchPin(){const ids=[...NF.selected];if(!ids.length)return;
  try{await Promise.all(ids.map(id=>post('/api/nucleus/projects/'+encodeURIComponent(id),{pinned:true},'PATCH')));NF.selected.clear();toast(`Pinned ${ids.length} projects`);await loadNucleus(true);}
  catch(e){toast('Could not pin projects: '+e.message,true);}}
async function nucleusPin(id,on){try{await post('/api/nucleus/projects/'+encodeURIComponent(id),{pinned:!!on},'PATCH');toast(on?'Project pinned':'Project unpinned');await loadNucleus(true);}
  catch(e){toast('Could not update project: '+e.message,true);}}
function nucleusAsk(id){const p=(N.projects||[]).find(x=>x.id===id);go('nucleus');
  el('chatbox').value=`Give me the current Nucleus picture for project ${id}${p?` (${p.name})`:''}. Lead with what needs me and cite the exact ticket and agent thread ids.`;
  el('chatbox').focus();}
function nucleusDetail(p){if(!p)return '<div class="nucleus-detail"><div class="none">Select a project to see its Linear tickets, agent threads, and workspaces.</div></div>';
  const tickets=(p.tickets||[]).slice(0,12),threads=(p.threads||[]).slice(0,12);
  const ticketRows=tickets.length?tickets.map(t=>`<li><span class="native-id">${esc(t.ticket||t.id)}</span> · ${esc(t.title)}<span class="cell-sub">${esc(t.state||'')} · ${esc(t.assignee||'unassigned')}</span></li>`).join(''):'<li>No mapped Linear tickets.</li>';
  const threadRows=threads.length?threads.map(t=>`<li><span class="native-id">${esc(t.id)}</span> · ${esc(t.title||'Untitled')}<span class="cell-sub">${esc(t.surface)} · ${esc(t.state||'unknown')} · ${esc(t.age||'')}</span></li>`).join(''):'<li>No mapped agent threads.</li>';
  return `<section class="nucleus-detail" aria-label="Selected project details"><header><div class="copy"><h2>${esc(p.name)}</h2>
    <p>${esc(p.objective||(p.attention_reasons||[]).join(' · ')||'No objective set yet.')}</p><p class="native-id">${esc(p.id)}</p></div>
    <button type="button" onclick="nucleusPin('${esc(p.id)}',${!p.pinned})">${p.pinned?'Unpin':'Pin'}</button>
    <button type="button" onclick="nucleusAsk('${esc(p.id)}')">Ask Brutus about this project</button></header>
    <div><h3>Linear tickets · ${p.ticket_count||0}</h3><ul>${ticketRows}</ul></div>
    <div><h3>Agent threads · ${p.thread_count||0}</h3><ul>${threadRows}</ul></div></section>`;}
function nucleusHtml(){return triadWrap(loadSt.nucleus,'loadNucleus',()=>{
  const s=N.summary||{},rows=nucleusRows(),total=rows.length,pages=Math.max(1,Math.ceil(total/NF.size));
  if(NF.page>pages)NF.page=pages;const start=(NF.page-1)*NF.size,shown=rows.slice(start,start+NF.size);
  if(!NSEL&&shown.length)NSEL=shown[0].id;let selected=(N.projects||[]).find(p=>p.id===NSEL)||shown[0];
  const src=N.source_status||{};const sourceHtml=Object.entries(src).map(([name,v])=>`<span class="source"><span class="dot ${v.state==='fresh'?'ok':v.state==='partial'?'warn':'bad'}"></span>${esc(name)} · ${esc(v.state)} · ${esc(v.count||0)}</span>`).join('');
  const allSelected=shown.length&&shown.every(p=>NF.selected.has(p.id));
  const rowHtml=shown.map(nucleusRowHtml).join('');
  return `<div class="nucleus-shell" data-grid data-client-mode><h2 class="sr-only" data-grid-title>Project Nucleus operating graph</h2><section class="nucleus-metrics" aria-label="Portfolio summary">
    <div class="nucleus-metric"><span class="value">${s.projects||0}</span><span class="label">canonical projects</span></div>
    <div class="nucleus-metric"><span class="value">${s.projects_needing_you||0}</span><span class="label">need you now</span></div>
    <div class="nucleus-metric"><span class="value">${s.tickets||0}</span><span class="label">open Linear tickets</span></div>
    <div class="nucleus-metric"><span class="value">${s.recent_threads||0}</span><span class="label">agent threads · 48h</span></div></section>
    <div class="nucleus-source" aria-label="Source freshness">${sourceHtml}</div>
    <div class="nucleus-toolbar" data-toolbar role="search"><label for="nucleus-q" class="sr-only">Search Nucleus</label>
      <input type="search" data-filter-input data-shine-probe-value="__shine_no_match__" id="nucleus-q" value="${esc(NF.q)}" placeholder="Search projects, tickets, threads, or paths…" oninput="nucleusLiveFilter(this)" onkeydown="if(event.key==='Enter')nucleusFilter()">
      <select id="nucleus-status" aria-label="Attention filter" onchange="nucleusFilter()"><option value="">All attention</option>${[['needs_you','Needs you'],['at_risk','At risk'],['active','Active'],['quiet','Quiet']].map(x=>`<option value="${x[0]}"${NF.status===x[0]?' selected':''}>${x[1]}</option>`).join('')}</select>
      <select id="nucleus-source" aria-label="Source filter" onchange="nucleusFilter()"><option value="">All sources</option>${['linear','codex','cursor','claude'].map(x=>`<option value="${x}"${NF.source===x?' selected':''}>${x[0].toUpperCase()+x.slice(1)}</option>`).join('')}</select>
      <button type="button" onclick="nucleusFilter()">Search</button><button type="button" data-clear-filters aria-label="Clear all filters" onclick="nucleusClear()">Clear</button>
      <button type="button" ${NF.selected.size?'':'disabled'} onclick="nucleusBatchPin()">Pin selected · ${NF.selected.size}</button>
      <details class="nucleus-cols"><summary>Columns</summary><div class="menu">${Object.entries({tickets:'Linear tickets',threads:'Agent threads',risk:'Git risk'}).map(([k,l])=>`<label><input type="checkbox" ${NF.cols[k]?'checked':''} onchange="nucleusToggleCol('${k}',this.checked)">${l}</label>`).join('')}</div></details></div>
    <div hidden aria-hidden="true"><span data-state="loading"></span><span data-state="empty"></span><span data-state="filtered-empty"></span><span data-state="error" role="alert"><button type="button" data-retry onclick="loadNucleus(true)">Retry</button></span></div>
    <div class="nucleus-grid" data-grid-scroll><div id="nucleus-empty" class="none" data-state="${NF.q||NF.status||NF.source?'filtered-empty':'empty'}" ${total?'hidden':''}>${NF.q||NF.status||NF.source?'No projects match these filters. Clear filters to restore the full population.':'No project population was returned. Check source status above.'}</div><table id="nucleus-table" class="nucleus-table" data-shine-contract="table" data-client-mode><thead><tr>
      <th style="width:var(--shine-space-10)"><input type="checkbox" aria-label="Select this page" ${allSelected?'checked':''} onchange="nucleusSelectPage(this.checked)"></th>
      <th data-sort-key="name" aria-sort="${NF.sort==='name'?(NF.dir==='asc'?'ascending':'descending'):'none'}">${nucleusSortLabel('name','Project')}<span class="resize" role="separator" aria-label="Resize project column" aria-orientation="vertical" aria-valuemin="96" aria-valuemax="640" aria-valuenow="240" tabindex="0" data-column-resize onpointerdown="nucleusResizeStart(event)" onkeydown="nucleusResizeKey(event)"></span></th>
      <th data-sort-key="attention_score" aria-sort="${NF.sort==='attention_score'?(NF.dir==='asc'?'ascending':'descending'):'none'}">${nucleusSortLabel('attention_score','Attention')}<span class="resize" role="separator" aria-label="Resize attention column" aria-orientation="vertical" aria-valuemin="96" aria-valuemax="640" aria-valuenow="240" tabindex="0" data-column-resize onpointerdown="nucleusResizeStart(event)" onkeydown="nucleusResizeKey(event)"></span></th>
      ${NF.cols.tickets?`<th data-sort-key="ticket_count" aria-sort="${NF.sort==='ticket_count'?(NF.dir==='asc'?'ascending':'descending'):'none'}">${nucleusSortLabel('ticket_count','Linear')}</th>`:''}
      ${NF.cols.threads?`<th data-sort-key="thread_count" aria-sort="${NF.sort==='thread_count'?(NF.dir==='asc'?'ascending':'descending'):'none'}">${nucleusSortLabel('thread_count','Agents')}</th>`:''}
      ${NF.cols.risk?'<th>Git risk</th>':''}<th>Action</th></tr></thead><tbody id="nucleus-body">${rowHtml}</tbody></table>
      <div class="nucleus-pager" data-pagination aria-label="Project pagination"><span id="nucleus-range" class="range" data-page-range data-total="${total}">${total?`${start+1}–${Math.min(start+NF.size,total)} of ${total}`:'0 of 0'}</span><label>Rows <select data-page-size onchange="NF.size=Number(this.value);NF.page=1;nucleusApplyGrid()">${[10,20,50].map(x=>`<option value="${x}"${NF.size===x?' selected':''}>${x}</option>`).join('')}</select></label><button id="nucleus-prev" type="button" ${NF.page<=1?'disabled':''} onclick="nucleusPage(-1)">Previous</button><span id="nucleus-page-label">Page ${NF.page} of ${pages}</span><button id="nucleus-next" type="button" data-page-next aria-label="Next page" ${NF.page>=pages?'disabled':''} onclick="nucleusPage(1)">Next</button></div></div>
    ${nucleusDetail(selected)}</div>`;});}

/* ---------------- agents page ---------------- */
function agentsHtml(){
  const filt=`<div class="filt list-bar">
    <select id="ag-surf" onchange="AGfilt.surface=this.value;loadAgents()" aria-label="Surface filter">
      <option value=""${AGfilt.surface===''?' selected':''}>All surfaces</option>
      <option value="codex"${AGfilt.surface==='codex'?' selected':''}>Codex</option>
      <option value="cursor"${AGfilt.surface==='cursor'?' selected':''}>Cursor</option>
      <option value="claude"${AGfilt.surface==='claude'?' selected':''}>Claude</option>
    </select>
    <label for="ag-q" class="sr-only">Filter agents by title or folder</label>
    <input id="ag-q" placeholder="Filter title or folder…" value="${esc(AGfilt.q||'')}"
           onkeydown="if(event.key==='Enter'){AGfilt.q=this.value;loadAgents()}" />
    <button type="button" onclick="AGfilt.q=(el('ag-q').value||'');loadAgents()">Filter</button>
    <button type="button" onclick="AGfilt.showHidden=!AGfilt.showHidden;loadAgents()">${AGfilt.showHidden?'Hide parked':'Show parked'}</button>
  </div><div id="agents-err" class="inline-err"></div>`;
  return triadWrap(loadSt.agents,'loadAgents',()=>{
    if(!AG.length)return filt+'<div class="none">No recent Codex, Cursor, or Claude threads were found.</div>';
    const cdx=AG.filter(a=>a.surface==='codex'),cur=AG.filter(a=>a.surface==='cursor'), cla=AG.filter(a=>a.surface==='claude');
    const row=a=>{
      const sid=esc(a.id);
      const flags=[
        a.live?'<span class="flag live">live</span>':'',
        a.kept?'<span class="flag kept">kept</span>':'',
        a.surface==='codex'?'<span class="flag cur">Codex</span>':a.surface==='cursor'?'<span class="flag cur">Cursor</span>':'<span class="flag cla">Claude</span>',
      ].filter(Boolean).join(' ');
      const where=a.cwd||a.project||'';
      return `<div class="p">
        <span class="nm" title="${esc(a.title)}">${esc(a.title)}</span>
        <span class="cm" title="${esc(where)}">${esc(where)}</span>
        ${flags}
        <span class="a">${esc(a.age||'')}</span>
        <span class="b">
          <button type="button" onclick="agentKeep('${sid}',${!a.kept})">${a.kept?'Unkeep':'Keep'}</button>
          <button type="button" onclick="agentHide('${sid}',${!a.hidden})">${a.hidden?'Unhide':'Park'}</button>
          <button type="button" onclick="agentPromote('${sid}','notes')">To Notes</button>
          <button type="button" onclick="agentCopy('${sid}')">Copy id</button>
        </span></div>`;
    };
    let h=filt;
    if(cdx.length){const pg=pageSlice('agents.codex',cdx,'threads');
      h+=sec('Codex',cdx.length,pg.rows.map(row).join('')+pg.bar);}
    if(cur.length){const pg=pageSlice('agents.cursor',cur,'threads');
      h+=sec('Cursor',cur.length,pg.rows.map(row).join('')+pg.bar);}
    if(cla.length){const pg=pageSlice('agents.claude',cla,'threads');
      h+=sec('Claude Code',cla.length,pg.rows.map(row).join('')+pg.bar);}
    return h;});}
window.agentKeep=async(id,on)=>{clearInlineError('agents-err');
  try{await post('/api/agents/'+encodeURIComponent(id),{kept:!!on},'PATCH');
    toast(on?'Kept':'Unkept');await loadAgents();}
  catch(e){showInlineError('agents-err',e.message);}};
window.agentHide=async(id,on)=>{
  if(on){confirmAction({title:'Park this agent?',body:'It will be hidden from the default list. You can show parked agents anytime.',
    confirmLabel:'Park',danger:true,onConfirm:()=>doAgentHide(id,true)});return;}
  await doAgentHide(id,false);};
async function doAgentHide(id,on){clearInlineError('agents-err');
  try{await post('/api/agents/'+encodeURIComponent(id),{hidden:!!on},'PATCH');
    toast(on?'Parked':'Unhidden');await loadAgents();}
  catch(e){showInlineError('agents-err',e.message);}}
window.agentPromote=async(id,to)=>{await doAgentPromote(id,to);};
async function doAgentPromote(id,to){clearInlineError('agents-err');
  try{const d=await post('/api/agents/'+encodeURIComponent(id)+'/promote',{to});
    toast('Saved to Notes');
    if(to==='notes')loadTodos();await loadAgents();}
  catch(e){showInlineError('agents-err',e.message);}}
window.agentCopy=async id=>{try{await navigator.clipboard.writeText(id);toast('Copied '+id);}
  catch(e){toast(id);}};

/* ---------------- projects page ---------------- */
function projectsHtml(){
  return triadWrap(loadSt.projects,'loadProjects',()=>{
    if(!P.length)return '<div class="none">No projects found.</div>';
    const q=(LIST.projects.q||'').trim();
    const filtered=sortRows(filterRows(P,q,['name','branch','last_commit','path']),LIST.projects.sort,
      {age:'last_commit_epoch',ticket:'name',title:'name'},LIST.projects.dir);
    if(!filtered.length)return listToolbarHtml('projects','Search projects…',
        [['age','last commit'],['ticket','name']])+
      '<div class="none">Nothing matches.</div>';
    const risk=filtered.filter(p=>p.at_risk), hot=filtered.filter(p=>!p.at_risk&&p.activity==='hot'),
          rest=filtered.filter(p=>!p.at_risk&&p.activity!=='hot');
    const row=p=>`<div class="p"><span class="nm" title="${esc(p.path)}">${esc(p.name)}</span>
      <span class="br">${esc(p.branch)}</span>
      <span class="cm" title="${esc(p.last_commit)}">${esc(p.last_commit)}</span>
      ${p.dirty?`<span class="flag dirty">${p.dirty} unsaved</span>`:''}
      ${p.unpushed?`<span class="flag push">${p.unpushed} ${p.never_pushed?'only on this laptop':'unpushed'}</span>`:''}
      <span class="a">${esc(p.age)}</span></div>`;
    let h=listToolbarHtml('projects','Search projects…',[['age','last commit'],['ticket','name']])
      +'<div id="projects-err" class="inline-err"></div>';
    if(risk.length)h+=sec('Carrying unsaved or unpushed work',risk.length,risk.map(row).join(''),true);
    if(hot.length)h+=sec('Active this week',hot.length,hot.map(row).join(''));
    if(rest.length){const pg=pageSlice('projects.rest',rest,'repos');
      h+=sec('Everything else',rest.length,pg.rows.map(row).join('')+pg.bar);}
    return h;});}

/* ---------------- notes page ---------------- */
const LANES = ['Inbox','In Progress','Blocked','Done'];
let menuOpen=new Set();
window.toggleTodoMenu=id=>{
  if(menuOpen.has(id))menuOpen.delete(id);else{menuOpen.clear();menuOpen.add(id);}
  render();
  const b=document.querySelector('[aria-controls="tm-'+id+'"]');if(b)b.focus();};
function laneForStatus(s){return {todo:'Inbox',doing:'In Progress',done:'Done'}[s]||'Inbox';}
function tagSpan(tags){
  if(!tags) return '';
  return tags.split(',').map(t=>t.trim()).filter(Boolean)
    .map(t=>`<span class="tag">${esc(t)}</span>`).join(' ');
}
function canonHtml(){
  const cap=`<div class="cap">
    <label for="canoncap" class="sr-only">Capture work</label>
    <textarea id="canoncap" rows="3" placeholder="What needs to happen?"></textarea>
    <button type="button" onclick="captureCanon()">Capture</button>
    <button type="button" class="owner-auth" onclick="authenticateCanon()">Unlock owner actions</button>
  </div><div id="canon-err" class="inline-err"></div>`;
  return triadWrap(loadSt.canon,'loadCanon',()=>{
    const inbox=Canon.inbox||[], today=Canon.today||[], review=Canon.review||[], cards=Canon.execution_cards||[];
    const inboxInner=!inbox.length?'<div class="none">Nothing waiting.</div>':
      inbox.map(it=>`<div class="p"><span class="nm">${esc((it.raw_capture||'').slice(0,140))}</span>
        <span class="cm">${esc(it.source||'')}</span>
        <span class="b"><button type="button" onclick="promoteCanon('${esc(it.id)}')">Start</button></span></div>`).join('');
    const evCount=it=>((it&&it.evidence_refs)||[]).length;
    const todayInner=!today.length?'<div class="none">Nothing in motion.</div>':
      today.map(it=>`<div class="p"><span class="nm">${esc(it.title||'')}</span>
        <span class="st">${esc(it.state||'')}</span>
        <span class="cm">${evCount(it)?evCount(it)+' evidence':''}</span></div>`).join('');
    const reviewInner=!review.length?'<div class="none">Nothing to review.</div>':
      review.map(it=>`<div class="p"><span class="nm">${esc(it.title||'')}</span>
        <span class="st">${esc(it.state||'')}</span>
        <span class="cm">${evCount(it)?evCount(it)+' evidence':''}</span>
        <span class="b"><button type="button" onclick="reviewCanon('${esc(it.id)}')">Accept</button></span></div>`).join('');
    const cardInner=!cards.length?'<div class="none">No cards yet.</div>':
      cards.map(it=>`<div class="p"><span class="nm">${esc(it.scope||it.id)}</span>
        <span class="st">${esc(it.status||'')}</span></div>`).join('');
    return cap+sec('Inbox',inbox.length,inboxInner,true)+sec('Today',today.length,todayInner)+
      sec('Review',review.length,reviewInner,true)+sec('Cards',cards.length,cardInner);
  });
}
function notesHtml(){
  const cap=`<div class="cap">
    <label for="newtodo" class="sr-only">Capture a note</label>
    <input id="newtodo" placeholder="Capture a thought — hit Enter" onkeydown="if(event.key==='Enter')addTodo()" />
    <label for="newtags" class="sr-only">Tags for note</label>
    <input id="newtags" placeholder="tags (optional)" onkeydown="if(event.key==='Enter')addTodo()" />
    <button type="button" onclick="addTodo()">Add</button>
  </div><div id="notes-err" class="inline-err"></div>`;
  let h=cap;
  const kanbanInner=triadWrap(loadSt.todos,'loadTodos',()=>{
    if(!T.length)return '<div class="none">Nothing captured yet. Anything you type is saved here, and can become a real tracked ticket with one click.</div>';
    const byLane={}; LANES.forEach(l=>byLane[l]=[]);
    T.forEach(t=>{byLane[t.lane||laneForStatus(t.status)].push(t);});
    // Six peer buttons per card (3 lane moves + promote + tag + delete) is
    // button spam, and "move" looked exactly like "act". One primary action
    // plus a disclosure; move and act are labelled groups inside it.
    const row=t=>{
      const cur=t.lane||laneForStatus(t.status);
      const isOpen=menuOpen.has(t.id);
      const mid='tm-'+t.id;
      const moves=LANES.filter(l=>l!==cur)
        .map(l=>`<button type="button" onclick="setLane('${t.id}','${l}')">${esc(l)}</button>`).join('');
      return `<div class="todo">
      <span class="tx">${esc(t.text)}</span>
      <span class="tags">${tagSpan(t.tags)}</span>
      ${t.promoted_ticket?`<span class="pt">→ ${esc(t.promoted_ticket)}</span>`:''}
      <span class="b">
        ${!t.promoted_ticket?`<button type="button" class="prim" onclick="promote('${t.id}')"
          title="Make this a tracked ticket the bots can work">Send to bots</button>`:''}
        <button type="button" class="more" aria-expanded="${isOpen?'true':'false'}" aria-controls="${mid}"
          onclick="toggleTodoMenu('${t.id}')">${isOpen?'Close':'More…'}</button>
      </span>
      ${isOpen?`<div class="tmenu" id="${mid}">
        <span class="lbl">Move to</span>
        <span class="grp2">${moves}</span>
        <span class="lbl">This note</span>
        <span class="grp2">
          <button type="button" onclick="editTags('${t.id}')">Edit tags</button>
          <button type="button" class="del" onclick="delTodo('${t.id}')">Delete</button>
        </span></div>`:''}</div>`;};
    // Work is read and acted on vertically. A sideways board turns the final
    // lanes into hidden work and makes each card too narrow to be useful.
    const laneList=lane=>`<section class="notes-lane" aria-label="${esc(lane)} notes">
      <header><span>${esc(lane)}</span><span>${byLane[lane].length}</span></header>
      <div class="notes-list">${byLane[lane].length?byLane[lane].map(row).join(''):'<div class="none">Nothing here.</div>'}</div></section>`;
    return `<div class="notes-stack" id="notes-stack">${LANES.map(laneList).join('')}</div>`;});
  h+=kanbanInner;
  const lesCap=`<div class="cap" style="margin-top:var(--shine-space-4)">
    <label for="newles" class="sr-only">Lesson title</label>
    <input id="newles" placeholder="Lesson — what we learned" onkeydown="if(event.key==='Enter')addLesson()" />
    <label for="newlestags" class="sr-only">Lesson tags</label>
    <input id="newlestags" placeholder="tags" onkeydown="if(event.key==='Enter')addLesson()" />
    <button type="button" onclick="addLesson()">Save lesson</button>
  </div><div id="lessons-err" class="inline-err"></div>`;
  const lessonsInner=triadWrap(loadSt.lessons,'loadLessons',()=>
    (L&&L.length)?L.map(x=>`<div class="todo"><span class="tx"><b>${esc(x.title)}</b> — ${esc(x.body)}</span>
      <span class="tags">${tagSpan(x.tags)}</span></div>`).join(''):
    '<div class="none">Draft lessons stay here. Nothing is emailed or published.</div>');
  h+=sec('Lessons (this laptop only)',(L||[]).length,lesCap+lessonsInner);
  return h;}

/* ---------------- chatbots page ---------------- */
function chatbotsHtml(){
  return triadWrap(loadSt.sites,'loadSites',()=>chatbotsInner());}
function chatbotsInner(){
  const bots=S.filter(x=>x.category==='chatbot'), cons=S.filter(x=>x.category==='console');
  const row=x=>{
    const dot = x.live===null?'<span class="dot" style="background:var(--dim2)"></span>'
      : x.live?'<span class="dot ok"></span>':'<span class="dot bad"></span>';
    const stTxt=x.live===null?'Unknown':x.live?'Live':'Down';
    const proj = x.repo ? P.find(p=>p.name===x.repo) : null;
    return `<div class="p">${dot}
      <span class="nm">${esc(x.name)}</span>
      <span class="cm" title="${esc(x.what)}">${esc(x.what)}</span>
      <span class="st" style="font-size:var(--shine-text-xs);color:var(--dim2)">${stTxt}</span>
      ${proj?`<span class="a">${esc(proj.age)}</span>`:''}
      ${x.url?`<span class="b"><a href="${esc(x.url)}" target="_blank" rel="noopener"
        style="color:var(--blue);font-size:var(--shine-text-xs)">Open</a></span>`
        :`<span class="a" style="flex-basis:auto;color:var(--dim2)">local project</span>`}
    </div>`;};
  let h=sec('Chatbots & demos',bots.length,bots.length?bots.map(row).join(''):'<div class="none">None registered.</div>');
  h+=sec('Consoles',cons.length,cons.map(row).join(''));
  h+=`<div class="none">Add a site: one entry in <span style="font-family:ui-monospace,Menlo,monospace">brutus/sites.py</span>.</div>`;
  return h;}

/* ---------------- avatar page ----------------
   One surface for "who is the demo and where does it run". Anam holds only
   3 custom avatars, so switching persona/outfit is delete + re-enroll, which
   mints a new id — that is why applying takes a few seconds and ends in a
   redeploy. Saved configs store the INTENT (which master, which tier), never
   the id, because ids die on every swap. */
let AV={faces:[],drafts:[],enrolled:[],env:{},configs:[],tiers:[],transports:[]};
function avatarHtml(){
  return `<section aria-label="Avatar and Anam">${triadWrap(loadSt.avatar,'loadAvatar',()=>avatarInner())}</section>`;}
function avatarInner(){
  if(AV.faces_error||AV.studio_error)
    return `<div class="inline-err" style="display:block">Studio unreachable — avatar control needs it (${esc(AV.faces_error||AV.studio_error)})</div>
      <div class="none"><button type="button" onclick="loadAvatar()">Retry</button></div>`;
  const faces=AV.faces||[];
  const drafts=AV.drafts||[];
  if(!faces.length&&!drafts.length)
    return '<div class="none">No face masters or mflux drafts yet. Run scripts/make-face.sh on a prompt, then reload.</div>';
  const personas=[...new Set(faces.map(f=>f.persona))];
  const sel=AVSEL.persona||personas[0]||'';
  const looks=faces.filter(f=>f.persona===sel);
  const lookSel=AVSEL.look&&looks.some(l=>l.look===AVSEL.look)?AVSEL.look:(looks[0]||{}).look;
  const tier=AVSEL.tier||AV.env.PRIMARY_TIER||'anam';
  const transport=AVSEL.transport||AV.env.TEXT_TRANSPORT||'textbelt';
  const cur=(AV.enrolled||[]).find(a=>a.id===AV.env.ANAM_AVATAR_ID);
  const lookOpts=['professional','businesscasual','military','original','formal'];

  let h=sec('Live now',null,`<div class="p">
    <span class="nm">${esc(cur?cur.name:'(id not in the enrolled set)')}</span>
    <span class="cm">serving as the demo face · tier ${esc(AV.env.PRIMARY_TIER||'anam')} · texts via ${esc(AV.env.TEXT_TRANSPORT||'?')}</span>
    <span class="a">${esc((AV.env.ANAM_AVATAR_ID||'').slice(0,8))}</span></div>
    ${(AV.enrolled||[]).map(a=>`<div class="p"><span class="nm">${esc(a.name)}</span>
      <span class="cm">enrolled on Anam${a.id===AV.env.ANAM_AVATAR_ID?' — current default':''}</span>
      <span class="a">${esc(a.id.slice(0,8))}</span></div>`).join('')}
    <div class="none">Anam holds 3 custom avatars at once. Applying a new face replaces one.</div>`);

  /* mflux drafts: generated on the Studio, not yet in faces/looks. Stage copies
     into the masters library; Stage & apply enrolls (delete-to-swap) in one shot. */
  const draftBody=AV.drafts_error
    ? `<div class="inline-err" style="display:block">Could not list mflux drafts (${esc(AV.drafts_error)})</div>`
    : !drafts.length
      ? '<div class="none">No drafts in ~/mflux-out/faces. Generate with <code>scripts/make-face.sh "prompt" name</code>.</div>'
      : drafts.map((d,i)=>{
          const ok=d.anam_ok?'ready for Anam':`${d.width||'?'}×${d.height||'?'} — Anam needs ≥1152`;
          const persona=esc(d.persona_hint||'face');
          const disabled=d.anam_ok?'':' disabled';
          return `<div class="p" data-draft="${esc(d.name)}">
            <span class="nm">${esc(d.name)}</span>
            <span class="cm">${ok}</span>
            <span class="b" style="display:flex;gap:var(--shine-space-1);flex-wrap:wrap;align-items:center">
              <label class="sr-only" for="av-draft-p-${i}">Persona</label>
              <input id="av-draft-p-${i}" value="${persona}" placeholder="persona" style="width:7rem" aria-label="Persona for ${esc(d.name)}">
              <label class="sr-only" for="av-draft-l-${i}">Outfit</label>
              <select id="av-draft-l-${i}" aria-label="Outfit for ${esc(d.name)}">
                ${lookOpts.map(l=>`<option value="${l}"${l==='professional'?' selected':''}>${l}</option>`).join('')}
              </select>
              <button type="button"${disabled} onclick="avatarStage(${i},false)">Stage</button>
              <button type="button"${disabled} onclick="avatarStage(${i},true)">Stage &amp; apply</button>
            </span></div>`;
        }).join('');
  h+=sec('mflux drafts',drafts.length,draftBody
    +'<div class="none">Stage copies into faces/looks so Apply can enroll. Anam still caps at 3 — Stage &amp; apply replaces the selected slot.</div>');

  if(faces.length){
  h+=sec('Change the demo',null,`<div style="padding:var(--shine-space-1) var(--shine-space-1)">
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:8px">
      <label class="cm">Person</label>
      <select id="av-persona" onchange="AVSEL.persona=this.value;AVSEL.look=null;render()">
        ${personas.map(p=>`<option value="${esc(p)}"${p===sel?' selected':''}>${esc(p)}</option>`).join('')}
      </select>
      <label class="cm">Outfit</label>
      <select id="av-look" onchange="AVSEL.look=this.value;render()">
        ${looks.map(l=>`<option value="${esc(l.look)}"${l.look===lookSel?' selected':''}>${esc(l.look)}</option>`).join('')}
      </select>
      <label class="cm">Service</label>
      <select id="av-tier" onchange="AVSEL.tier=this.value;render()">
        ${AV.tiers.map(t=>`<option value="${esc(t[0])}"${t[0]===tier?' selected':''}>${esc(t[1])}</option>`).join('')}
      </select>
      <label class="cm">Texts</label>
      <select id="av-transport" onchange="AVSEL.transport=this.value;render()">
        ${AV.transports.map(t=>`<option value="${esc(t[0])}"${t[0]===transport?' selected':''}>${esc(t[1])}</option>`).join('')}
      </select>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <label class="cm">Replace</label>
      <select id="av-replace">
        ${(AV.enrolled||[]).map(a=>`<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('')}
      </select>
      <button type="button" onclick="avatarApply()">Apply &amp; redeploy</button>
      <label for="av-name" class="sr-only">Name this avatar config</label>
      <input id="av-name" placeholder="name this config" style="min-width:150px">
      <button type="button" onclick="avatarSave()">Save config</button>
    </div>
    <div id="av-out" class="cm" style="margin-top:8px;white-space:pre-wrap"></div>
    <div id="avatar-err" class="inline-err"></div>
  </div>`);
  } else {
    h+='<div id="av-out" class="cm" style="margin:8px 2px;white-space:pre-wrap"></div><div id="avatar-err" class="inline-err"></div>';
  }

  h+=sec('Saved configs',(AV.configs||[]).length,(AV.configs||[]).length
    ? AV.configs.map(c=>`<div class="p">
        <span class="nm">${esc(c.name)}</span>
        <span class="cm">${esc(c.face)} · ${esc(c.tier||'anam')} · ${esc(c.transport||'')}</span>
        <span class="b"><button type="button" class="linkish" onclick="avatarApplySaved('${esc(c.name)}')">Apply</button>
        &nbsp;<button type="button" class="linkish dim" onclick="avatarDelete('${esc(c.name)}')">Delete</button></span>
      </div>`).join('')
    : '<div class="none">No saved configs yet. Pick a person, outfit and service, then Save.</div>');
  return h;}

const NL=String.fromCharCode(10);  /* see note in avatarApply: no escapes in this template */
let AVSEL={persona:null,look:null,tier:null,transport:null};
function avFace(){const p=AVSEL.persona||(AV.faces[0]||{}).persona;
  const l=AVSEL.look||((AV.faces.find(f=>f.persona===p)||{}).look);
  const hit=AV.faces.find(f=>f.persona===p&&f.look===l);return hit?hit.face:null;}
async function avatarApply(cfg){
  const run=async()=>{
    const out=el('av-out');const face=cfg?cfg.face:avFace();
    const body={face,tier:cfg?cfg.tier:(el('av-tier')||{}).value,
      transport:cfg?cfg.transport:(el('av-transport')||{}).value,
      replace_id:(el('av-replace')||{}).value||null};
    out.textContent='Applying — enrolling on Anam, updating env, redeploying…';
    clearInlineError('avatar-err');
    try{const r=await fetch('/api/avatar/apply',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)});const d=await r.json();
      out.textContent=(d.steps||[]).map(s=>`${s.ok?'ok':'FAILED'}  ${s.step}: ${s.detail||''}`).join(NL)
        +NL+(d.ok?'Live in ~1 minute once the deploy finishes.':'Some steps failed — nothing partially applied is hidden above.');
      toast(d.ok?'Avatar applied':'Apply had failures',!d.ok);loadAvatar();
    }catch(e){out.textContent='Failed: '+e.message;showInlineError('avatar-err',e.message);}};
  confirmAction({title:'Apply and redeploy?',body:'This enrolls on Anam and redeploys the demo face. It takes a few seconds.',
    confirmLabel:'Apply',danger:true,onConfirm:run});}
function avatarApplySaved(name){const c=(AV.configs||[]).find(x=>x.name===name);if(c)avatarApply(c);}
async function avatarStage(idx,alsoApply){
  const d=(AV.drafts||[])[idx]; if(!d) return;
  const persona=(el('av-draft-p-'+idx)||{}).value||d.persona_hint||'';
  const look=(el('av-draft-l-'+idx)||{}).value||'professional';
  const body={draft:d.name,persona,look,enroll:!!alsoApply,
    replace_id:(el('av-replace')||{}).value||null,
    tier:(el('av-tier')||{}).value||AV.env.PRIMARY_TIER||'anam',
    transport:(el('av-transport')||{}).value||AV.env.TEXT_TRANSPORT||'textbelt'};
  const run=async()=>{
    const out=el('av-out');
    out.textContent=alsoApply
      ?'Staging into faces/looks, then enrolling on Anam…'
      :'Staging into faces/looks…';
    clearInlineError('avatar-err');
    try{
      const r=await fetch('/api/avatar/stage',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify(body)});
      const res=await r.json();
      if(!alsoApply){
        out.textContent=res.ok
          ?(`Staged as ${res.face}${res.dims?' ('+res.dims+')':''}. Pick it under Change the demo, or Stage & apply next time.`)
          :('Stage failed: '+(res.error||'unknown'));
        toast(res.ok?'Draft staged':'Stage failed',!res.ok);
      }else{
        const steps=res.steps||[];
        out.textContent=steps.map(s=>`${s.ok?'ok':'FAILED'}  ${s.step}: ${s.detail||''}`).join(NL)
          +NL+(res.ok?'Live in ~1 minute once the deploy finishes.':'Some steps failed — see above.');
        toast(res.ok?'Staged and applied':'Stage/apply had failures',!res.ok);
      }
      loadAvatar();
    }catch(e){out.textContent='Failed: '+e.message;showInlineError('avatar-err',e.message);}
  };
  if(alsoApply){
    confirmAction({title:'Stage and enroll?',
      body:'Copies the mflux PNG into faces/looks, deletes the selected Anam slot, enrolls the new face, and redeploys.',
      confirmLabel:'Stage & apply',danger:true,onConfirm:run});
  }else{run();}
}
async function avatarSave(){const n=(el('av-name')||{}).value||'';
  if(!n.trim()){el('av-out').textContent='Give the config a name first.';return;}
  clearInlineError('avatar-err');
  try{await fetch('/api/avatar/configs',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:n,face:avFace(),tier:(el('av-tier')||{}).value,
      transport:(el('av-transport')||{}).value})});
    el('av-out').textContent='Saved.';toast('Config saved');loadAvatar();}
  catch(e){showInlineError('avatar-err',e.message);}}
async function avatarDelete(name){
  confirmAction({title:'Delete saved config?',body:'This removes the saved preset. It does not change the live demo.',
    confirmLabel:'Delete',danger:true,onConfirm:async()=>{
      clearInlineError('avatar-err');
      try{await fetch('/api/avatar/configs/'+encodeURIComponent(name),{method:'DELETE'});
        toast('Config deleted');loadAvatar();}
      catch(e){showInlineError('avatar-err',e.message);}}});}

/* ---------------- demo maker page ---------------- */
function demoMakerHtml(){
  return `<section aria-label="Demo Maker">${triadWrap(loadSt.sites,'loadSites',()=>demoMakerInner())}</section>`;}
function demoMakerInner(){
  const site=(S||[]).find(x=>x.name&&x.name.indexOf('Voicemaker')>=0)||{};
  const lib=(S||[]).find(x=>x.name&&x.name.indexOf('Clearspeed Demos')>=0)||{};
  const dot=x=>x.live===null?'<span class="dot" style="background:var(--dim2)"></span>'
    :x.live===false?'<span class="dot bad"></span>':'<span class="dot ok"></span>';
  const stTxt=x=>x.live===null?'Unknown':x.live===false?'Down':'Live';
  return sec('Fowler Demo Maker',null,`
    <div class="p">${dot(site)}<span class="nm">Studio</span>
      <span class="cm">Build multi-voice demo videos; publish sends them to the library</span>
      <span class="st" style="font-size:var(--shine-text-xs);color:var(--dim2)">${stTxt(site)}</span>
      <span class="b">${site.url?`<a href="${esc(site.url)}" target="_blank" rel="noopener"
        style="color:var(--blue);font-size:var(--shine-text-xs)">Open</a>`:''}</span></div>
    <div class="p">${dot(lib)}<span class="nm">Published library</span>
      <span class="cm">Where a published demo lands — public site</span>
      <span class="st" style="font-size:var(--shine-text-xs);color:var(--dim2)">${stTxt(lib)}</span>
      <span class="b">${lib.url?`<a href="${esc(lib.url)}" target="_blank" rel="noopener"
        style="color:var(--blue);font-size:var(--shine-text-xs)">Open</a>`:''}</span></div>
    <div class="none">Runs on the Studio (tailnet-only). Publishing auto-syncs to clearspeeddemos.com.</div>`);}

/* ---------------- render ---------------- */
// The page is fully rebuilt on every board SSE / loader refresh. Without
// preserving the values of any inputs inside #page, text the user is actively
// typing into answer fields or the notes capture box is erased. Save before
// rebuild, restore after.
function savePageInputs(){
  const out={};
  document.querySelectorAll('#page input, #page textarea').forEach(inp=>{
    if(inp.id) out[inp.id]={v:inp.value,s:inp.selectionStart,e:inp.selectionEnd,f:inp===document.activeElement};
  });
  return out;
}
function restorePageInputs(saved){
  Object.entries(saved||{}).forEach(([id,state])=>{
    const inp=el(id);
    if(!inp) return;
    inp.value=state.v;
    if(state.f){
      inp.focus();
      if(inp.setSelectionRange && state.s!==null) inp.setSelectionRange(state.s,state.e);
    }
  });
}
function render(){
  const saved=savePageInputs();
  setNav();
  el('n-nucleus').textContent=((N.summary||{}).projects_needing_you||(N.summary||{}).projects_at_risk)||'';
  el('n-work').textContent=(B.counts&&(B.counts.needs_you+B.stuck_total))||'';
  el('n-agents').textContent=(AGcounts&&AGcounts.total)||'';
  el('n-projects').textContent=P.filter(p=>p.at_risk).length||'';
  el('n-notes').textContent=T.filter(t=>t.status!=='done').length||'';
  el('n-canon').textContent=(Canon.inbox||[]).length||'';
  el('n-chatbots').textContent=S.filter(x=>x.live===false).length||'';
  const syncN=(id,v)=>{const n=el(id);if(n)n.textContent=v;};
  syncN('mob-n-chatbots',el('n-chatbots').textContent);
  syncN('mob-n-projects',el('n-projects').textContent);
  syncN('mob-n-canon',el('n-canon').textContent);
  syncN('mob-n-work',el('n-work').textContent);

  el('head').textContent = page==='nucleus' ? 'Command center — what needs your attention now' :
    page==='work' ? (B.headline||'') :
    page==='canon' ? 'Inbox — capture, today, review' :
    page==='chatbots' ? 'Your chatbots & sites' :
    page==='avatar' ? 'Avatar — who the demo is, and where it runs' :
    page==='demomaker' ? 'Fowler Demo Maker — voices into the demo library' :
    page==='agents' ? 'Agents — Codex, Cursor & Claude threads on this laptop' :
    page==='studio' ? 'Studio runs' :
    page==='projects' ? 'Your projects, straight from git' :
    'Notes — capture now, sort later';
  const s=[];
  if(B.linear_ok===false)s.push('<span class="bad">Linear unreachable</span>');
  else s.push('<span class="ok">Connected</span>');
  if(page==='work'&&B.hidden)s.push(B.hidden+' self-tests hidden');
  if(B.generated_at)s.push('checked '+new Date(B.generated_at).toLocaleTimeString());
  el('sub').innerHTML=page==='studio'?esc('Studio · '+(ST.connection==='live'?'Observed ':'Last observation ')+studioTime(ST.collected_at,ST.timezone)+' · Updates every minute'):s.join(' &nbsp;·&nbsp; ');

  const a=B.alarm||{},ab=el('alarm');
  if(a.alarm){
    const txt=alarmText(a);
    ab.innerHTML=`${esc(txt.short)}
      <button type="button" class="linkish alarm-act"
        onclick="checkNow()">Check now</button>`;
    ab.style.display='block';
  }else ab.style.display='none';

  el('page').innerHTML = page==='nucleus'?nucleusHtml():page==='work'?workHtml():page==='canon'?canonHtml():
    page==='chatbots'?chatbotsHtml():page==='avatar'?avatarHtml():
    page==='demomaker'?demoMakerHtml():
    page==='studio'?studioHtml():page==='agents'?agentsHtml():
    page==='projects'?projectsHtml():notesHtml();
  restorePageInputs(saved);
  markKanbanClipped();
}
// A sideways-scrolling board must say so — Blocked and Done sat off-screen with
// no affordance at all when the chat panel was expanded.
function markKanbanClipped(){
  const w=el('kanban-wrap'),k=el('kanban');
  if(!w||!k)return;
  w.classList.toggle('clipped',k.scrollWidth>k.clientWidth+2);
}

/* ---------------- bots rail ---------------- */
function botsHtml(h){
  const bots=[
    {who:'Cursor', ok:!!(h&&h.brain&&h.brain.cursor_enabled), note:'reasoning brain'},
    {who:'Linear', ok:B.linear_ok!==false, note:'work source'},
    {who:'Brutus', ok:!!h, note:'local capture'},
  ];
  const rows=bots.map(b=>`<div class="bot">
    <span class="dot ${b.ok?'ok':'bad'}"></span><span class="who">${esc(b.who)}</span>
    <span class="st">${b.ok?esc(b.note):'down'}</span></div>`).join('');
  // Collapsed form for expanded-chat mode: still names anything down, in words,
  // so bot health is never reduced to an invisible or colour-only signal.
  const down=bots.filter(b=>!b.ok);
  const sum=`<div class="bot-sum"><span class="dot ${down.length?'bad':'ok'}"></span>
    <span>${down.length
      ? esc(down.map(b=>b.who).join(', '))+(down.length===1?' is down':' are down')
      : 'All '+bots.length+' bots up'}</span></div>`;
  const html=sum+rows;
  el('bots').innerHTML=html;
  const mb=el('mob-bots');if(mb)mb.innerHTML=rows;}

/* ---------------- actions ---------------- */
window.go=go;window.openMobSheet=openMobSheet;window.closeMobSheets=closeMobSheets;
window.goMob=goMob;window.goFromMore=goFromMore;
window.toggle=r=>{open.has(r)?open.delete(r):open.add(r);render();};
window.answer=async(t,preset)=>{if(busy)return;busy=true;clearInlineError('work-err');
  let sticky='';
  try{const inp=el('i-'+t);
    if(!inp&&!preset){sticky=`${t}: answer box missing — reload the page.`;
      showInlineError('work-err',sticky);toast(sticky,true);return;}
    const body=(preset||(inp&&inp.value)||'').trim();
    if(!body){toast('Type an answer first.',true);return;}
    const d=await post('/api/answer_input',{ticket_id:t,body});
    const why=(d.dispatch_error||d.error||'').trim();
    if(d.ok===false){
      sticky=`${t}: not saved — ${why||'rejected'}`;
      toast(sticky,true);
    }else if(d.resumed===false){
      // Note is on Atlas5; resume needs a retained work order. Not a silent success.
      sticky=why
        ? `${t}: answer saved. Bot did not resume — ${why}`
        : `${t}: answer saved. Bot did not resume (no work order / already idle).`;
      toast(sticky,true);
    }else{
      toast(d.redropped||d.recovered
        ? `${t}: answered — work order re-dropped, bot queued.`
        : `${t}: answered — bot resumed.`);
      if(inp)inp.value='';
    }
    await loadBoard();
    if(sticky)showInlineError('work-err',sticky);
  }catch(e){sticky=`${t}: ${e.message}`;showInlineError('work-err',sticky);toast(sticky,true);}
  finally{busy=false;}};
async function doDecide(ids,no){if(busy)return;busy=true;clearInlineError('work-err');
  let ok=0,bad=0,err='';
  for(const i of ids){try{await post('/api/approve/'+i,{reject:!!no});ok++;}
    catch(e){bad++;err=e.message;}}
  if(bad)showInlineError('work-err',err||`${bad} failed`);
  else toast(`${no?'Rejected':'Approved'} ${ok}`);
  busy=false;await loadBoard();}
window.decide=async(ids,no,actionId)=>{
  const n=(ids||[]).length||1;
  const act=(B.actions||[]).find(a=>a.id===actionId)||{};
  const why=(act.why||act.what||'').trim();
  const title=(act.title||'').trim();
  const blast=no
    ? `Reject parks ${n} ticket${n===1?'':'s'} on this Justin gate. Bots will not continue them.`
    : `Approve clears the Justin gate for ${n} ticket${n===1?'':'s'}. Bots may pick them up again.`;
  const body=[title,why,blast].filter(Boolean).join(NL+NL);
  confirmAction({
    title:no?`Reject ${n}?`:`Approve ${n}?`,
    body,
    confirmLabel:no?`Reject ${n}`:`Approve ${n}`,
    danger:!!no,
    onConfirm:()=>doDecide(ids,!!no),
  });
};
window.frontierApply=async(paths)=>{
  const list=(paths||[]).filter(Boolean);
  const n=list.length||1;
  confirmAction({
    title:`Send ${n} frontier item${n===1?'':'s'} to the bots?`,
    body:'Applies the same next step to every path in this batch.',
    confirmLabel:`Send ${n}`,
    onConfirm:async()=>{
      if(busy)return;busy=true;clearInlineError('work-err');
      let ok=0,err='';
      try{
        for(const p of list){
          await post('/api/frontier/apply',{path:p,next_action:'investigate',notes:'batch from Brutus Work'});
          ok++;
        }
        toast(`Sent ${ok}`);
      }catch(e){err=e.message;showInlineError('work-err',err);}
      finally{busy=false;await loadBoard();}
    },
  });
};
window.cursorApply=async(paths)=>{
  const list=(paths||[]).filter(Boolean);
  confirmAction({
    title:'Mark Cursor work applied?',
    body:`Tells Studio these ${list.length||1} Cursor job path(s) are done.`,
    confirmLabel:'Mark applied',
    onConfirm:async()=>{
      if(busy)return;busy=true;clearInlineError('work-err');
      let ok=0;
      try{
        for(const p of list){
          await post('/api/cursor/apply',{path:p,notes:'marked from Brutus Work'});
          ok++;
        }
        toast(`Applied ${ok}`);
      }catch(e){showInlineError('work-err',e.message);}
      finally{busy=false;await loadBoard();}
    },
  });
};
window.loadBrief=async(force)=>{
  if(Brief&&Brief.ok!==false&&!force){briefOpen=true;render();return;}
  briefOpen=true;
  try{
    const r=await fetch('/api/brief',{cache:'no-store'});
    Brief=await r.json();
  }catch(e){Brief={ok:false,error:e.message,markdown:''};}
  render();
};
async function doRestart(ids){if(busy)return;busy=true;clearInlineError('work-err');
  try{const d=await post('/api/requeue_stale',{thread_ids:ids});toast(`Restarted ${d.count||0}.`);}
  catch(e){showInlineError('work-err',e.message);}finally{busy=false;await loadBoard();}}
window.restart=async ids=>{
  confirmAction({title:'Start over?',body:'This sends the work back to the bots from the beginning.',
    confirmLabel:'Start over',danger:true,onConfirm:()=>doRestart(ids)});};
async function doSteerRestart(tix){if(busy)return;busy=true;clearInlineError('work-err');
  try{const d=await post('/api/steer_retriage',{ticket_ids:tix});
    const bad=(d.failed||[]).length;
    if(bad)showInlineError('work-err',`${bad} failed`);
    else toast(`Restarted ${d.count||0} with grounding.`);}
  catch(e){showInlineError('work-err',e.message);}finally{busy=false;await loadBoard();}}
window.steerRestart=async tix=>{
  confirmAction({title:`Start all ${tix.length} over?`,body:'These tickets get fresh grounding from the bots.',
    confirmLabel:'Start all over',danger:true,onConfirm:()=>doSteerRestart(tix)});};
window.checkNow=async()=>{toast('Checking…');try{await post('/api/watchdog/tick')}catch(e){}
  await loadBoard();};
window.toggleProbes=async()=>{probes=!probes;await loadBoard();};
window.uncap=async(ticket,action)=>{
  confirmAction({
    title:`Reset attempts on ${ticket}?`,
    body:'This clears the retry cap and asks the bot to pick the ticket up again. Only do this on purpose.',
    confirmLabel:'Reset attempts',
    danger:true,
    onConfirm:async()=>{
      if(busy)return;busy=true;clearInlineError('work-err');
      try{
        const d=await post('/api/capped_attempts/reset',{
          ticket_id:ticket,action:action||'investigate',confirm:true,resume:true,
          reason:'brutus UI operator uncap',
        });
        if(d.ok)toast(`${ticket}: attempts reset`+(d.resumed?' · resumed':''));
        else showInlineError('work-err',d.error||'reset failed');
      }catch(e){showInlineError('work-err',e.message);}
      finally{busy=false;await loadBoard();}
    },
  });
};

async function captureCanon(){
  const i=el('canoncap');const raw=(i&&i.value||'').trim();
  if(!raw)return;clearInlineError('canon-err');
  try{await post('/api/canon/inbox',{raw_capture:raw,source:'brutus:ui'});
    if(i)i.value='';toast('Captured');await loadCanon();}
  catch(e){showInlineError('canon-err',e.message);}}
async function promoteCanon(id){
  const item=(Canon.inbox||[]).find(x=>x.id===id);
  const title=((item&&item.raw_capture)||'Inbox item').split(NL)[0].slice(0,80);
  clearInlineError('canon-err');
  try{await post('/api/canon/inbox/'+id+'/promote',{title,description:(item&&item.raw_capture)||''});
    toast('Started');await loadCanon();}
  catch(e){showInlineError('canon-err',e.message);}}
async function reviewCanon(id){
  clearInlineError('canon-err');
  try{await post('/api/canon/work/'+id+'/review',{action:'accept',reason:''});
    toast('Accepted');await loadCanon();}
  catch(e){showInlineError('canon-err',e.message);}}
async function loadCanon(){
  const wasOk=loadSt.canon.s==='ok';
  if(!wasOk){loadSt.canon.s='loading';if(page==='canon')render();}
  try{const r=await fetch('/api/canon',{cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);
    Canon=await r.json();loadSt.canon.s='ok';loadSt.canon.err='';
  }catch(e){loadSt.canon.s='error';loadSt.canon.err=e.message||'Cannot reach Inbox.';}
  render();}

/* notes */
window.addTodo=async()=>{const i=el('newtodo');const text=(i&&i.value||'').trim();
  const g=el('newtags');const tags=(g&&g.value||'').trim();
  if(!text)return;clearInlineError('notes-err');
  try{await post('/api/todos',{text,tags});i.value='';g.value='';toast('Note added');await loadTodos();}
  catch(e){showInlineError('notes-err',e.message);}};
window.setTodo=async(id,status)=>{clearInlineError('notes-err');
  try{await post('/api/todos/'+id,{status},'PATCH');await loadTodos();}
  catch(e){showInlineError('notes-err',e.message);}};
window.setLane=async(id,lane)=>{clearInlineError('notes-err');
  try{await post('/api/todos/'+id,{lane},'PATCH');await loadTodos();}
  catch(e){showInlineError('notes-err',e.message);}};
window.editTags=id=>{const t=T.find(x=>x.id===id);if(!t)return;promptTags(id,t.tags||'');};
async function doEditTags(id,tags){clearInlineError('notes-err');
  try{await post('/api/todos/'+id,{tags:tags},'PATCH');toast('Tags updated');await loadTodos();}
  catch(e){showInlineError('notes-err',e.message);}}
async function doDelTodo(id){clearInlineError('notes-err');
  try{await fetch('/api/todos/'+id,{method:'DELETE'});toast('Note deleted');await loadTodos();}
  catch(e){showInlineError('notes-err',e.message);}}
window.delTodo=async id=>{
  confirmAction({title:'Delete this note?',body:'This cannot be undone.',confirmLabel:'Delete',danger:true,
    onConfirm:()=>doDelTodo(id)});};
window.promote=async id=>{clearInlineError('notes-err');
  try{const d=await post('/api/todos/'+id+'/promote');
    toast('Now tracked as '+(d.ticket||'a ticket')+' — the bots will triage it.');await loadTodos();}
  catch(e){showInlineError('notes-err',e.message);}};
window.addLesson=async()=>{const i=el('newles');const text=(i&&i.value||'').trim();
  const g=el('newlestags');const tags=(g&&g.value||'').trim();
  if(!text)return;clearInlineError('lessons-err');
  try{await post('/api/lessons',{body:text,title:text.slice(0,80),tags});
    i.value='';if(g)g.value='';toast('Lesson saved');await loadLessons();}
  catch(e){showInlineError('lessons-err',e.message);}};

/* chat */
// escape first, then allow exactly one formatting nicety: **bold**
function fmt(t){return esc(t).replace(/\\*\\*([^*]+)\\*\\*/g,'<b>$1</b>');}
function drawChat(){el('msgs').innerHTML=C.map(m=>
    `<div class="m ${m.k}">${m.w?`<span class="who">${esc(m.w)}</span>`:''}${fmt(m.t)}</div>`).join('');
  el('msgs').scrollTop=el('msgs').scrollHeight;}
function addMsg(kind,text,who){C.push({k:kind,t:text,w:who});drawChat();}
function popMsg(){C.pop();drawChat();}
function applyChatSize(){
  // Conversation is a deliberate full-screen workspace. It never competes with
  // the actual work surface for a few hundred pixels in a side rail.
  const big=!isMobile()&&localStorage.getItem('brutus.chatbig')==='1';
  document.body.classList.toggle('chatbig',big);
  const dock=el('chatdock');
  if(dock){
    if(big){dock.setAttribute('role','dialog');dock.setAttribute('aria-modal','true');}
    else{dock.removeAttribute('role');dock.removeAttribute('aria-modal');}
  }
  const b=el('bigbtn'); if(b)b.textContent=isMobile()?'Chat':(big?'Close':'Open');
}
function hasSpeechRec(){
  return !!(window.SpeechRecognition||window.webkitSpeechRecognition);
}
function applyVoiceBtns(){
  const live=el('livebtn'), sp=el('speakbtn'), box=el('chatbox');
  if(!live||!sp)return;
  const canLive=voice.enabled&&(hasSpeechRec()||voice.whisper);
  live.disabled=!canLive;
  sp.disabled=!voice.enabled||!voice.tts;
  live.className='icon'+(liveOn?' rec':'');
  live.textContent=liveOn?'Live · on':'Live';
  sp.className='icon'+(speakOn&&voice.tts?' on':'');
  sp.textContent=speakOn?'Speak · on':'Speak';
  if(box){
    let label='Talk to Brutus';
    if(liveOn&&!chatBusy&&!speakingOut){box.placeholder='Listening… just talk';label='Listening — speak your message';}
    else if(liveOn&&speakingOut){box.placeholder='Brutus speaking…';label='Brutus is speaking';}
    else if(liveOn&&chatBusy){box.placeholder='Thinking…';label='Brutus is thinking';}
    else box.placeholder='Talk to Brutus…';
    box.setAttribute('aria-label',label);
  }
}
window.openCommandChat=()=>{
  if(isMobile()){openMobSheet('chat');drawChat();return;}
  localStorage.setItem('brutus.chatbig','1');applyChatSize();drawChat();
  openModal(el('chatdock'),el('chatbox'));
};
window.bigChat=()=>{
  if(isMobile()){openMobSheet('chat');drawChat();return;}
  const big=localStorage.getItem('brutus.chatbig')==='1';
  localStorage.setItem('brutus.chatbig',big?'0':'1');
  applyChatSize();drawChat();
  if(big)closeModal(el('chatdock'));else openModal(el('chatdock'),el('chatbox'));
  el('chatbox').focus();};
window.toggleSpeak=()=>{
  if(!voice.tts){toast('Speaking is not set up yet',true);return;}
  speakOn=!speakOn;localStorage.setItem('brutus.speak',speakOn?'1':'0');
  applyVoiceBtns();
  if(!speakOn&&speakAudio){speakAudio.pause();speakAudio=null;speakingOut=false;}
};
async function speakReply(text){
  if(!speakOn||!voice.tts||!text)return;
  speakingOut=true;applyVoiceBtns();
  pauseLiveListen();
  try{
    const r=await fetch('/api/speak',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text})});
    if(!r.ok){let d={};try{d=await r.json()}catch(e){}
      throw new Error(d.detail||('HTTP '+r.status));}
    const blob=await r.blob();
    const url=URL.createObjectURL(blob);
    if(speakAudio){speakAudio.pause();URL.revokeObjectURL(speakAudio._url||'');}
    speakAudio=new Audio(url);speakAudio._url=url;
    await new Promise(resolve=>{
      speakAudio.onended=()=>{URL.revokeObjectURL(url);resolve();};
      speakAudio.onerror=()=>{URL.revokeObjectURL(url);resolve();};
      speakAudio.play().catch(()=>resolve());
    });
  }catch(e){toast('Could not speak: '+e.message,true);}
  finally{
    speakingOut=false;applyVoiceBtns();
    if(liveOn) resumeLiveListen();
  }
}
function pauseLiveListen(){
  if(recognition){try{recognition.stop();}catch(e){}}
  if(vadRec&&vadRec.state==='recording'){try{vadRec.requestData();}catch(e){}}
}
function resumeLiveListen(){
  if(!liveOn||chatBusy||speakingOut)return;
  if(recognition){
    clearTimeout(liveRestartTimer);
    liveRestartTimer=setTimeout(()=>{
      if(!liveOn||chatBusy||speakingOut)return;
      try{recognition.start();}catch(e){}
    },250);
  }
}
window.toggleLive=async()=>{
  if(liveOn){stopLive();return;}
  if(!voice.enabled){toast('Voice is not enabled',true);return;}
  // Live always speaks replies back — that's the point.
  if(voice.tts){speakOn=true;localStorage.setItem('brutus.speak','1');}
  liveOn=true;applyVoiceBtns();
  try{
    if(hasSpeechRec()) startLiveSpeechRec();
    else if(voice.whisper) await startLiveVad();
    else throw new Error('No live speech support in this browser');
    toast('Live on — just talk');
  }catch(e){liveOn=false;applyVoiceBtns();toast('Live failed: '+e.message,true);}
};
function stopLive(){
  liveOn=false;
  clearTimeout(liveRestartTimer);
  if(recognition){
    try{recognition.onend=null;recognition.onerror=null;recognition.onresult=null;recognition.stop();}catch(e){}
    recognition=null;
  }
  stopLiveVad();
  applyVoiceBtns();
  toast('Live off');
}
function startLiveSpeechRec(){
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  recognition=new SR();
  recognition.continuous=true;
  recognition.interimResults=true;
  recognition.lang='en-US';
  recognition.onresult=async(ev)=>{
    if(!liveOn||chatBusy||speakingOut)return;
    let interim='', final='';
    for(let i=ev.resultIndex;i<ev.results.length;i++){
      const t=ev.results[i][0].transcript;
      if(ev.results[i].isFinal) final+=t;
      else interim+=t;
    }
    if(interim) el('chatbox').value=interim.trim();
    if(!final.trim())return;
    el('chatbox').value=final.trim();
    pauseLiveListen();
    await sendChat();
  };
  recognition.onerror=(ev)=>{
    if(ev.error==='aborted'||ev.error==='no-speech')return;
    if(ev.error==='not-allowed'){toast('Mic permission blocked',true);stopLive();return;}
  };
  recognition.onend=()=>{
    // Browser ends the session periodically — keep live going.
    if(liveOn&&!chatBusy&&!speakingOut) resumeLiveListen();
  };
  recognition.start();
}
async function startLiveVad(){
  // Continuous listen with silence detection → Whisper. No Stop button.
  vadStream=await navigator.mediaDevices.getUserMedia({audio:true});
  vadCtx=new (window.AudioContext||window.webkitAudioContext)();
  const src=vadCtx.createMediaStreamSource(vadStream);
  vadAnalyser=vadCtx.createAnalyser();
  vadAnalyser.fftSize=2048;
  src.connect(vadAnalyser);
  const mime=MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ?'audio/webm;codecs=opus'
    :(MediaRecorder.isTypeSupported('audio/webm')?'audio/webm':'');
  const buf=new Uint8Array(vadAnalyser.fftSize);
  const tick=()=>{
    if(!liveOn)return;
    vadRaf=requestAnimationFrame(tick);
    if(chatBusy||speakingOut)return;
    vadAnalyser.getByteTimeDomainData(buf);
    let sum=0;for(let i=0;i<buf.length;i++){const v=(buf[i]-128)/128;sum+=v*v;}
    const rms=Math.sqrt(sum/buf.length);
    const loud=rms>0.04;
    if(loud){
      vadSilentMs=0;
      if(!vadSpeaking){
        vadSpeaking=true;vadChunks=[];
        try{
          vadRec=mime?new MediaRecorder(vadStream,{mimeType:mime}):new MediaRecorder(vadStream);
          vadRec.ondataavailable=e=>{if(e.data&&e.data.size)vadChunks.push(e.data);};
          vadRec.start(250);
        }catch(e){vadSpeaking=false;}
      }
    }else if(vadSpeaking){
      vadSilentMs+=16;
      if(vadSilentMs>900&&vadRec&&vadRec.state==='recording'){
        const rec=vadRec;
        vadRec=null;vadSpeaking=false;vadSilentMs=0;
        rec.onstop=async()=>{
          const blob=new Blob(vadChunks,{type:rec.mimeType||'audio/webm'});
          vadChunks=[];
          if(blob.size>1200) await voiceSendLive(blob);
        };
        try{rec.stop();}catch(e){}
      }
    }
  };
  vadRaf=requestAnimationFrame(tick);
}
function stopLiveVad(){
  if(vadRaf) cancelAnimationFrame(vadRaf);vadRaf=0;
  try{if(vadRec&&vadRec.state==='recording')vadRec.stop();}catch(e){}
  vadRec=null;vadSpeaking=false;vadChunks=[];
  if(vadStream){vadStream.getTracks().forEach(t=>t.stop());vadStream=null;}
  if(vadCtx){try{vadCtx.close();}catch(e){} vadCtx=null;}
  vadAnalyser=null;
}
async function voiceSendLive(blob){
  if(!liveOn||chatBusy||speakingOut)return;
  chatBusy=true;applyVoiceBtns();pauseLiveListen();
  addMsg('sys','…');
  try{
    const fd=new FormData();
    fd.append('file',blob,'clip.webm');
    const r=await fetch('/api/transcribe',{method:'POST',body:fd});
    let d={};try{d=await r.json()}catch(e){}
    if(!r.ok)throw new Error(d.detail||('HTTP '+r.status));
    popMsg();
    const text=(d.text||'').trim();
    if(!text){chatBusy=false;applyVoiceBtns();if(liveOn)resumeLiveListen();return;}
    el('chatbox').value=text;
    chatBusy=false;applyVoiceBtns();
    await sendChat();
  }catch(e){popMsg();addMsg('sys','Live failed: '+e.message);
    chatBusy=false;applyVoiceBtns();if(liveOn)resumeLiveListen();}
}
async function ensureOperatorSession(){
  if(operatorSession){const r=await fetch('/api/session/'+encodeURIComponent(operatorSession),{cache:'no-store'});if(r.ok)return operatorSession;}
  const d=await post('/api/session/open',{title:'Brutus command rail',kind:'operator'});
  operatorSession=d.session_id;localStorage.setItem('brutus.operator.session',operatorSession);return operatorSession;
}
window.sendChat=async()=>{
  if(chatBusy)return;
  const i=el('chatbox');const msg=(i.value||'').trim();if(!msg)return;
  i.value='';addMsg('me',msg);chatBusy=true;applyVoiceBtns();pauseLiveListen();addMsg('sys','thinking…');
  try{
    const sid=await ensureOperatorSession();
    let r=await fetch('/api/session/'+encodeURIComponent(sid)+'/say',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({message:msg,channel:liveOn?'voice':'text',wait:true})});
    if(r.status===404){operatorSession='';localStorage.removeItem('brutus.operator.session');const fresh=await ensureOperatorSession();
      r=await fetch('/api/session/'+encodeURIComponent(fresh)+'/say',{method:'POST',headers:{'content-type':'application/json'},
        body:JSON.stringify({message:msg,channel:liveOn?'voice':'text',wait:true})});}
    let d={};try{d=await r.json()}catch(_e){}if(!r.ok)throw new Error(d.detail||('HTTP '+r.status));
    popMsg();
    const reply=d.reply||'(no reply)';
    addMsg('bot',reply,'Brutus · '+(d.lane||'conversation'));
    await speakReply(reply);
  }catch(e){popMsg();addMsg('sys','Chat failed: '+e.message);}
  finally{chatBusy=false;applyVoiceBtns();if(liveOn&&!speakingOut) resumeLiveListen();}};

/* ---------------- loaders ---------------- */
async function loadNucleus(force=false){
  const wasOk=loadSt.nucleus.s==='ok';
  if(!wasOk){loadSt.nucleus.s='loading';if(page==='nucleus')render();}
  try{const r=await fetch('/api/nucleus'+(force?'?force=true':''),{cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);N=await r.json();loadSt.nucleus.s='ok';loadSt.nucleus.err='';
    if(!NSEL&&(N.projects||[]).length)NSEL=N.projects[0].id;
  }catch(e){loadSt.nucleus.s='error';loadSt.nucleus.err=e.message||'Cannot build the operating graph.';}render();}
async function loadBoard(){
  const wasOk=loadSt.board.s==='ok';
  if(!wasOk){loadSt.board.s='loading';render();}
  try{
    const r=await fetch('/api/board'+(probes?'?include_probes=true':''),{cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);
    B=await r.json();B.counts=B.counts||{needs_you:0,working:0,queued:0,stuck:0};
    loadSt.board.s='ok';loadSt.board.err='';
    try{
      const cr=await fetch('/api/capped_attempts',{cache:'no-store'});
      if(cr.ok){const cd=await cr.json();Capped=cd.rows||[];}
      else Capped=[];
    }catch(_e){Capped=[];}
    // Alarm lives in the banner only. Re-pasting it into chat every time the
    // quiet-key flipped trained Justin to ignore both channels.
    if(!(B.alarm||{}).alarm)alarmChatKey='';
    if(Brief===null)loadBrief(false);
  }catch(e){loadSt.board.s='error';loadSt.board.err=e.message||'Cannot reach Brutus.';
    B=Object.assign({headline:'Cannot reach Brutus.',studio_ok:false,counts:{},
      needs_you:[],working:[],queued:[],stuck:[],stuck_total:0,actions:[]},B);Capped=[];}render();}
async function loadProjects(){
  const wasOk=loadSt.projects.s==='ok';
  if(!wasOk){loadSt.projects.s='loading';if(page==='projects')render();}
  try{const r=await fetch('/api/projects',{cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);
    P=(await r.json()).projects||[];loadSt.projects.s='ok';loadSt.projects.err='';
  }catch(e){loadSt.projects.s='error';loadSt.projects.err=e.message;}
  render();}
async function loadAgents(){
  const wasOk=loadSt.agents.s==='ok';
  if(!wasOk){loadSt.agents.s='loading';if(page==='agents')render();}
  try{
    const q=new URLSearchParams();
    if(AGfilt.surface)q.set('surface',AGfilt.surface);
    if(AGfilt.q)q.set('q',AGfilt.q);
    if(AGfilt.showHidden)q.set('include_hidden','true');
    const r=await fetch('/api/agents?'+q.toString(),{cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);
    const d=await r.json();AG=d.agents||[];AGcounts=d.counts||{};
    loadSt.agents.s='ok';loadSt.agents.err='';
  }catch(e){loadSt.agents.s='error';loadSt.agents.err=e.message;}
  render();}
async function loadTodos(){
  const wasOk=loadSt.todos.s==='ok';
  if(!wasOk){loadSt.todos.s='loading';if(page==='notes')render();}
  try{const r=await fetch('/api/todos',{cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);
    T=(await r.json()).todos||[];loadSt.todos.s='ok';loadSt.todos.err='';
  }catch(e){loadSt.todos.s='error';loadSt.todos.err=e.message;}
  render();}
async function loadLessons(){
  const wasOk=loadSt.lessons.s==='ok';
  if(!wasOk){loadSt.lessons.s='loading';if(page==='notes')render();}
  try{const r=await fetch('/api/lessons',{cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);
    L=(await r.json()).lessons||[];loadSt.lessons.s='ok';loadSt.lessons.err='';
  }catch(e){loadSt.lessons.s='error';loadSt.lessons.err=e.message;}
  render();}
async function loadSites(){
  if(loadSt.sites.s!=='ok'){loadSt.sites.s='loading';
    if(page==='chatbots'||page==='demomaker')render();}
  try{const r=await fetch('/api/sites',{cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);
    S=(await r.json()).sites||[];loadSt.sites.s='ok';loadSt.sites.err='';
  }catch(e){loadSt.sites.s='error';loadSt.sites.err=e.message;}
  render();}
async function loadAvatar(){
  if(loadSt.avatar.s!=='ok'){loadSt.avatar.s='loading';if(page==='avatar')render();}
  try{const r=await fetch('/api/avatar',{cache:'no-store'});
    if(!r.ok)throw new Error('HTTP '+r.status);
    AV=await r.json();loadSt.avatar.s='ok';loadSt.avatar.err='';
  }catch(e){loadSt.avatar.s='error';loadSt.avatar.err=e.message;}
  render();}
async function loadHealth(){try{
  const r=await fetch('/api/healthz',{cache:'no-store'});
  const d=await r.json();botsHtml(d);
  if(d&&d.voice){voice=d.voice;applyVoiceBtns();}
}catch(e){botsHtml(null);}}
async function loadVoice(){try{
  const r=await fetch('/api/voice',{cache:'no-store'});voice=await r.json();
}catch(e){voice={enabled:false,whisper:false,tts:false};}
  applyVoiceBtns();}

(function(){
  const btn=el('theme-toggle');
  const apply=t=>{
    const theme=(t==='light')?'light':'dark';
    document.documentElement.dataset.theme=theme;
    try{localStorage.setItem('brutus.theme',theme);}catch(_e){}
    if(btn){
      btn.setAttribute('aria-pressed',theme==='light'?'true':'false');
      const lab=btn.querySelector('.label');
      if(lab) lab.textContent=theme==='light'?'Light':'Dark';
    }
  };
  let cur='dark';
  try{cur=localStorage.getItem('brutus.theme')||'dark';}catch(_e){}
  apply(cur);
  if(btn) btn.onclick=()=>apply(document.documentElement.dataset.theme==='light'?'dark':'light');
})();
el('confirm-cancel').onclick=closeConfirm;
el('confirm-ok').onclick=async()=>{const fn=confirmPending;if(confirmKeepOpen){if(fn)await fn();return;}
  closeConfirm();if(fn)await fn();};
el('confirm-input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing){
  e.preventDefault();el('confirm-ok').click();}});
el('confirm-toggle').onclick=()=>{const inp=el('confirm-input'),show=inp.type==='password';
  inp.type=show?'text':'password';el('confirm-toggle').textContent=show?'Hide token':'Show token';inp.focus();};
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){
    if(el('confirm-overlay').classList.contains('open'))closeConfirm();
    else closeMobSheets();
  }
});
window.addEventListener('resize',()=>{applyChatSize();placeChatdock();markKanbanClipped();});

// Land on a URL that names the page, so Back and bookmarks both work.
if(!pageFromHash())location.replace('#/'+page);
placeChatdock();applyChatSize();applyVoiceBtns();drawChat();
/* Board + ideas: SSE, same reserved-bus pattern as session.js initBoard. */
(function(){
  const boardEs=new EventSource('/api/session/board/events');
  boardEs.onmessage=(e)=>{try{const ev=JSON.parse(e.data);if(ev.kind==='board')loadBoard();}catch(_e){}};
  const ideasEs=new EventSource('/api/session/ideas/events');
  ideasEs.onmessage=(e)=>{try{const ev=JSON.parse(e.data);if(!ev.kind||ev.kind==='idea')loadTodos();}catch(_e){}};
})();
loadStudio();
setInterval(()=>{if(page==='studio')loadStudio();},60000);
loadNucleus();loadBoard();loadAgents();loadProjects();loadTodos();loadLessons();loadHealth();loadSites();loadAvatar();loadVoice();loadCanon();
setInterval(loadNucleus,60000);setInterval(loadAgents,45000);setInterval(loadProjects,90000);setInterval(loadHealth,30000);setInterval(loadSites,60000);setInterval(loadCanon,15000);
</script>
</body>
</html>
"""

BRUTUS_HTML = BRUTUS_HTML.replace("/* ---------------- navigation ---------------- */", STUDIO_JS + "\n/* ---------------- navigation ---------------- */")
