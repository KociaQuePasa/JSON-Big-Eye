"use strict";

const PAGE = 100;
const nf = new Intl.NumberFormat();

const $ = (id) => document.getElementById(id);
const get = async (url) => {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
};

// `jbe serve`: talk to the FastAPI server over HTTP.
const httpBackend = {
  meta: () => get("/api/meta"),
  node: (id) => get(`/api/node/${id}`),
  children: (id, offset, limit) => get(`/api/node/${id}/children?offset=${offset}&limit=${limit}`),
  ancestors: (id) => get(`/api/node/${id}/ancestors`),
  subtree: (id, maxNodes) => get(`/api/node/${id}/json?max_nodes=${maxNodes}`),
  search: (q, limit) => get(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`),
};

// `jbe open`: the page runs inside a pywebview window and calls Python directly (no port).
const desktopReady = new Promise((resolve) => {
  if (!window.JBE_DESKTOP) return resolve(null);
  if (window.pywebview?.api) return resolve(window.pywebview.api);
  window.addEventListener("pywebviewready", () => resolve(window.pywebview.api), { once: true });
});
let backend = httpBackend;

// nodeId -> record from the API (only for nodes currently in the graph)
const records = new Map();
// expanded nodeId -> offset of the child window currently shown
const expanded = new Map();
let meta = null;
let selectedId = null;

// ---------- labels & styling ----------

function keyLabel(rec, parentType) {
  if (rec.parent_id === null) return "$";
  return parentType === "array" ? `[${rec.idx}]` : rec.key;
}

function valueLabel(rec) {
  switch (rec.type) {
    case "object": return `{${nf.format(rec.child_count)}}`;
    case "array": return `[${nf.format(rec.child_count)}]`;
    case "string": {
      const s = rec.preview.length > 40 ? rec.preview.slice(0, 40) + "…" : rec.preview;
      return JSON.stringify(s);
    }
    default: return rec.preview;
  }
}

function label(rec, parentType) {
  const k = keyLabel(rec, parentType);
  const k2 = k.length > 32 ? k.slice(0, 32) + "…" : k;
  return `${k2}: ${valueLabel(rec)}`;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function graphStyle() {
  const types = ["object", "array", "string", "number", "bool", "null"];
  return [
    {
      selector: "node",
      style: {
        shape: "round-rectangle",
        label: "data(label)",
        "font-family": cssVar("--mono"),
        "font-size": 12,
        color: cssVar("--text"),
        "text-valign": "center",
        "text-halign": "center",
        width: "data(w)",
        height: 26,
        "background-color": cssVar("--node-bg"),
        "border-width": 1.5,
        "border-color": cssVar("--edge"),
      },
    },
    ...types.map((t) => ({
      selector: `node[type = "${t}"]`,
      style: { "border-color": cssVar(`--t-${t}`), color: t === "object" || t === "array" ? cssVar("--text") : cssVar(`--t-${t}`) },
    })),
    { selector: "node[?container]", style: { "border-width": 2 } },
    { selector: "node[?open]", style: { "background-color": cssVar("--bg") } },
    {
      selector: "node[?pager]",
      style: { "border-style": "dashed", "border-color": cssVar("--t-pager"), color: cssVar("--t-pager"), "font-style": "italic" },
    },
    { selector: "node:selected", style: { "border-width": 3, "border-color": cssVar("--accent"), "overlay-opacity": 0 } },
    {
      selector: "edge",
      style: {
        width: 1.2,
        "line-color": cssVar("--edge"),
        "curve-style": "taxi",
        "taxi-direction": "rightward",
        "taxi-turn": 24,
      },
    },
  ];
}

const cy = cytoscape({
  container: $("cy"),
  style: graphStyle(),
  wheelSensitivity: 0.25,
  minZoom: 0.05,
  maxZoom: 3,
  boxSelectionEnabled: false,
});
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => cy.style(graphStyle()));

// ---------- graph mutation ----------

const nid = (id) => `n${id}`;

function addRecord(rec, parentType) {
  records.set(rec.id, rec);
  const text = label(rec, parentType);
  cy.add({
    group: "nodes",
    data: {
      id: nid(rec.id),
      nodeId: rec.id,
      type: rec.type,
      label: text,
      w: Math.max(40, text.length * 7.3 + 22),
      container: rec.child_count > 0,
    },
  });
  if (rec.parent_id !== null && cy.$id(nid(rec.parent_id)).nonempty()) {
    cy.add({ group: "edges", data: { id: `e${rec.id}`, source: nid(rec.parent_id), target: nid(rec.id) } });
  }
}

function addPager(parent, offset, dir) {
  const count = parent.child_count;
  const text = dir === "prev"
    ? `◀ ${nf.format(Math.max(0, offset))}–${nf.format(offset + PAGE - 1)}`
    : `${nf.format(offset)}–${nf.format(Math.min(count, offset + PAGE) - 1)} ▶  (of ${nf.format(count)})`;
  const id = `p${parent.id}${dir}`;
  cy.add({ group: "nodes", data: { id, pager: true, parentId: parent.id, offset, label: text, w: text.length * 7.3 + 22 } });
  cy.add({ group: "edges", data: { id: `e${id}`, source: nid(parent.id), target: id } });
}

/** Remove everything below a node from the graph. */
function removeDescendants(id) {
  const removed = cy.$id(nid(id)).successors();
  removed.nodes().forEach((n) => {
    const d = n.data("nodeId");
    if (d !== undefined) { records.delete(d); expanded.delete(d); }
  });
  removed.remove();
}

/** Show the window of children of `id` starting at `offset` (replaces any previous window). */
async function expand(id, offset = 0) {
  const parent = records.get(id);
  offset = Math.max(0, Math.min(offset, Math.max(0, parent.child_count - 1)));
  const res = await backend.children(id, offset, PAGE);
  cy.batch(() => {
    removeDescendants(id);
    if (offset > 0) addPager(parent, Math.max(0, offset - PAGE), "prev");
    for (const c of res.children) addRecord(c, parent.type);
    if (offset + PAGE < parent.child_count) addPager(parent, offset + PAGE, "next");
    cy.$id(nid(id)).data("open", true);
  });
  expanded.set(id, offset);
}

function collapse(id) {
  cy.batch(() => {
    removeDescendants(id);
    cy.$id(nid(id)).data("open", false);
  });
  expanded.delete(id);
}

/** Re-run the tree layout, keeping `anchor` at the same spot on screen. */
function relayout(anchorId) {
  const anchor = anchorId === undefined ? null : cy.$id(anchorId);
  const before = anchor && anchor.nonempty() ? { ...anchor.renderedPosition() } : null;
  cy.layout({ name: "dagre", rankDir: "LR", nodeSep: 6, rankSep: 50, animate: false, fit: false }).run();
  if (before) {
    const after = anchor.renderedPosition();
    cy.panBy({ x: before.x - after.x, y: before.y - after.y });
  }
  updateStatus();
}

// ---------- details panel ----------

async function showDetails(id) {
  selectedId = id;
  setHash(`#node=${id}`);
  const rec = records.get(id);
  $("hint").hidden = true;
  $("details").hidden = false;
  $("d-type").textContent = rec.type;
  $("d-count").textContent = rec.type === "object" || rec.type === "array" ? nf.format(rec.child_count) : "—";
  $("d-depth").textContent = rec.depth;
  $("d-path").textContent = "…";
  $("d-value").textContent = "…";
  $("d-trunc").hidden = true;
  $("goto").hidden = rec.child_count <= PAGE;
  $("goto-idx").max = Math.max(0, rec.child_count - 1);
  $("goto-idx").value = expanded.get(id) ?? 0;

  const [node, sub] = await Promise.all([
    backend.node(id),
    backend.subtree(id, 500),
  ]);
  if (selectedId !== id) return;
  $("d-path").textContent = node.path;
  $("d-value").textContent = typeof sub.value === "string" ? sub.value : JSON.stringify(sub.value, null, 2);
  $("d-trunc").hidden = !sub.truncated && !(rec.type === "string" && rec.preview.length >= 1000);
}

// Deep links only make sense when served over HTTP; some webviews forbid replaceState.
function setHash(hash) {
  try { history.replaceState(null, "", hash); } catch { /* ignore */ }
}

function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  // Fallback for webviews without the async clipboard API.
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.append(ta);
  ta.select();
  document.execCommand("copy");
  ta.remove();
}

$("copy-path").addEventListener("click", () => copyText($("d-path").textContent));

$("goto").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (selectedId === null) return;
  const idx = Number($("goto-idx").value) || 0;
  await expand(selectedId, idx);
  relayout(nid(selectedId));
});

// ---------- interaction ----------

cy.on("tap", "node", async (evt) => {
  const n = evt.target;
  if (n.data("pager")) {
    const parentId = n.data("parentId");
    await expand(parentId, n.data("offset"));
    relayout(nid(parentId));
    return;
  }
  const id = n.data("nodeId");
  showDetails(id);
  if (!n.data("container")) return;
  if (expanded.has(id)) collapse(id);
  else await expand(id, 0);
  relayout(n.id());
});

/** Expand every ancestor of `id` (paging to the right window) and focus it. */
async function reveal(id) {
  const { ancestors } = await backend.ancestors(id);
  for (let i = 0; i < ancestors.length - 1; i++) {
    const a = ancestors[i];
    const next = ancestors[i + 1];
    if (!records.has(a.id)) addRecord(a);
    const win = expanded.get(a.id);
    if (win === undefined || next.idx < win || next.idx >= win + PAGE) {
      await expand(a.id, Math.floor(next.idx / PAGE) * PAGE);
    }
  }
  relayout();
  const target = cy.$id(nid(id));
  cy.$(":selected").unselect();
  target.select();
  cy.zoom(Math.max(cy.zoom(), 0.9));
  cy.center(target);
  showDetails(id);
}

// ---------- search ----------

let searchTimer = null;
let searchSeq = 0;
const results = $("results");

$("search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(runSearch, 250);
});
$("search").addEventListener("keydown", (e) => {
  const items = [...results.querySelectorAll("li[data-id]")];
  const cur = items.findIndex((li) => li.classList.contains("active"));
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const nxt = e.key === "ArrowDown" ? Math.min(items.length - 1, cur + 1) : Math.max(0, cur - 1);
    items.forEach((li, i) => li.classList.toggle("active", i === nxt));
    items[nxt]?.scrollIntoView({ block: "nearest" });
  } else if (e.key === "Enter") {
    (items[cur] ?? items[0])?.click();
  } else if (e.key === "Escape") {
    results.hidden = true;
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement.tagName !== "INPUT") {
    e.preventDefault();
    $("search").focus();
  }
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search")) results.hidden = true;
});

async function runSearch() {
  const q = $("search").value.trim();
  const seq = ++searchSeq;
  if (!q) { results.hidden = true; return; }
  const { hits } = await backend.search(q, 100);
  if (seq !== searchSeq) return;
  results.replaceChildren();
  if (!hits.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "No matches";
    results.append(li);
  }
  for (const h of hits) {
    const li = document.createElement("li");
    li.dataset.id = h.id;
    const p = document.createElement("div");
    p.className = "r-path";
    p.textContent = h.path;
    const v = document.createElement("div");
    v.className = "r-val";
    v.textContent = h.preview ?? (h.type === "array" ? `[${nf.format(h.child_count)}]` : `{${nf.format(h.child_count)}}`);
    li.append(p, v);
    li.addEventListener("click", () => {
      results.hidden = true;
      reveal(h.id);
    });
    results.append(li);
  }
  results.hidden = false;
}

// ---------- toolbar & status ----------

$("fit").addEventListener("click", () => cy.fit(undefined, 30));
$("reset").addEventListener("click", async () => {
  setHash("#");
  await init();
});

function updateStatus() {
  if (!meta) return;
  const shown = cy.nodes("[nodeId >= 0]").length;
  $("status").textContent =
    `${nf.format(meta.node_count)} nodes in file · ${nf.format(shown)} shown · ` +
    (meta.fts ? "full-text search on" : "search: substring (no FTS index)");
}

async function init() {
  backend = (await desktopReady) ?? httpBackend;
  meta = await backend.meta();
  $("source").textContent = meta.source;
  $("source").title = meta.source;
  cy.elements().remove();
  records.clear();
  expanded.clear();
  addRecord(meta.root);
  if (meta.root.child_count > 0) await expand(meta.root.id, 0);
  relayout();
  cy.fit(undefined, 30);
  if (cy.zoom() < 0.6 || cy.zoom() > 1) {
    cy.zoom(cy.zoom() > 1 ? 1 : 0.8);
    cy.center(cy.zoom() === 1 ? cy.elements() : cy.$id(nid(meta.root.id)));
  }
  const linked = /node=(\d+)/.exec(location.hash);
  if (linked && Number(linked[1]) !== meta.root.id) await reveal(Number(linked[1]));
  else showDetails(meta.root.id);
}

init().catch((err) => {
  $("status").textContent = `Error: ${err.message}`;
});
