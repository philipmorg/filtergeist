"use strict";

const state = {
  status: null,
  streams: [],
  activeSlug: null,
  cfg: null,
  meta: { cycles: [], open_build: null },
  dirty: false,
  activeTab: "overview",
  outputSubtab: "guide_markdown",
  outputsCache: null,
  whatIfResult: null,
  pollPreview: null,
};

function esc(val) {
  return String(val ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function csvToList(str) {
  return String(str || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function linesToList(str) {
  return String(str || "")
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

function showToast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => { el.hidden = true; }, 2800);
}

function markDirty(isDirty = true) {
  state.dirty = isDirty;
  const pill = document.getElementById("dirty-indicator");
  pill.textContent = isDirty ? "Unsaved edits" : "Saved";
  pill.className = "status-pill " + (isDirty ? "status-dirty" : "status-clean");
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function ensureShape(cfg) {
  cfg.defaults = cfg.defaults || {};
  cfg.outputs = cfg.outputs || {};
  cfg.outputs.formats = {
    markdown: true,
    review_markdown: false,
    text_xml: false,
    podcast_xml: false,
    json: false,
    ...(cfg.outputs.formats || {}),
  };
  cfg.outputs.feed_metadata = cfg.outputs.feed_metadata || {};
  cfg.selection = cfg.selection || { mode: "running_guide", total_items: 10, max_per_domain: 2, max_per_author: 2, min_final_score: 6.0, backfill_relax_delta: 0.8 };
  cfg.scoring = cfg.scoring || {};
  cfg.scoring.axes_weights = cfg.scoring.axes_weights || { audience_fit: 0.45, operator_utility: 0.30, reframe_sharp_thesis: 0.20, evidence_anchoring: 0.05, voice_risk: 0.0 };
  cfg.scoring.slop_penalties = cfg.scoring.slop_penalties || { clean: 0.0, borderline: 0.8, slop: 2.0 };
  cfg.scoring.diversity = cfg.scoring.diversity || { enabled: true, new_domain_bonus: 0.25, last_cycle_domain_penalty: 0.35, last_cycle_author_penalty: 0.35, similarity_threshold: 0.45, similarity_penalty: 1.0 };
  cfg.audience = cfg.audience || { profile: "", core_thesis: "", pillars: [], positive_signals: [], reject_signals: [], triage_min_relevance: 3 };
  cfg.categories = cfg.categories || {};
  cfg.agent_lenses = cfg.agent_lenses || [];
  cfg.podcast_discovery = cfg.podcast_discovery || { enabled: true, results_per_query: 4, include_domains: ["exa.ai/library/podcast"], queries: [] };
  cfg.content_sources = cfg.content_sources || [];
  cfg.apify = cfg.apify || { enabled: false, max_usd_per_run: 1.5, timeout_s: 180, actors: {}, sources: [] };
  cfg.apify.actors = { ...(state.status?.default_actors || {}), ...(cfg.apify.actors || {}) };
  cfg.apify.sources = cfg.apify.sources || [];
  cfg.focus_topics = cfg.focus_topics || [];
  cfg.hype_topics = cfg.hype_topics || [];
  cfg.publisher_penalties = cfg.publisher_penalties || { enabled: true, saas: 0.8, agency: 0.6, general: 0.0, waive_banger_score_10: 9.0, fade_width: 0.8, full_at_signals: 6, exempt_domains: [], min_banger_score_10: { saas: 8.6, agency: 8.5, general: 0.0 }, kind_overrides: {} };
  cfg.publisher_penalties.min_banger_score_10 = cfg.publisher_penalties.min_banger_score_10 || { saas: 8.6, agency: 8.5, general: 0.0 };
  cfg.publisher_penalties.kind_overrides = cfg.publisher_penalties.kind_overrides || {};
  cfg.must_include = cfg.must_include || [];
  cfg.trusted_sources = cfg.trusted_sources || [];
  cfg.exemplars = cfg.exemplars || { urls: [], taste_anchors: [] };
  return cfg;
}

async function loadStream(slug) {
  const data = await api(`/api/streams/${encodeURIComponent(slug)}`);
  state.activeSlug = slug;
  state.cfg = ensureShape(data.stream);
  state.meta = {
    cycles: data.cycles || [],
    open_build: data.open_build,
    has_guide: data.has_guide,
    has_review: data.has_review,
    has_text_xml: data.has_text_xml,
    has_podcast_xml: data.has_podcast_xml,
  };
  state.outputsCache = null;
  state.whatIfResult = null;
  state.pollPreview = null;
  markDirty(false);
  renderStreamDropdown();
  renderActiveTab();
}

function renderStreamDropdown() {
  const sel = document.getElementById("stream-select");
  sel.innerHTML = state.streams
    .map((s) => `<option value="${esc(s.slug)}" ${s.slug === state.activeSlug ? "selected" : ""}>${esc(s.newsletter)} (${esc(s.slug)})</option>`)
    .join("");
}

async function saveCurrentStream() {
  if (!state.cfg || !state.activeSlug) return;
  await api(`/api/streams/${encodeURIComponent(state.activeSlug)}`, {
    method: "PUT",
    body: JSON.stringify({ stream: state.cfg }),
  });
  const listRes = await api("/api/streams");
  state.streams = listRes.streams || [];
  renderStreamDropdown();
  markDirty(false);
  showToast(`Saved streams/${state.activeSlug}.json`);
}

function renderActiveTab() {
  const container = document.getElementById("panel-content");
  if (!state.cfg) {
    container.innerHTML = `<div class="card"><p>Loading stream configuration…</p></div>`;
    return;
  }
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === state.activeTab);
  });

  const renderers = {
    overview: renderOverviewTab,
    sources: renderSourcesTab,
    apify: renderApifyTab,
    rubric: renderRubricTab,
    lenses: renderLensesTab,
    scoring: renderScoringTab,
    simulator: renderSimulatorTab,
    outputs: renderOutputsTab,
  };
  (renderers[state.activeTab] || renderOverviewTab)(container);
}

/* ---------------- TAB 1: OVERVIEW & OUTPUTS ---------------- */
function renderOverviewTab(el) {
  const c = state.cfg;
  const fmts = c.outputs.formats;
  const fmeta = c.outputs.feed_metadata;
  const exaUrl = "https://mcp.exa.ai/mcp?tools=web_search_exa,web_search_advanced_exa,web_fetch_exa,agent_run";
  const apifyNote = c.apify.enabled
    ? `\n# Optional Apify MCP (enabled in this stream):\nclaude mcp add --transport http apify "https://mcp.apify.com" --header "Authorization: Bearer \${APIFY_TOKEN}"`
    : "";

  const formatDefs = [
    ["markdown", "Markdown Guide", "out/<stream>/issue_content.md — Ranked picks, thesis, verbatim quotes, and Items of Note."],
    ["text_xml", "Text RSS XML", "out/<stream>/feed.xml — Full-content RSS 2.0 feed for Feedbin, NetNewsWire, or Reeder."],
    ["podcast_xml", "Podcast RSS XML", "out/<stream>/podcast_feed.xml — iTunes audio RSS with MP3/M4A enclosures for podcast apps."],
    ["review_markdown", "Audit Review Pile", "out/<stream>/review_content.md — Every discovered link with prefilter/triage/gate dispositions."],
    ["json", "Structured Issue JSON", "out/<stream>/issue-YYYY-MM-DD.json — Machine-readable picks & score breakdowns."],
  ];

  el.innerHTML = `
    <div class="card">
      <h2>Publication Identity</h2>
      <p class="help">Stream file: <code>streams/${esc(c.slug)}.json</code> · Output directory: <code>out/${esc(c.slug)}/</code></p>
      <div class="field-grid">
        <label>Publication / Brief Name
          <input type="text" data-bind="newsletter" value="${esc(c.newsletter)}">
        </label>
        <label>Editor / Owner Name
          <input type="text" data-bind="owner" value="${esc(c.owner)}">
        </label>
        <label>Website URL
          <input type="url" data-bind="owner_site" value="${esc(c.owner_site)}">
        </label>
      </div>
      <label>Description
        <input type="text" data-bind="description" value="${esc(c.description)}">
      </label>
    </div>

    <div class="card">
      <h2>Selectable Output Formats</h2>
      <p class="help">Choose which files <code>render-guide</code> writes on every curation pass. Click any card to toggle.</p>
      <div class="format-grid">
        ${formatDefs.map(([key, label, desc]) => `
          <div class="format-card ${fmts[key] ? "enabled" : ""}" data-fmt="${key}" tabindex="0" role="checkbox" aria-checked="${Boolean(fmts[key])}">
            <div class="format-card-top">
              <input type="checkbox" tabindex="-1" ${fmts[key] ? "checked" : ""}>
              <span>${esc(label)}</span>
            </div>
            <p>${esc(desc)}</p>
          </div>
        `).join("")}
      </div>
      <div class="field-grid">
        <label>Text RSS Feed Title
          <input type="text" data-fmeta="title" value="${esc(fmeta.title || "")}" placeholder="${esc(c.newsletter)} — Research Feed">
        </label>
        <label>Podcast RSS Feed Title
          <input type="text" data-fmeta="podcast_title" value="${esc(fmeta.podcast_title || "")}" placeholder="${esc(c.newsletter)} — Listen Queue">
        </label>
        <label>Podcast iTunes Author
          <input type="text" data-fmeta="author" value="${esc(fmeta.author || "")}" placeholder="${esc(c.owner)}">
        </label>
        <label>Feed Language
          <input type="text" data-fmeta="language" value="${esc(fmeta.language || "en-us")}">
        </label>
      </div>
      <label class="inline-check">
        <input type="checkbox" id="chk-resolve-enc" ${fmeta.resolve_enclosures !== false ? "checked" : ""}>
        Resolve direct audio enclosures (<code>.mp3</code> / <code>.m4a</code>) for audio picks in XML feeds
      </label>
    </div>

    <div class="card">
      <h2>Discovery Window &amp; Selection Mode</h2>
      <div class="field-grid">
        <label>Lookback Days
          <input type="number" min="1" max="365" data-def="lookback_days" value="${esc(c.defaults.lookback_days ?? 14)}">
        </label>
        <label>Max Candidates Fetched / Run
          <input type="number" min="10" max="500" data-def="max_fetch" value="${esc(c.defaults.max_fetch ?? 120)}">
        </label>
        <label>Max Full LLM Reviews (<code>max_spines</code>)
          <input type="number" min="5" max="200" data-def="max_spines" value="${esc(c.defaults.max_spines ?? 40)}">
        </label>
        <label>Default Exa Mode
          <select data-def-str="default_exa_mode">
            <option value="deep" ${c.defaults.default_exa_mode === "deep" ? "selected" : ""}>deep (Exa Agent lenses + Search)</option>
            <option value="standard" ${c.defaults.default_exa_mode === "standard" ? "selected" : ""}>standard (Regular Exa Search)</option>
          </select>
        </label>
        <label>Default Exa Agent Effort
          <select data-def-str="default_exa_effort">
            <option value="low" ${c.defaults.default_exa_effort === "low" ? "selected" : ""}>low (~$0.025/lens, recommended)</option>
            <option value="medium" ${c.defaults.default_exa_effort === "medium" ? "selected" : ""}>medium</option>
            <option value="high" ${c.defaults.default_exa_effort === "high" ? "selected" : ""}>high (exhaustive)</option>
          </select>
        </label>
        <label>Guide Selection Rule
          <select id="sel-mode">
            <option value="running_guide" ${c.selection.mode !== "capped_shortlist" ? "selected" : ""}>Running Guide (keep all non-rejected picks)</option>
            <option value="capped_shortlist" ${c.selection.mode === "capped_shortlist" ? "selected" : ""}>Capped Shortlist (enforce quotas &amp; domain caps)</option>
          </select>
        </label>
      </div>
      <div class="field-grid" id="shortlist-fields" ${c.selection.mode === "capped_shortlist" ? "" : "hidden"}>
        <label>Target Total Picks
          <input type="number" min="1" max="50" data-sel="total_items" value="${esc(c.selection.total_items ?? 10)}">
        </label>
        <label>Max Picks per Domain
          <input type="number" min="1" max="20" data-sel="max_per_domain" value="${esc(c.selection.max_per_domain ?? 2)}">
        </label>
        <label>Max Picks per Author
          <input type="number" min="1" max="20" data-sel="max_per_author" value="${esc(c.selection.max_per_author ?? 2)}">
        </label>
        <label>Minimum Final Score Floor
          <input type="number" step="0.1" data-sel-float="min_final_score" value="${esc(c.selection.min_final_score ?? 6.0)}">
        </label>
      </div>
    </div>

    <div class="card">
      <h2>Quick-Start Agent &amp; MCP Commands</h2>
      <p class="help">Connect Exa MCP once, then point Claude Code, Codex, or Cursor at <code>${esc(state.status?.skill_path || "SKILL.md")}</code>.</p>
      <pre class="code-box"># Claude Code
claude mcp add --transport http exa "${exaUrl}&login"${apifyNote}

# Codex CLI
codex mcp add exa --url "${exaUrl}"

# Curate this stream from your coding agent:
"Use the filtergeist skill to curate stream '${esc(c.slug)}' in ${esc(c.defaults.default_exa_mode || "deep")} mode."</pre>
    </div>
  `;

  el.querySelectorAll("[data-bind]").forEach((inp) => {
    inp.addEventListener("input", () => { c[inp.dataset.bind] = inp.value; markDirty(); });
  });
  el.querySelectorAll("[data-fmeta]").forEach((inp) => {
    inp.addEventListener("input", () => { c.outputs.feed_metadata[inp.dataset.fmeta] = inp.value; markDirty(); });
  });
  el.querySelector("#chk-resolve-enc").addEventListener("change", (e) => {
    c.outputs.feed_metadata.resolve_enclosures = e.target.checked;
    markDirty();
  });
  el.querySelectorAll(".format-card").forEach((card) => {
    const toggle = () => {
      const k = card.dataset.fmt;
      c.outputs.formats[k] = !c.outputs.formats[k];
      markDirty();
      renderOverviewTab(el);
    };
    card.addEventListener("click", (e) => { e.preventDefault(); toggle(); });
    card.addEventListener("keydown", (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggle(); } });
  });
  el.querySelectorAll("[data-def]").forEach((inp) => {
    inp.addEventListener("input", () => { c.defaults[inp.dataset.def] = parseInt(inp.value || "0", 10); markDirty(); });
  });
  el.querySelectorAll("[data-def-str]").forEach((sel) => {
    sel.addEventListener("change", () => { c.defaults[sel.dataset.defStr] = sel.value; markDirty(); renderOverviewTab(el); });
  });
  el.querySelector("#sel-mode").addEventListener("change", (e) => {
    c.selection.mode = e.target.value;
    markDirty();
    renderOverviewTab(el);
  });
  el.querySelectorAll("[data-sel]").forEach((inp) => {
    inp.addEventListener("input", () => { c.selection[inp.dataset.sel] = parseInt(inp.value || "1", 10); markDirty(); });
  });
  el.querySelectorAll("[data-sel-float]").forEach((inp) => {
    inp.addEventListener("input", () => { c.selection[inp.dataset.selFloat] = parseFloat(inp.value || "0"); markDirty(); });
  });
}

/* ---------------- TAB 2: RSS & WEB SOURCES ---------------- */
function renderSourcesTab(el) {
  const c = state.cfg;
  const previewHtml = state.pollPreview
    ? `<div class="item-row" style="background:var(--accent-soft);border-color:var(--accent)">
        <strong>Live Poll Result:</strong> ${esc(state.pollPreview.feed_stats.xml_ok)}/${esc(state.pollPreview.feed_stats.total_sources)} XML feeds succeeded ·
        ${esc(state.pollPreview.feed_stats.items_found)} items in window ·
        ${esc(state.pollPreview.exa_fallback_batches.length)} Exa fallback batches.
        <ul style="margin:8px 0 0;padding-left:18px">
          ${state.pollPreview.sample_candidates.slice(0, 6).map((it) => `<li><strong>${esc(it.source)}:</strong> ${esc(it.title)} (${esc(it.words)}w)</li>`).join("")}
        </ul>
      </div>`
    : "";

  el.innerHTML = `
    <div class="card">
      <div class="card-header">
        <div>
          <h2>Curated RSS, Atom &amp; Podcast Feeds (${c.content_sources.length})</h2>
          <p class="help">Polled directly over HTTP first; any feed that blocks XML automatically falls back to domain-scoped Exa search.</p>
        </div>
        <div style="display:flex;gap:8px">
          <button type="button" id="btn-poll-all" class="btn btn-secondary">Poll Enabled Feeds Live</button>
          <button type="button" id="btn-add-feed" class="btn btn-primary">+ Add Feed</button>
        </div>
      </div>
      ${previewHtml}
      <div id="feeds-list">
        ${c.content_sources.map((src, idx) => `
          <div class="item-row" data-feed-idx="${idx}">
            <div class="item-row-head">
              <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
                <label class="inline-check"><input type="checkbox" class="f-en" ${src.enabled !== false ? "checked" : ""}> Enabled</label>
                <label class="inline-check"><input type="checkbox" class="f-owned" ${src.owned_source ? "checked" : ""}> Editor's Own Site (exempt from vendor penalty)</label>
              </div>
              <div style="display:flex;gap:8px;align-items:center">
                <span class="feed-test-res" style="font-size:12px;color:var(--muted)"></span>
                <button type="button" class="btn btn-secondary btn-sm btn-test-feed">Test Feed</button>
                <button type="button" class="btn btn-danger btn-sm btn-del-feed">Remove</button>
              </div>
            </div>
            <div class="field-grid" style="margin-bottom:0">
              <label>Source Name
                <input type="text" class="f-name" value="${esc(src.name || "")}" placeholder="Author or Publication">
              </label>
              <label>Feed XML URL
                <input type="url" class="f-url" value="${esc(src.url || "")}" placeholder="https://example.com/feed.xml">
              </label>
              <label>Primary Domain (for Exa fallback)
                <input type="text" class="f-dom" value="${esc(src.domain || "")}" placeholder="example.com">
              </label>
              <label>Platform &amp; Max Entries
                <div style="display:flex;gap:8px">
                  <select class="f-plat" style="flex:1">
                    <option value="rss" ${src.platform !== "podcast" ? "selected" : ""}>Article RSS</option>
                    <option value="podcast" ${src.platform === "podcast" ? "selected" : ""}>Podcast RSS</option>
                  </select>
                  <input type="number" class="f-max" min="1" max="50" style="width:78px" value="${esc(src.max_entries ?? 10)}">
                </div>
              </label>
            </div>
          </div>
        `).join("")}
      </div>
    </div>

    <div class="card">
      <h2>Allowlists, Exemplar Seeds &amp; Filters</h2>
      <div class="field-grid">
        <label>High-Signal Search Allowlist Domains (one per line)
          <textarea rows="6" id="txt-allowlist" class="mono">${esc((c.query_site_allowlist || []).join("\n"))}</textarea>
        </label>
        <label>Exemplar Seed URLs (taste anchors, one per line)
          <textarea rows="6" id="txt-exemplars" class="mono">${esc((c.exemplars?.urls || []).join("\n"))}</textarea>
        </label>
      </div>
      <div class="field-grid">
        <label>Blocked Domains (<code>deny_domains</code>, one per line)
          <textarea rows="5" id="txt-deny-domains" class="mono">${esc((c.deny_domains || []).join("\n"))}</textarea>
        </label>
        <label>Blocked Topic Terms (<code>topic_deny_terms</code>, comma-separated)
          <textarea rows="5" id="txt-deny-terms">${esc((c.topic_deny_terms || []).join(", "))}</textarea>
        </label>
      </div>
      <label>Prefilter Reject Regex
        <input type="text" id="inp-reject-re" class="mono" value="${esc(c.prefilter_reject_regex || "")}">
      </label>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <h2>Trusted Source Boosts</h2>
          <p class="help">Small score bonus (capped at 0.5) applied only when <code>audience_fit &gt;= 3</code>.</p>
        </div>
        <button type="button" id="btn-add-trusted" class="btn btn-secondary btn-sm">+ Add Trusted Host</button>
      </div>
      ${c.trusted_sources.map((ts, idx) => `
        <div class="field-grid" data-ts-idx="${idx}" style="align-items:end;margin-bottom:8px">
          <label>Host <input type="text" class="ts-host" value="${esc(ts.host)}"></label>
          <label>Label <input type="text" class="ts-label" value="${esc(ts.label)}"></label>
          <label>Boost <input type="number" step="0.05" min="0" max="0.5" class="ts-boost" value="${esc(ts.boost)}"></label>
          <button type="button" class="btn btn-danger btn-sm btn-del-ts">Remove</button>
        </div>
      `).join("")}
    </div>
  `;

  el.querySelector("#btn-add-feed").addEventListener("click", () => {
    c.content_sources.unshift({ name: "", url: "", platform: "rss", domain: "", enabled: true, max_entries: 10 });
    markDirty();
    renderSourcesTab(el);
  });

  el.querySelector("#btn-poll-all").addEventListener("click", async (e) => {
    e.target.disabled = true;
    e.target.textContent = "Polling live feeds…";
    try {
      state.pollPreview = await api("/api/poll-preview", { method: "POST", body: JSON.stringify({ stream: c }) });
      renderSourcesTab(el);
    } catch (err) {
      showToast(`Poll error: ${err.message}`);
      e.target.disabled = false;
      e.target.textContent = "Poll Enabled Feeds Live";
    }
  });

  el.querySelectorAll("[data-feed-idx]").forEach((row) => {
    const idx = parseInt(row.dataset.feedIdx, 10);
    const src = c.content_sources[idx];
    row.querySelector(".f-en").addEventListener("change", (e) => { src.enabled = e.target.checked; markDirty(); });
    row.querySelector(".f-owned").addEventListener("change", (e) => { src.owned_source = e.target.checked; markDirty(); });
    row.querySelector(".f-name").addEventListener("input", (e) => { src.name = e.target.value; markDirty(); });
    row.querySelector(".f-url").addEventListener("input", (e) => { src.url = e.target.value; markDirty(); });
    row.querySelector(".f-dom").addEventListener("input", (e) => { src.domain = e.target.value; markDirty(); });
    row.querySelector(".f-plat").addEventListener("change", (e) => { src.platform = e.target.value; markDirty(); });
    row.querySelector(".f-max").addEventListener("input", (e) => { src.max_entries = parseInt(e.target.value || "10", 10); markDirty(); });
    row.querySelector(".btn-del-feed").addEventListener("click", () => {
      c.content_sources.splice(idx, 1);
      markDirty();
      renderSourcesTab(el);
    });
    row.querySelector(".btn-test-feed").addEventListener("click", async (e) => {
      const badge = row.querySelector(".feed-test-res");
      badge.textContent = "Testing…";
      e.target.disabled = true;
      try {
        const r = await api("/api/test-feed", { method: "POST", body: JSON.stringify({ url: src.url, lookback_days: 30 }) });
        badge.textContent = r.ok ? `OK XML (${r.items_in_window} recent)` : `Fallback -> Exa (${r.reason})`;
        badge.style.color = r.ok ? "var(--ok)" : "var(--warn)";
      } catch (err) {
        badge.textContent = err.message;
        badge.style.color = "var(--err)";
      } finally {
        e.target.disabled = false;
      }
    });
  });

  el.querySelector("#txt-allowlist").addEventListener("input", (e) => { c.query_site_allowlist = linesToList(e.target.value); markDirty(); });
  el.querySelector("#txt-exemplars").addEventListener("input", (e) => { c.exemplars.urls = linesToList(e.target.value); markDirty(); });
  el.querySelector("#txt-deny-domains").addEventListener("input", (e) => { c.deny_domains = linesToList(e.target.value); markDirty(); });
  el.querySelector("#txt-deny-terms").addEventListener("input", (e) => { c.topic_deny_terms = csvToList(e.target.value); markDirty(); });
  el.querySelector("#inp-reject-re").addEventListener("input", (e) => { c.prefilter_reject_regex = e.target.value; markDirty(); });

  el.querySelector("#btn-add-trusted").addEventListener("click", () => {
    c.trusted_sources.push({ host: "", label: "", boost: 0.25 });
    markDirty();
    renderSourcesTab(el);
  });
  el.querySelectorAll("[data-ts-idx]").forEach((row) => {
    const idx = parseInt(row.dataset.tsIdx, 10);
    const ts = c.trusted_sources[idx];
    row.querySelector(".ts-host").addEventListener("input", (e) => { ts.host = e.target.value.trim(); markDirty(); });
    row.querySelector(".ts-label").addEventListener("input", (e) => { ts.label = e.target.value; markDirty(); });
    row.querySelector(".ts-boost").addEventListener("input", (e) => { ts.boost = parseFloat(e.target.value || "0"); markDirty(); });
    row.querySelector(".btn-del-ts").addEventListener("click", () => { c.trusted_sources.splice(idx, 1); markDirty(); renderSourcesTab(el); });
  });
}

/* ---------------- TAB 3: APIFY SOCIAL & VIDEO ---------------- */
function renderApifyTab(el) {
  const ap = state.cfg.apify;
  const tokOk = state.status?.apify_token_present;
  const cats = Object.keys(state.cfg.categories || {});

  el.innerHTML = `
    <div class="card">
      <div class="card-header">
        <div>
          <h2>Optional Apify Social, Video &amp; Web Discovery</h2>
          <p class="help">Completely opt-in. When unchecked (or when <code>--noapify</code> / <code>FILTERGEIST_NOAPIFY=1</code> is set), zero Apify calls occur.</p>
        </div>
        <span class="status-pill ${tokOk ? "status-clean" : "status-dirty"}">
          ${tokOk ? "APIFY_TOKEN detected in env" : "APIFY_TOKEN not set (MCP plan mode works)"}
        </span>
      </div>
      <div class="field-grid">
        <label class="inline-check">
          <input type="checkbox" id="ap-enabled" ${ap.enabled ? "checked" : ""}>
          <strong>Enable Apify lane for this stream</strong>
        </label>
        <label>Hard Spend Cap per Run (USD)
          <input type="number" step="0.25" min="0.1" max="50" id="ap-max-usd" value="${esc(ap.max_usd_per_run ?? 1.5)}">
        </label>
        <label>Actor Timeout (seconds)
          <input type="number" min="30" max="600" id="ap-timeout" value="${esc(ap.timeout_s ?? 180)}">
        </label>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <h2>Configured Apify Sources (${ap.sources.length})</h2>
          <p class="help">Supports LinkedIn profiles/searches, X/Twitter handles &amp; lists, YouTube channels, and JS-rendered pages.</p>
        </div>
        <button type="button" id="btn-add-apify" class="btn btn-primary">+ Add Apify Source</button>
      </div>
      ${ap.sources.map((src, idx) => `
        <div class="item-row" data-ap-idx="${idx}">
          <div class="item-row-head">
            <label class="inline-check"><input type="checkbox" class="aps-en" ${src.enabled !== false ? "checked" : ""}> Enabled</label>
            <button type="button" class="btn btn-danger btn-sm btn-del-ap">Remove</button>
          </div>
          <div class="field-grid" style="margin-bottom:0">
            <label>Label
              <input type="text" class="aps-name" value="${esc(src.name || "")}" placeholder="e.g. @mitchellh on X">
            </label>
            <label>Source Kind
              <select class="aps-kind">
                ${["x_profiles", "x_lists", "linkedin_profiles", "linkedin_search", "youtube_channel", "web_crawler"].map((k) =>
                  `<option value="${k}" ${src.kind === k ? "selected" : ""}>${k}</option>`
                ).join("")}
              </select>
            </label>
            <label>Target (handle, query, or URL)
              <input type="text" class="aps-target" value="${esc(src.target || "")}">
            </label>
            <label>Category Hint &amp; Max Items
              <div style="display:flex;gap:8px">
                <select class="aps-cat" style="flex:1">
                  <option value="">(auto)</option>
                  ${cats.map((c) => `<option value="${esc(c)}" ${src.category_hint === c ? "selected" : ""}>${esc(c)}</option>`).join("")}
                </select>
                <input type="number" class="aps-max" min="1" max="50" style="width:74px" value="${esc(src.max_items ?? 10)}">
              </div>
            </label>
          </div>
        </div>
      `).join("")}
    </div>

    <div class="card">
      <h2>Apify Actor Registry</h2>
      <p class="help">Override default Apify Store actor slugs if your team uses custom actors.</p>
      <div class="field-grid">
        ${Object.entries(ap.actors || {}).map(([k, val]) => `
          <label>${esc(k)}
            <input type="text" class="mono" data-actor="${esc(k)}" value="${esc(val)}">
          </label>
        `).join("")}
      </div>
    </div>
  `;

  el.querySelector("#ap-enabled").addEventListener("change", (e) => { ap.enabled = e.target.checked; markDirty(); });
  el.querySelector("#ap-max-usd").addEventListener("input", (e) => { ap.max_usd_per_run = parseFloat(e.target.value || "1.5"); markDirty(); });
  el.querySelector("#ap-timeout").addEventListener("input", (e) => { ap.timeout_s = parseInt(e.target.value || "180", 10); markDirty(); });
  el.querySelector("#btn-add-apify").addEventListener("click", () => {
    ap.sources.push({ name: "", kind: "x_profiles", target: "", max_items: 10, enabled: true, category_hint: cats[0] || "" });
    markDirty();
    renderApifyTab(el);
  });
  el.querySelectorAll("[data-ap-idx]").forEach((row) => {
    const idx = parseInt(row.dataset.apIdx, 10);
    const s = ap.sources[idx];
    row.querySelector(".aps-en").addEventListener("change", (e) => { s.enabled = e.target.checked; markDirty(); });
    row.querySelector(".aps-name").addEventListener("input", (e) => { s.name = e.target.value; markDirty(); });
    row.querySelector(".aps-kind").addEventListener("change", (e) => { s.kind = e.target.value; markDirty(); });
    row.querySelector(".aps-target").addEventListener("input", (e) => { s.target = e.target.value; markDirty(); });
    row.querySelector(".aps-cat").addEventListener("change", (e) => { s.category_hint = e.target.value || null; markDirty(); });
    row.querySelector(".aps-max").addEventListener("input", (e) => { s.max_items = parseInt(e.target.value || "10", 10); markDirty(); });
    row.querySelector(".btn-del-ap").addEventListener("click", () => { ap.sources.splice(idx, 1); markDirty(); renderApifyTab(el); });
  });
  el.querySelectorAll("[data-actor]").forEach((inp) => {
    inp.addEventListener("input", () => { ap.actors[inp.dataset.actor] = inp.value.trim(); markDirty(); });
  });
}

/* ---------------- TAB 4: EDITORIAL RUBRIC ---------------- */
function renderRubricTab(el) {
  const aud = state.cfg.audience;
  const defs = state.cfg.defaults;

  el.innerHTML = `
    <div class="card">
      <h2>Reader Profile &amp; Core Editorial Thesis</h2>
      <p class="help">Guides relevance screening, Exa Agent evaluation, and full article reviews (<code>spine</code>).</p>
      <label style="margin-bottom:14px">Audience Profile
        <textarea rows="4" id="aud-profile">${esc(aud.profile || "")}</textarea>
      </label>
      <label style="margin-bottom:14px">Core Editorial Thesis
        <textarea rows="3" id="aud-thesis">${esc(aud.core_thesis || "")}</textarea>
      </label>
      <label>Editorial Pillars (one per line)
        <textarea rows="5" id="aud-pillars">${esc((aud.pillars || []).join("\n"))}</textarea>
      </label>
    </div>

    <div class="card">
      <h2>Admit vs. Hard-Reject Signals</h2>
      <div class="field-grid">
        <label>Strong Positive Signals (one per line)
          <textarea rows="7" id="aud-pos">${esc((aud.positive_signals || []).join("\n"))}</textarea>
        </label>
        <label>Hard Reject &amp; Slop Signals (one per line)
          <textarea rows="7" id="aud-rej">${esc((aud.reject_signals || []).join("\n"))}</textarea>
        </label>
      </div>
      <div class="field-grid">
        <label>Quick Screen Minimum Relevance (<code>0–4</code>)
          <input type="number" min="1" max="4" id="aud-triage" value="${esc(aud.triage_min_relevance ?? 3)}">
        </label>
        <label>False-Negative Exploration Probe Rate (<code>0.0–0.5</code>)
          <input type="number" step="0.05" min="0" max="0.5" id="aud-probe" value="${esc(defs.exploration_probe_rate ?? 0.1)}">
        </label>
      </div>
    </div>
  `;

  el.querySelector("#aud-profile").addEventListener("input", (e) => { aud.profile = e.target.value; markDirty(); });
  el.querySelector("#aud-thesis").addEventListener("input", (e) => { aud.core_thesis = e.target.value; markDirty(); });
  el.querySelector("#aud-pillars").addEventListener("input", (e) => { aud.pillars = linesToList(e.target.value); markDirty(); });
  el.querySelector("#aud-pos").addEventListener("input", (e) => { aud.positive_signals = linesToList(e.target.value); markDirty(); });
  el.querySelector("#aud-rej").addEventListener("input", (e) => { aud.reject_signals = linesToList(e.target.value); markDirty(); });
  el.querySelector("#aud-triage").addEventListener("input", (e) => { aud.triage_min_relevance = parseInt(e.target.value || "3", 10); markDirty(); });
  el.querySelector("#aud-probe").addEventListener("input", (e) => { defs.exploration_probe_rate = parseFloat(e.target.value || "0.1"); markDirty(); });
}

/* ---------------- TAB 5: CATEGORIES & EXA LENSES ---------------- */
function renderLensesTab(el) {
  const c = state.cfg;
  const catEntries = Object.entries(c.categories || {});
  const exaCats = ["", "personal site", "research paper", "news", "company", "github", "pdf", "financial report"];

  el.innerHTML = `
    <div class="card">
      <div class="card-header">
        <div>
          <h2>Canonical Categories &amp; Search Lanes (${catEntries.length})</h2>
          <p class="help">Section headings in <code>issue_content.md</code> plus category search queries for Exa MCP.</p>
        </div>
        <button type="button" id="btn-add-cat" class="btn btn-primary">+ Add Category</button>
      </div>
      ${catEntries.map(([key, cfg]) => `
        <div class="item-row" data-cat-key="${esc(key)}">
          <div class="item-row-head">
            <strong><code>${esc(key)}</code></strong>
            <button type="button" class="btn btn-danger btn-sm btn-del-cat">Remove</button>
          </div>
          <div class="field-grid">
            <label>Heading Label
              <input type="text" class="cat-label" value="${esc(cfg.label || "")}">
            </label>
            <label>Exa MCP Category Filter
              <select class="cat-exacat">
                ${exaCats.map((ec) => `<option value="${esc(ec)}" ${(cfg.exa_mcp_category || "") === ec ? "selected" : ""}>${ec || "(none / general web & blogs)"}</option>`).join("")}
              </select>
            </label>
            <label>Shortlist Min / Max
              <div style="display:flex;gap:8px">
                <input type="number" min="0" max="20" class="cat-min" value="${esc(cfg.min ?? 0)}">
                <input type="number" min="1" max="30" class="cat-max" value="${esc(cfg.max ?? 4)}">
              </div>
            </label>
            <label>Exclude Phrases (comma-separated)
              <input type="text" class="cat-excl" value="${esc((cfg.exclude_text || []).join(", "))}">
            </label>
          </div>
          <label style="margin-bottom:8px">Primary Exa Query
            <input type="text" class="cat-q" value="${esc(cfg.query || "")}">
          </label>
          <label>Additional Query Phrasings (one per line)
            <textarea rows="2" class="cat-addq">${esc((cfg.additional_queries || []).join("\n"))}</textarea>
          </label>
        </div>
      `).join("")}
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <h2>Exa Agent Lenses (<code>agent_run</code> in Deep Mode)</h2>
          <p class="help">Multi-step research scouts with native quality gates applied to fetched page text. Use <code>&lt;LOOKBACK_DAYS&gt;</code> as a placeholder.</p>
        </div>
        <button type="button" id="btn-add-lens" class="btn btn-secondary btn-sm">+ Add Agent Lens</button>
      </div>
      ${c.agent_lenses.map((ln, idx) => `
        <div class="item-row" data-lens-idx="${idx}">
          <div class="item-row-head">
            <label class="inline-check"><input type="checkbox" class="ln-en" ${ln.enabled !== false ? "checked" : ""}> <strong><code>exa_agent_${esc(ln.key)}</code></strong></label>
            <button type="button" class="btn btn-danger btn-sm btn-del-lens">Remove</button>
          </div>
          <div class="field-grid">
            <label>Lens Key <input type="text" class="ln-key" value="${esc(ln.key)}"></label>
            <label>Category Hint <input type="text" class="ln-cat" value="${esc(ln.category_hint || "")}"></label>
            <label>Max Items <input type="number" min="1" max="15" class="ln-max" value="${esc(ln.max_items ?? 8)}"></label>
            <label>Native Gate Floor (/10) <input type="number" step="0.1" min="0" max="10" class="ln-gate" value="${esc(ln.min_gate_score_10 ?? 7.5)}"></label>
          </div>
          <label style="margin-bottom:8px">Native Gate Rubric (judged on fetched source text)
            <input type="text" class="ln-rubric" value="${esc(ln.gate_rubric || "")}">
          </label>
          <label>Exa Agent Prompt
            <textarea rows="5" class="ln-prompt">${esc(ln.prompt || "")}</textarea>
          </label>
        </div>
      `).join("")}
    </div>

    <div class="card">
      <h2>Podcast Full-Transcript Discovery (<code>exa.ai/library/podcast</code>)</h2>
      <div class="field-grid">
        <label class="inline-check">
          <input type="checkbox" id="pod-en" ${c.podcast_discovery.enabled !== false ? "checked" : ""}>
          Enable Exa Podcast Transcript Lane
        </label>
        <label>Results per Query
          <input type="number" min="1" max="15" id="pod-num" value="${esc(c.podcast_discovery.results_per_query ?? 4)}">
        </label>
      </div>
      <label>Podcast Transcript Search Queries (one per line)
        <textarea rows="4" id="pod-queries">${esc((c.podcast_discovery.queries || []).join("\n"))}</textarea>
      </label>
    </div>
  `;

  el.querySelector("#btn-add-cat").addEventListener("click", () => {
    const raw = prompt("New category slug (lowercase, e.g. 'security'):");
    if (!raw) return;
    const slug = raw.trim().toLowerCase().replace(/[^a-z0-9_]/g, "_");
    if (!slug || c.categories[slug]) return;
    c.categories[slug] = { label: slug.title || slug, min: 0, max: 3, exa_mcp_category: null, query: "", additional_queries: [], include_text_single: [], exclude_text: ["sponsored"] };
    markDirty();
    renderLensesTab(el);
  });

  el.querySelectorAll("[data-cat-key]").forEach((row) => {
    const k = row.dataset.catKey;
    const cfg = c.categories[k];
    row.querySelector(".cat-label").addEventListener("input", (e) => { cfg.label = e.target.value; markDirty(); });
    row.querySelector(".cat-exacat").addEventListener("change", (e) => { cfg.exa_mcp_category = e.target.value || null; markDirty(); });
    row.querySelector(".cat-min").addEventListener("input", (e) => { cfg.min = parseInt(e.target.value || "0", 10); markDirty(); });
    row.querySelector(".cat-max").addEventListener("input", (e) => { cfg.max = parseInt(e.target.value || "4", 10); markDirty(); });
    row.querySelector(".cat-excl").addEventListener("input", (e) => { cfg.exclude_text = csvToList(e.target.value); markDirty(); });
    row.querySelector(".cat-q").addEventListener("input", (e) => { cfg.query = e.target.value; markDirty(); });
    row.querySelector(".cat-addq").addEventListener("input", (e) => { cfg.additional_queries = linesToList(e.target.value); markDirty(); });
    row.querySelector(".btn-del-cat").addEventListener("click", () => { delete c.categories[k]; markDirty(); renderLensesTab(el); });
  });

  el.querySelector("#btn-add-lens").addEventListener("click", () => {
    c.agent_lenses.push({ key: "custom", enabled: true, category_hint: Object.keys(c.categories)[0] || "", effort: "low", max_items: 8, min_gate_score_10: 7.5, gate_rubric: "", prompt: "" });
    markDirty();
    renderLensesTab(el);
  });
  el.querySelectorAll("[data-lens-idx]").forEach((row) => {
    const idx = parseInt(row.dataset.lensIdx, 10);
    const ln = c.agent_lenses[idx];
    row.querySelector(".ln-en").addEventListener("change", (e) => { ln.enabled = e.target.checked; markDirty(); });
    row.querySelector(".ln-key").addEventListener("input", (e) => { ln.key = e.target.value.trim(); markDirty(); });
    row.querySelector(".ln-cat").addEventListener("input", (e) => { ln.category_hint = e.target.value.trim(); markDirty(); });
    row.querySelector(".ln-max").addEventListener("input", (e) => { ln.max_items = parseInt(e.target.value || "8", 10); markDirty(); });
    row.querySelector(".ln-gate").addEventListener("input", (e) => { ln.min_gate_score_10 = parseFloat(e.target.value || "7.5"); markDirty(); });
    row.querySelector(".ln-rubric").addEventListener("input", (e) => { ln.gate_rubric = e.target.value; markDirty(); });
    row.querySelector(".ln-prompt").addEventListener("input", (e) => { ln.prompt = e.target.value; markDirty(); });
    row.querySelector(".btn-del-lens").addEventListener("click", () => { c.agent_lenses.splice(idx, 1); markDirty(); renderLensesTab(el); });
  });

  el.querySelector("#pod-en").addEventListener("change", (e) => { c.podcast_discovery.enabled = e.target.checked; markDirty(); });
  el.querySelector("#pod-num").addEventListener("input", (e) => { c.podcast_discovery.results_per_query = parseInt(e.target.value || "4", 10); markDirty(); });
  el.querySelector("#pod-queries").addEventListener("input", (e) => { c.podcast_discovery.queries = linesToList(e.target.value); markDirty(); });
}

/* ---------------- TAB 6: SCORING, HYPE & VENDORS ---------------- */
function renderScoringTab(el) {
  const c = state.cfg;
  const w = c.scoring.axes_weights;
  const div = c.scoring.diversity;
  const pp = c.publisher_penalties;
  const overridesStr = Object.entries(pp.kind_overrides || {}).map(([h, k]) => `${h}=${k}`).join("\n");

  el.innerHTML = `
    <div class="card">
      <h2>Axis Weights, Recency &amp; Cross-Issue Diversity</h2>
      <div class="field-grid">
        <label>Audience Fit Weight <input type="number" step="0.05" data-w="audience_fit" value="${esc(w.audience_fit ?? 0.45)}"></label>
        <label>Operator Utility Weight <input type="number" step="0.05" data-w="operator_utility" value="${esc(w.operator_utility ?? 0.30)}"></label>
        <label>Reframe / Sharp Thesis Weight <input type="number" step="0.05" data-w="reframe_sharp_thesis" value="${esc(w.reframe_sharp_thesis ?? 0.20)}"></label>
        <label>Evidence Anchoring Weight <input type="number" step="0.05" data-w="evidence_anchoring" value="${esc(w.evidence_anchoring ?? 0.05)}"></label>
        <label>Voice Risk Weight <input type="number" step="0.05" data-w="voice_risk" value="${esc(w.voice_risk ?? 0.0)}"></label>
        <label>Recency Half-Life (days) <input type="number" min="1" max="90" id="sc-halflife" value="${esc(c.defaults.recency_half_life_days ?? 10)}"></label>
      </div>
      <div class="field-grid">
        <label class="inline-check"><input type="checkbox" id="div-en" ${div.enabled !== false ? "checked" : ""}> Enable Cross-Issue Diversity Memory</label>
        <label>New Domain Bonus <input type="number" step="0.05" id="div-new" value="${esc(div.new_domain_bonus ?? 0.25)}"></label>
        <label>Repeat Domain Penalty <input type="number" step="0.05" id="div-dom" value="${esc(div.last_cycle_domain_penalty ?? 0.35)}"></label>
        <label>Duplicate Idea Penalty <input type="number" step="0.1" id="div-sim" value="${esc(div.similarity_penalty ?? 1.0)}"></label>
      </div>
      <label>Focus Anchor Terms (+0.2 each up to +0.6, comma-separated)
        <input type="text" id="sc-anchors" value="${esc((c.focus_anchors || []).join(", "))}">
      </label>
    </div>

    <div class="card">
      <h2>Publisher / Vendor Blog Down-Weighting</h2>
      <p class="help">Down-weights SaaS and agency marketing blogs unless base quality exceeds the waive score; hard-gates weak vendor posts out of the guide.</p>
      <div class="field-grid">
        <label class="inline-check"><input type="checkbox" id="pp-en" ${pp.enabled !== false ? "checked" : ""}> Enable Publisher Down-Weighting</label>
        <label>SaaS Penalty / Hard Gate Floor
          <div style="display:flex;gap:8px">
            <input type="number" step="0.1" id="pp-saas" value="${esc(pp.saas ?? 0.8)}">
            <input type="number" step="0.1" id="pp-saas-min" value="${esc(pp.min_banger_score_10.saas ?? 8.6)}">
          </div>
        </label>
        <label>Agency Penalty / Hard Gate Floor
          <div style="display:flex;gap:8px">
            <input type="number" step="0.1" id="pp-agency" value="${esc(pp.agency ?? 0.6)}">
            <input type="number" step="0.1" id="pp-agency-min" value="${esc(pp.min_banger_score_10.agency ?? 8.5)}">
          </div>
        </label>
        <label>Waive Score / Fade Width
          <div style="display:flex;gap:8px">
            <input type="number" step="0.1" id="pp-waive" value="${esc(pp.waive_banger_score_10 ?? 9.0)}">
            <input type="number" step="0.1" id="pp-fade" value="${esc(pp.fade_width ?? 0.8)}">
          </div>
        </label>
      </div>
      <div class="field-grid">
        <label>Exempt Domains (comma-separated; owner sites auto-exempt)
          <input type="text" id="pp-exempt" value="${esc((pp.exempt_domains || []).join(", "))}">
        </label>
        <label>Deterministic Host Overrides (<code>domain.com=saas|agency</code>, one per line)
          <textarea rows="3" id="pp-overrides" class="mono">${esc(overridesStr)}</textarea>
        </label>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <h2>Focus-Topic Boosters (${c.focus_topics.length})</h2>
          <p class="help">Reward articles centrally focused on priority themes.</p>
        </div>
        <button type="button" id="btn-add-focus" class="btn btn-secondary btn-sm">+ Add Focus Topic</button>
      </div>
      ${c.focus_topics.map((ft, idx) => `
        <div class="item-row" data-ft-idx="${idx}">
          <div class="item-row-head">
            <label class="inline-check"><input type="checkbox" class="ft-en" ${ft.enabled !== false ? "checked" : ""}> <strong>${esc(ft.label || ft.key)}</strong></label>
            <button type="button" class="btn btn-danger btn-sm btn-del-ft">Remove</button>
          </div>
          <div class="field-grid" style="margin-bottom:0">
            <label>Key &amp; Label
              <div style="display:flex;gap:6px">
                <input type="text" class="ft-key" value="${esc(ft.key || "")}" placeholder="key">
                <input type="text" class="ft-lbl" value="${esc(ft.label || "")}" placeholder="Label">
              </div>
            </label>
            <label>Terms (comma-separated) <input type="text" class="ft-terms" value="${esc((ft.terms || []).join(", "))}"></label>
            <label>Topic Tags (comma-separated) <input type="text" class="ft-tags" value="${esc((ft.topic_tags || []).join(", "))}"></label>
            <label>Max Boost <input type="number" step="0.05" class="ft-boost" value="${esc(ft.boost ?? 0.45)}"></label>
          </div>
        </div>
      `).join("")}
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <h2>Hype-Topic Suppressors (${c.hype_topics.length})</h2>
          <p class="help">Require exceptional quality for overhyped topics; weak hype pieces are gated out at selection time.</p>
        </div>
        <button type="button" id="btn-add-hype" class="btn btn-secondary btn-sm">+ Add Hype Suppressor</button>
      </div>
      ${c.hype_topics.map((ht, idx) => `
        <div class="item-row" data-ht-idx="${idx}">
          <div class="item-row-head">
            <label class="inline-check"><input type="checkbox" class="ht-en" ${ht.enabled !== false ? "checked" : ""}> <strong>${esc(ht.label || ht.key)}</strong></label>
            <button type="button" class="btn btn-danger btn-sm btn-del-ht">Remove</button>
          </div>
          <div class="field-grid" style="margin-bottom:0">
            <label>Key &amp; Label
              <div style="display:flex;gap:6px">
                <input type="text" class="ht-key" value="${esc(ht.key || "")}">
                <input type="text" class="ht-lbl" value="${esc(ht.label || "")}">
              </div>
            </label>
            <label>Terms (comma-separated) <input type="text" class="ht-terms" value="${esc((ht.terms || []).join(", "))}"></label>
            <label>Topic Tags (comma-separated) <input type="text" class="ht-tags" value="${esc((ht.topic_tags || []).join(", "))}"></label>
            <label>Penalty / Hard Floor / Max Cap
              <div style="display:flex;gap:6px">
                <input type="number" step="0.1" class="ht-pen" value="${esc(ht.penalty ?? 1.0)}" title="Max penalty">
                <input type="number" step="0.1" class="ht-min" value="${esc(ht.min_banger_score_10 ?? 7.8)}" title="Hard floor">
                <input type="number" min="0" max="20" class="ht-cap" value="${esc(ht.max_selected ?? 2)}" title="Max selected">
              </div>
            </label>
          </div>
        </div>
      `).join("")}
    </div>
  `;

  el.querySelectorAll("[data-w]").forEach((inp) => {
    inp.addEventListener("input", () => { w[inp.dataset.w] = parseFloat(inp.value || "0"); markDirty(); });
  });
  el.querySelector("#sc-halflife").addEventListener("input", (e) => { c.defaults.recency_half_life_days = parseFloat(e.target.value || "10"); markDirty(); });
  el.querySelector("#div-en").addEventListener("change", (e) => { div.enabled = e.target.checked; markDirty(); });
  el.querySelector("#div-new").addEventListener("input", (e) => { div.new_domain_bonus = parseFloat(e.target.value || "0"); markDirty(); });
  el.querySelector("#div-dom").addEventListener("input", (e) => { div.last_cycle_domain_penalty = parseFloat(e.target.value || "0"); markDirty(); });
  el.querySelector("#div-sim").addEventListener("input", (e) => { div.similarity_penalty = parseFloat(e.target.value || "0"); markDirty(); });
  el.querySelector("#sc-anchors").addEventListener("input", (e) => { c.focus_anchors = csvToList(e.target.value); markDirty(); });

  el.querySelector("#pp-en").addEventListener("change", (e) => { pp.enabled = e.target.checked; markDirty(); });
  el.querySelector("#pp-saas").addEventListener("input", (e) => { pp.saas = parseFloat(e.target.value || "0"); markDirty(); });
  el.querySelector("#pp-saas-min").addEventListener("input", (e) => { pp.min_banger_score_10.saas = parseFloat(e.target.value || "0"); markDirty(); });
  el.querySelector("#pp-agency").addEventListener("input", (e) => { pp.agency = parseFloat(e.target.value || "0"); markDirty(); });
  el.querySelector("#pp-agency-min").addEventListener("input", (e) => { pp.min_banger_score_10.agency = parseFloat(e.target.value || "0"); markDirty(); });
  el.querySelector("#pp-waive").addEventListener("input", (e) => { pp.waive_banger_score_10 = parseFloat(e.target.value || "9.0"); markDirty(); });
  el.querySelector("#pp-fade").addEventListener("input", (e) => { pp.fade_width = parseFloat(e.target.value || "0.8"); markDirty(); });
  el.querySelector("#pp-exempt").addEventListener("input", (e) => { pp.exempt_domains = csvToList(e.target.value); markDirty(); });
  el.querySelector("#pp-overrides").addEventListener("input", (e) => {
    const map = {};
    linesToList(e.target.value).forEach((line) => {
      const [h, k] = line.split("=").map((s) => s.trim());
      if (h && k) map[h] = k;
    });
    pp.kind_overrides = map;
    markDirty();
  });

  el.querySelector("#btn-add-focus").addEventListener("click", () => {
    c.focus_topics.push({ key: "new_focus", label: "New Focus Topic", enabled: true, terms: [], topic_tags: [], focus_threshold: 0.35, boost: 0.45 });
    markDirty();
    renderScoringTab(el);
  });
  el.querySelectorAll("[data-ft-idx]").forEach((row) => {
    const idx = parseInt(row.dataset.ftIdx, 10);
    const ft = c.focus_topics[idx];
    row.querySelector(".ft-en").addEventListener("change", (e) => { ft.enabled = e.target.checked; markDirty(); });
    row.querySelector(".ft-key").addEventListener("input", (e) => { ft.key = e.target.value.trim(); markDirty(); });
    row.querySelector(".ft-lbl").addEventListener("input", (e) => { ft.label = e.target.value; markDirty(); });
    row.querySelector(".ft-terms").addEventListener("input", (e) => { ft.terms = csvToList(e.target.value); markDirty(); });
    row.querySelector(".ft-tags").addEventListener("input", (e) => { ft.topic_tags = csvToList(e.target.value); markDirty(); });
    row.querySelector(".ft-boost").addEventListener("input", (e) => { ft.boost = parseFloat(e.target.value || "0.4"); markDirty(); });
    row.querySelector(".btn-del-ft").addEventListener("click", () => { c.focus_topics.splice(idx, 1); markDirty(); renderScoringTab(el); });
  });

  el.querySelector("#btn-add-hype").addEventListener("click", () => {
    c.hype_topics.push({ key: "new_hype", label: "New Hype Topic", enabled: true, terms: [], topic_tags: [], focus_threshold: 0.5, penalty: 1.0, waive_banger_score_10: 8.8, fade_width: 0.8, min_banger_score_10: 7.8, max_selected: 2 });
    markDirty();
    renderScoringTab(el);
  });
  el.querySelectorAll("[data-ht-idx]").forEach((row) => {
    const idx = parseInt(row.dataset.htIdx, 10);
    const ht = c.hype_topics[idx];
    row.querySelector(".ht-en").addEventListener("change", (e) => { ht.enabled = e.target.checked; markDirty(); });
    row.querySelector(".ht-key").addEventListener("input", (e) => { ht.key = e.target.value.trim(); markDirty(); });
    row.querySelector(".ht-lbl").addEventListener("input", (e) => { ht.label = e.target.value; markDirty(); });
    row.querySelector(".ht-terms").addEventListener("input", (e) => { ht.terms = csvToList(e.target.value); markDirty(); });
    row.querySelector(".ht-tags").addEventListener("input", (e) => { ht.topic_tags = csvToList(e.target.value); markDirty(); });
    row.querySelector(".ht-pen").addEventListener("input", (e) => { ht.penalty = parseFloat(e.target.value || "1.0"); markDirty(); });
    row.querySelector(".ht-min").addEventListener("input", (e) => { ht.min_banger_score_10 = parseFloat(e.target.value || "0"); markDirty(); });
    row.querySelector(".ht-cap").addEventListener("input", (e) => { ht.max_selected = parseInt(e.target.value || "2", 10); markDirty(); });
    row.querySelector(".btn-del-ht").addEventListener("click", () => { c.hype_topics.splice(idx, 1); markDirty(); renderScoringTab(el); });
  });
}

/* ---------------- TAB 7: PINS & WHAT-IF SIMULATOR ---------------- */
function renderSimulatorTab(el) {
  const c = state.cfg;
  const cycles = state.meta.cycles || [];
  const today = new Date().toISOString().slice(0, 10);
  const wif = state.whatIfResult;

  el.innerHTML = `
    <div class="card">
      <div class="card-header">
        <div>
          <h2>Editorial Must-Include Pins (${c.must_include.length})</h2>
          <p class="help">Pin specific URLs for a target issue build date. Pinned links bypass prefilter length floors and shortlist caps.</p>
        </div>
        <button type="button" id="btn-add-pin" class="btn btn-primary">+ Pin URL</button>
      </div>
      ${c.must_include.length === 0 ? `<p class="help">No pinned links yet.</p>` : ""}
      ${c.must_include.map((pin, idx) => `
        <div class="item-row" data-pin-idx="${idx}">
          <div class="item-row-head">
            <label class="inline-check"><input type="checkbox" class="pin-en" ${pin.enabled !== false ? "checked" : ""}> Active Pin</label>
            <button type="button" class="btn btn-danger btn-sm btn-del-pin">Remove</button>
          </div>
          <div class="field-grid" style="margin-bottom:0">
            <label>URL <input type="url" class="pin-url" value="${esc(pin.url || "")}" placeholder="https://example.com/post"></label>
            <label>Target Cycle Date <input type="date" class="pin-date" value="${esc(pin.cycle_start || today)}"></label>
            <label>Category Hint <input type="text" class="pin-cat" value="${esc(pin.category_hint || "")}"></label>
            <label>Editor Note <input type="text" class="pin-note" value="${esc(pin.note || "")}" placeholder="Why you pinned this"></label>
          </div>
        </div>
      `).join("")}
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <h2>Zero-Cost What-If Scoring Lab</h2>
          <p class="help">Test your current unsaved scoring weights, vendor gates, hype suppressors, and shortlist rules instantly against a saved cycle.</p>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <select id="wif-cycle" aria-label="Cycle to simulate">
            ${cycles.length ? cycles.map((cy) => `<option value="${esc(cy)}">${esc(cy)}</option>`).join("") : `<option value="">(no saved cycle yet)</option>`}
          </select>
          <button type="button" id="btn-seed-demo" class="btn btn-secondary">Load Sample Cycle</button>
          <button type="button" id="btn-run-wif" class="btn btn-primary">Run What-If</button>
        </div>
      </div>
      ${wif ? `
        <p class="help">
          Cycle <code>${esc(wif.cycle_start)}</code> · Mode: <code>${esc(wif.selection_mode)}</code> ·
          <strong>${esc(wif.selected_count)} / ${esc(wif.total_candidates)}</strong> candidates admitted.
        </p>
        <table class="data-table">
          <thead>
            <tr>
              <th>Status</th>
              <th>Candidate</th>
              <th>Category / Lane</th>
              <th>Saved Score</th>
              <th>What-If Score</th>
              <th>Breakdown / Gate</th>
            </tr>
          </thead>
          <tbody>
            ${wif.rows.map((r) => `
              <tr>
                <td>
                  <span class="status-pill ${r.selected ? "status-clean" : "status-err"}">
                    ${r.selected ? "Admitted" : (r.gate_reason ? "Gated" : "Dropped")}
                  </span>
                </td>
                <td>
                  <strong>${esc(r.title || "(untitled)")}</strong><br>
                  <span style="font-size:12px;color:var(--muted)">${esc(r.author || "unknown")} · ${esc(r.canonical_url)}</span>
                </td>
                <td><code>${esc(r.category)}</code><br><span style="font-size:12px;color:var(--muted)">${esc(r.lane)}</span></td>
                <td>${r.before_score ?? "—"}</td>
                <td>
                  <strong>${r.after_score ?? "—"}</strong>
                  ${r.delta ? `<span style="font-size:12px;color:${r.delta > 0 ? "var(--ok)" : "var(--err)"}">(${r.delta > 0 ? "+" : ""}${r.delta})</span>` : ""}
                </td>
                <td style="font-size:12px">
                  ${r.gate_reason ? `<span style="color:var(--err)">${esc(r.gate_reason)}</span>` : ""}
                  ${r.breakdown ? `base ${r.breakdown.base} · rec +${r.breakdown.recency_bonus} · foc +${r.breakdown.focus_boost} · pub -${r.breakdown.publisher_penalty} · hype -${r.breakdown.hype_penalty}` : ""}
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      ` : `<p class="help">Click <strong>Load Sample Cycle</strong> to generate an instant 5-item test build (with indie, research, podcast, SaaS vendor, and hype examples) and renders in all enabled output formats, or click <strong>Run What-If</strong> on an existing build.</p>`}
    </div>
  `;

  el.querySelector("#btn-add-pin").addEventListener("click", () => {
    c.must_include.push({ url: "", cycle_start: today, category_hint: Object.keys(c.categories)[0] || "", note: "", enabled: true });
    markDirty();
    renderSimulatorTab(el);
  });
  el.querySelectorAll("[data-pin-idx]").forEach((row) => {
    const idx = parseInt(row.dataset.pinIdx, 10);
    const pin = c.must_include[idx];
    row.querySelector(".pin-en").addEventListener("change", (e) => { pin.enabled = e.target.checked; markDirty(); });
    row.querySelector(".pin-url").addEventListener("input", (e) => { pin.url = e.target.value.trim(); markDirty(); });
    row.querySelector(".pin-date").addEventListener("change", (e) => { pin.cycle_start = e.target.value; markDirty(); });
    row.querySelector(".pin-cat").addEventListener("input", (e) => { pin.category_hint = e.target.value.trim(); markDirty(); });
    row.querySelector(".pin-note").addEventListener("input", (e) => { pin.note = e.target.value; markDirty(); });
    row.querySelector(".btn-del-pin").addEventListener("click", () => { c.must_include.splice(idx, 1); markDirty(); renderSimulatorTab(el); });
  });

  el.querySelector("#btn-seed-demo").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      const res = await api("/api/seed-demo-cycle", {
        method: "POST",
        body: JSON.stringify({ slug: state.activeSlug, stream: c }),
      });
      if (!state.meta.cycles.includes(res.cycle_start)) state.meta.cycles.unshift(res.cycle_start);
      state.outputsCache = null;
      state.whatIfResult = await api("/api/what-if", {
        method: "POST",
        body: JSON.stringify({ slug: state.activeSlug, cycle_start: res.cycle_start, stream: c }),
      });
      showToast(`Generated sample cycle ${res.cycle_start} & rendered outputs`);
      renderSimulatorTab(el);
    } catch (err) {
      showToast(err.message);
      e.target.disabled = false;
    }
  });

  el.querySelector("#btn-run-wif").addEventListener("click", async () => {
    const cyc = el.querySelector("#wif-cycle").value;
    try {
      state.whatIfResult = await api("/api/what-if", {
        method: "POST",
        body: JSON.stringify({ slug: state.activeSlug, cycle_start: cyc, stream: c }),
      });
      renderSimulatorTab(el);
    } catch (err) {
      showToast(err.message);
    }
  });
}

/* ---------------- TAB 8: RENDERED OUTPUTS & LEADERBOARD ---------------- */
async function renderOutputsTab(el) {
  if (!state.outputsCache) {
    el.innerHTML = `<div class="card"><p>Loading rendered outputs…</p></div>`;
    state.outputsCache = await api(`/api/outputs/${encodeURIComponent(state.activeSlug)}`);
  }
  const out = state.outputsCache;
  const subtabs = [
    ["guide_markdown", "Markdown Guide (issue_content.md)"],
    ["text_xml", "Text RSS XML (feed.xml)"],
    ["podcast_xml", "Podcast RSS XML (podcast_feed.xml)"],
    ["review_markdown", "Review Pile (review_content.md)"],
    ["issue_json", "Issue JSON"],
    ["leaderboard", "Author Leaderboard"],
  ];

  let bodyHtml = "";
  if (state.outputSubtab === "leaderboard") {
    const rows = out.leaderboard || [];
    bodyHtml = rows.length
      ? `<table class="data-table">
          <thead><tr><th>Author</th><th>Admitted Picks</th><th>Runs Seen</th><th>Best Score</th><th>Domains</th></tr></thead>
          <tbody>
            ${rows.map((r) => `<tr><td><strong>${esc(r.name)}</strong></td><td>${esc(r.hits)}</td><td>${esc(r.runs_seen)}</td><td>${esc(r.max_score)}</td><td>${esc(Object.keys(r.domains || {}).join(", "))}</td></tr>`).join("")}
          </tbody>
        </table>`
      : `<p class="help">No authors recorded yet. Run a curation pass or load a sample cycle in the What-If Lab.</p>`;
  } else {
    const text = out[state.outputSubtab] || "";
    bodyHtml = text
      ? `<pre class="code-box" style="max-height:620px;overflow:auto">${esc(text)}</pre>`
      : `<p class="help">This output file has not been generated yet for <code>${esc(state.activeSlug)}</code>. Enable the format under Overview &amp; Outputs and run a curation pass (or click <strong>Load Sample Cycle</strong> in Tab 7).</p>`;
  }

  el.innerHTML = `
    <div class="card">
      <div class="card-header">
        <div>
          <h2>Rendered Outputs &amp; Author Leaderboard</h2>
          <p class="help">Inspect generated files in <code>out/${esc(state.activeSlug)}/</code>.</p>
        </div>
        <button type="button" id="btn-refresh-out" class="btn btn-secondary btn-sm">Refresh Files</button>
      </div>
      <div class="subtabs">
        ${subtabs.map(([k, label]) => `
          <button type="button" class="btn ${state.outputSubtab === k ? "btn-primary" : "btn-secondary"} btn-sm" data-subtab="${k}">${esc(label)}</button>
        `).join("")}
      </div>
      ${bodyHtml}
    </div>
  `;

  el.querySelector("#btn-refresh-out").addEventListener("click", async () => {
    state.outputsCache = null;
    await renderOutputsTab(el);
  });
  el.querySelectorAll("[data-subtab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.outputSubtab = btn.dataset.subtab;
      renderOutputsTab(el);
    });
  });
}

/* ---------------- BOOTSTRAP & GLOBAL EVENTS ---------------- */
async function init() {
  state.status = await api("/api/status");
  const listRes = await api("/api/streams");
  state.streams = listRes.streams || [];
  const initial = state.streams[0]?.slug || state.status.default_stream;
  await loadStream(initial);

  document.getElementById("stream-select").addEventListener("change", (e) => loadStream(e.target.value));
  document.getElementById("btn-save").addEventListener("click", () => saveCurrentStream().catch((err) => showToast(err.message)));

  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.activeTab = btn.dataset.tab;
      renderActiveTab();
    });
  });

  // New Stream Modal
  const newModal = document.getElementById("modal-new-stream");
  document.getElementById("btn-new-stream").addEventListener("click", () => newModal.showModal());
  document.getElementById("btn-cancel-new").addEventListener("click", () => newModal.close());
  document.getElementById("form-new-stream").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const payload = {
        slug: document.getElementById("new-slug").value.trim(),
        newsletter: document.getElementById("new-newsletter").value.trim(),
        owner: document.getElementById("new-owner").value.trim(),
        owner_site: document.getElementById("new-site").value.trim(),
        description: document.getElementById("new-desc").value.trim(),
        template_slug: state.activeSlug,
      };
      const created = await api("/api/streams", { method: "POST", body: JSON.stringify(payload) });
      const refreshed = await api("/api/streams");
      state.streams = refreshed.streams || [];
      newModal.close();
      await loadStream(created.stream.slug);
      showToast(`Created stream '${created.stream.slug}'`);
    } catch (err) {
      showToast(err.message);
    }
  });

  // Raw JSON Modal
  const jsonModal = document.getElementById("modal-json");
  const jsonEditor = document.getElementById("raw-json-editor");
  document.getElementById("btn-raw-json").addEventListener("click", () => {
    jsonEditor.value = JSON.stringify(state.cfg, null, 2);
    jsonModal.showModal();
  });
  document.getElementById("btn-close-json").addEventListener("click", () => jsonModal.close());
  document.getElementById("btn-apply-json").addEventListener("click", () => {
    try {
      const parsed = JSON.parse(jsonEditor.value);
      parsed.slug = state.activeSlug;
      state.cfg = ensureShape(parsed);
      markDirty(true);
      jsonModal.close();
      renderActiveTab();
      showToast("Applied JSON edits (click Save Config to persist)");
    } catch (err) {
      showToast(`Invalid JSON: ${err.message}`);
    }
  });

  window.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      saveCurrentStream().catch((err) => showToast(err.message));
    }
  });
}

init().catch((err) => {
  document.getElementById("panel-content").innerHTML = `<div class="card"><h2>Failed to load Studio</h2><p>${esc(err.message)}</p></div>`;
});
