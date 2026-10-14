"use strict";

// ------------------------------------------------------------ helpers
const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtNum = (n) => Number(n || 0).toLocaleString();
const fmtTime = (t) => (t ? new Date(t * 1000).toLocaleString() : "never");
const fmtDur = (a, b) => { const s = Math.max(0, Math.round((b || Date.now() / 1000) - a)); return s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`; };

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.json !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.json); }
  if (opts.form) init.body = opts.form;
  const r = await fetch("/api" + path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(msg);
  }
  return r.json();
}

function toast(msg, bad = false) {
  const t = document.createElement("div");
  t.className = "toast" + (bad ? " bad" : "");
  t.textContent = msg;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), bad ? 6000 : 3000);
}
const guard = (fn) => async (...a) => { try { return await fn(...a); } catch (e) { toast(e.message, true); } };

const S = { state: null, artists: null, genres: [], skipped: new Set(), poll: null };

async function loadState() { S.state = await api("/state"); renderChrome(); return S.state; }
async function loadArtists() { const r = await api("/artists"); S.artists = r.artists; S.genres = r.genres; return r; }

function renderChrome() {
  const st = S.state;
  $("#badge-review").textContent = st.counts.undefined ? fmtNum(st.counts.undefined) : "";
  $("#dry-pill").classList.toggle("hidden", !st.config.dry_run);
  $("#dot-job").classList.toggle("on", st.job?.state === "running");
  $("#side-foot").textContent = st.config.library_base;
}

// ------------------------------------------------------------ router
const routes = { dashboard, review, artists, genres, apply, data: dataView, settings };
const titles = { dashboard: "Dashboard", review: "Review new artists", artists: "Artists", genres: "Genres", apply: "Apply tags", data: "Import / Export", settings: "Settings" };

async function route() {
  clearInterval(S.poll); S.poll = null;
  const name = (location.hash.replace(/^#\//, "") || "dashboard").split("?")[0];
  const fn = routes[name] || dashboard;
  const key = routes[name] ? name : "dashboard";
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === key));
  $("#page-title").textContent = titles[key];
  $("#sidebar").classList.remove("open");
  try { await loadState(); await fn(); } catch (e) { view.innerHTML = `<div class="err-banner">${esc(e.message)}</div>`; }
}
window.addEventListener("hashchange", route);
$("#menu-btn").onclick = () => $("#sidebar").classList.toggle("open");

// ------------------------------------------------------------ dashboard
async function dashboard() {
  const st = S.state, c = st.counts;
  const pct = c.artists ? Math.round((c.defined / c.artists) * 100) : 0;
  view.innerHTML = `
    ${st.library_exists ? "" : `<div class="err-banner">Library path <code>${esc(st.config.library_base)}</code> doesn't exist inside the container. Check your volume mount or <a href="#/settings">Settings</a>.</div>`}
    <div class="grid stats">
      <div class="card stat"><div class="n">${fmtNum(c.artists)}</div><div class="l">Artists in library</div></div>
      <div class="card stat"><div class="n">${fmtNum(c.defined)}</div><div class="l">Defined (${pct}%)</div></div>
      <div class="card stat link" onclick="location.hash='#/review'"><div class="n">${fmtNum(c.undefined)}</div><div class="l">Need a genre →</div></div>
      <div class="card stat link" onclick="location.hash='#/genres'"><div class="n">${fmtNum(c.genres)}</div><div class="l">Unique genres →</div></div>
    </div>
    <div class="card mt">
      <h2>Library coverage</h2>
      <div class="progressbar mt"><div style="width:${pct}%"></div></div>
      <p class="sub mt small">${c.orphans ? `${fmtNum(c.orphans)} definition(s) have no matching folder on disk. ` : ""}Last scanned ${fmtTime(st.scanned_at)}.</p>
      <div class="row"><button id="rescan">Rescan library</button></div>
    </div>
    <div class="grid two mt">
      <div class="card">
        <h2>Last apply</h2>
        <p class="sub">${st.last_apply ? fmtTime(st.last_apply) : "Never run. Your first smart apply will process every defined artist."}</p>
        <div class="row"><a href="#/apply"><button class="primary">Go to Apply</button></a></div>
      </div>
      <div class="card">
        <h2>Got an existing definitions file?</h2>
        <p class="sub">Upload your <code>artist_genres.json</code> and continue where you left off.</p>
        <div class="row"><a href="#/data"><button>Import JSON</button></a></div>
      </div>
    </div>`;
  $("#rescan").onclick = guard(async (e) => { e.target.disabled = true; e.target.textContent = "Scanning…"; await api("/scan", { method: "POST" }); toast("Library rescanned"); await route(); });
}

// ------------------------------------------------------------ review
async function review() {
  await loadArtists();
  const queue = S.artists.filter((a) => a.on_disk && !a.genre && !S.skipped.has(a.name));
  const total = S.state.counts.undefined;
  if (!queue.length) {
    view.innerHTML = `<div class="card empty"><div class="big">${total ? "⏭" : "🎉"}</div>
      <h2>${total ? "All remaining artists skipped" : "Every artist has a genre"}</h2>
      <p>${total ? `${total} still undefined.` : "New artists will show up here when they appear in your library."}</p>
      ${S.skipped.size ? `<button id="unskip">Review ${S.skipped.size} skipped</button>` : ""}</div>`;
    if (S.skipped.size) $("#unskip").onclick = () => { S.skipped.clear(); review(); };
    return;
  }
  const cur = queue[0];
  const donePct = Math.round(((S.state.counts.artists - total) / Math.max(1, S.state.counts.artists)) * 100);
  view.innerHTML = `
    <div class="row small muted" style="margin-bottom:10px"><span>${queue.length} to go${S.skipped.size ? ` · ${S.skipped.size} skipped` : ""}</span><span class="spacer"></span><span>${donePct}% of library defined</span></div>
    <div class="progressbar" style="margin-bottom:16px"><div style="width:${donePct}%"></div></div>
    <div class="card review-card">
      <div class="muted small">${esc(cur.group)}</div>
      <div class="artist">${esc(cur.name)}</div>
      <div class="tags small muted" id="peek">Reading tags on disk…</div>
      <div class="row" style="justify-content:center"><button type="button" id="lfm">Check Last.fm</button></div>
      <div id="lfmres" class="small" style="margin-top:12px"></div>
      <div class="section-label">Your genres <span class="muted">(A–Z)</span></div>
      <div class="chips" id="chips"></div>
      <form id="f">
        <input type="text" id="g" placeholder="Type a genre, or pick one above" autocomplete="off" autofocus>
        <div class="row end mt">
          <button type="button" class="ghost" id="skip">Skip</button>
          <button type="submit" class="primary">Save &amp; next</button>
        </div>
        <p class="small muted">Press <span class="kbd">Enter</span> to save. Single genre only — no <code>; | /</code></p>
      </form>
    </div>`;
  const input = $("#g"), chipsEl = $("#chips");
  let suggested = new Set();
  const drawChips = () => {
    const q = input.value.trim().toLowerCase();
    const list = S.genres.filter((g) => !q || g.genre.toLowerCase().includes(q)).sort((a, b) => a.genre.localeCompare(b.genre, undefined, { sensitivity: "base" }));
    chipsEl.innerHTML = list.map((g) => `<button type="button" class="chip${suggested.has(g.genre) ? " suggest" : ""}" data-g="${esc(g.genre)}">${esc(g.genre)}<span class="c">${g.count}</span></button>`).join("") || `<span class="muted small">No matching genres yet — type a new one.</span>`;
  };
  drawChips();
  input.addEventListener("input", drawChips);
  chipsEl.onclick = (e) => { const b = e.target.closest(".chip"); if (b) { input.value = b.dataset.g; $("#f").requestSubmit(); } };
  $("#skip").onclick = () => { S.skipped.add(cur.name); review(); };
  $("#lfm").onclick = guard(async (e) => {
    if (!S.state.config.lastfm_configured) { toast("Add a Last.fm API key in Settings first", true); return; }
    const btn = e.target; btn.disabled = true; btn.textContent = "Checking…";
    try {
      const r = await api("/lastfm?name=" + encodeURIComponent(cur.name));
      const known = new Map(S.genres.map((g) => [g.genre.toLowerCase(), g.genre]));
      $("#lfmres").innerHTML = r.tags.length
        ? `<div class="lfm-panel"><div class="section-label lfm">Last.fm says <span class="muted">· click a tag to use it · <b>✓</b> = matches one of your genres</span></div><div class="chips">${r.tags.map((t) => { const k = known.get(t.name.toLowerCase()); return `<button type="button" class="chip lfm-chip${k ? " match" : ""}" data-lfm="${esc(k || t.name)}">${k ? "✓ " : ""}${esc(k || t.name)}<span class="c">${t.count}</span></button>`; }).join("")}</div></div>`
        : `<span class="muted">Last.fm has no tags for this artist.</span>`;
    } finally { btn.disabled = false; btn.textContent = "Check Last.fm"; }
  });
  $("#lfmres").onclick = (e) => { const b = e.target.closest("[data-lfm]"); if (b) { input.value = b.dataset.lfm; drawChips(); input.focus(); } };
  $("#f").onsubmit = guard(async (e) => {
    e.preventDefault();
    const genre = input.value.trim();
    if (!genre) { S.skipped.add(cur.name); return review(); }
    await api("/artist", { method: "PUT", json: { name: cur.name, genre } });
    toast(`${cur.name} → ${genre}`);
    await loadState();
    review();
  });
  input.focus();
  try {
    const t = await api("/artist/tags?name=" + encodeURIComponent(cur.name));
    if (!$("#peek")) return;
    const known = new Map(S.genres.map((g) => [g.genre.toLowerCase(), g.genre]));
    t.genres.forEach((g) => { const k = known.get(g.genre.toLowerCase()); if (k) suggested.add(k); });
    $("#peek").innerHTML = t.total
      ? `Tags currently on disk: ${t.genres.map((g) => `<b>${esc(g.genre)}</b> ×${g.count}`).join(", ")} <span class="muted">(${t.sampled < t.total ? `sampled ${t.sampled}/${t.total}` : t.total} files)</span>`
      : "No audio files found in this folder.";
    drawChips();
  } catch (e) { if ($("#peek")) $("#peek").textContent = e.message; }
}

// ------------------------------------------------------------ artists
async function artists() {
  await loadArtists();
  const hq = new URLSearchParams(location.hash.split("?")[1] || "");
  const f = { q: "", show: "all", genre: hq.get("genre") };
  view.innerHTML = `
    <div class="card">
      <div class="row">
        <input type="search" id="q" placeholder="Search artists or genres…" style="max-width:340px">
        <div class="seg" id="seg">
          <button data-v="all" class="on">All</button><button data-v="defined">Defined</button><button data-v="undefined">Undefined</button><button data-v="orphan">Not on disk</button>
        </div>
        <span class="spacer"></span><span class="muted small" id="count"></span>
      </div>
      <div class="row mt ${f.genre ? "" : "hidden"}" id="gf"><span class="pill accent">Genre: <b>${esc(f.genre)}</b></span><button class="ghost small" id="gfx">✕ Clear genre filter</button></div>
      <datalist id="gl">${S.genres.map((g) => `<option value="${esc(g.genre)}">`).join("")}</datalist>
      <div class="table-wrap mt"><table><thead><tr><th>Artist</th><th>Group</th><th style="width:34%">Genre</th><th></th></tr></thead><tbody id="tb"></tbody></table></div>
    </div>`;
  const tb = $("#tb");
  const draw = () => {
    const q = f.q.toLowerCase();
    const rows = S.artists.filter((a) =>
      (f.genre === null || a.genre === f.genre) &&
      (!q || a.name.toLowerCase().includes(q) || (f.genre === null && a.genre.toLowerCase().includes(q))) &&
      (f.show === "all" || (f.show === "defined" && a.genre) || (f.show === "undefined" && !a.genre && a.on_disk) || (f.show === "orphan" && !a.on_disk)));
    $("#count").textContent = `${fmtNum(rows.length)} shown`;
    const shown = rows.slice(0, 300);
    tb.innerHTML = shown.map((a) => `<tr data-n="${esc(a.name)}">
      <td>${esc(a.name)}${a.on_disk ? "" : ` <span class="pill warn">not on disk</span>`}</td>
      <td class="muted">${esc(a.group)}</td>
      <td><input type="text" list="gl" value="${esc(a.genre)}" placeholder="—" data-orig="${esc(a.genre)}"></td>
      <td class="actions">${a.on_disk && a.genre ? `<button class="ghost" data-act="apply" title="Write tags for this artist now">Apply</button>` : ""}${a.genre ? `<button class="ghost danger" data-act="del" title="Remove definition">✕</button>` : ""}</td></tr>`).join("")
      + (rows.length > shown.length ? `<tr><td colspan="4" class="muted small">Showing first ${shown.length} — refine your search to see more.</td></tr>` : "")
      || `<tr><td colspan="4"><div class="empty">Nothing matches.</div></td></tr>`;
  };
  draw();
  $("#gfx").onclick = () => { f.genre = null; $("#gf").classList.add("hidden"); history.replaceState(null, "", "#/artists"); draw(); };
  $("#q").oninput = (e) => { f.q = e.target.value; draw(); };
  $("#seg").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; f.show = b.dataset.v; document.querySelectorAll("#seg button").forEach((x) => x.classList.toggle("on", x === b)); draw(); };
  const find = (n) => S.artists.find((a) => a.name === n);
  tb.addEventListener("change", guard(async (e) => {
    const input = e.target; if (input.tagName !== "INPUT") return;
    const name = input.closest("tr").dataset.n, val = input.value.trim();
    if (val === input.dataset.orig) return;
    try {
      if (!val) { await api("/artist?name=" + encodeURIComponent(name), { method: "DELETE" }); find(name).genre = ""; toast(`Cleared ${name}`); }
      else { await api("/artist", { method: "PUT", json: { name, genre: val } }); find(name).genre = val; toast(`${name} → ${val}`); }
    } catch (err) { input.value = input.dataset.orig; throw err; }
    input.dataset.orig = find(name).genre; await loadState();
  }));
  tb.addEventListener("click", guard(async (e) => {
    const b = e.target.closest("button[data-act]"); if (!b) return;
    const name = b.closest("tr").dataset.n;
    if (b.dataset.act === "del") {
      if (!confirm(`Remove the genre definition for "${name}"?`)) return;
      await api("/artist?name=" + encodeURIComponent(name), { method: "DELETE" });
      find(name).genre = ""; if (!find(name).on_disk) S.artists = S.artists.filter((a) => a.name !== name);
      draw(); await loadState();
    } else {
      await api("/apply", { method: "POST", json: { mode: "selected", artists: [name] } });
      location.hash = "#/apply";
    }
  }));
}

// ------------------------------------------------------------ genres
async function genres() {
  const r = await loadArtists();
  view.innerHTML = `<div class="card">
    <p class="sub">Every genre you've defined. Rename one to update all of its artists at once (a backup of your definitions is saved first). Merge two genres by renaming one to the other's name.</p>
    ${r.genres.length ? `<div class="table-wrap"><table><thead><tr><th>Genre</th><th>Artists</th><th></th></tr></thead><tbody>
    ${[...r.genres].sort((a, b) => b.count - a.count).map((g) => `<tr data-g="${esc(g.genre)}"><td>${esc(g.genre)}</td><td>${fmtNum(g.count)}</td><td class="actions"><button class="ghost" data-act="view">View artists</button><button class="ghost" data-act="rename">Rename / merge</button></td></tr>`).join("")}
    </tbody></table></div>` : `<div class="empty">No genres defined yet.</div>`}</div>
    <dialog id="dlg"><form method="dialog" id="rf"><h2 style="margin-top:0">Rename genre</h2>
      <p class="muted small" id="rmsg"></p><input type="text" id="rnew" list="gl2"><datalist id="gl2">${r.genres.map((g) => `<option value="${esc(g.genre)}">`).join("")}</datalist>
      <div class="row end mt"><button type="button" class="ghost" id="rcancel">Cancel</button><button class="primary" type="submit">Rename</button></div></form></dialog>`;
  const dlg = $("#dlg"); let old = "";
  view.onclick = (e) => {
    const b = e.target.closest("button[data-act]"); if (!b) return;
    old = b.closest("tr").dataset.g;
    if (b.dataset.act === "view") { location.hash = "#/artists?genre=" + encodeURIComponent(old); return; }
    $("#rmsg").textContent = `Change “${old}” for all ${r.genres.find((g) => g.genre === old).count} artists using it.`;
    $("#rnew").value = old; dlg.showModal(); $("#rnew").select();
  };
  $("#rcancel").onclick = () => dlg.close();
  $("#rf").onsubmit = guard(async (e) => {
    e.preventDefault();
    const nv = $("#rnew").value.trim(); if (!nv || nv === old) return dlg.close();
    const res = await api("/genres/rename", { method: "POST", json: { old, new: nv } });
    dlg.close(); toast(`Updated ${res.updated} artist(s)`); genres();
  });
}

// ------------------------------------------------------------ apply
async function apply() {
  await loadState();  // fresh job state (also after Start / when a run finishes)
  const st = S.state, running = st.job?.state === "running";
  const hist = await api("/history");
  view.innerHTML = `
    <div id="jobbox"></div>
    <div class="card" id="startbox">
      <h2>Write genre tags</h2>
      <p class="sub">Writes each artist's genre to every audio file in their folder. Files already correct are left untouched.</p>
      <div class="grid two">
        <label class="check"><input type="radio" name="mode" value="smart" checked><span>Smart apply<small>Only artists with new or changed folders since the last apply. Fast.</small></span></label>
        <label class="check"><input type="radio" name="mode" value="full"><span>Full apply<small>Check every defined artist. Slower, thorough.</small></span></label>
      </div>
      <label class="check mt"><input type="checkbox" id="dry" ${st.config.dry_run ? "checked" : ""}><span>Dry run<small>Report what would change without writing anything. Doesn't update the smart-apply timestamp.</small></span></label>
      <div class="row mt"><button class="primary" id="go" ${running ? "disabled" : ""}>Start</button><span class="muted small">Last real apply: ${fmtTime(st.last_apply)}</span></div>
    </div>
    <div class="card"><h2>History</h2>
      ${hist.length ? `<div class="table-wrap"><table><thead><tr><th>Started</th><th>Mode</th><th>Artists</th><th>Files</th><th>Changed</th><th>Errors</th><th>Result</th></tr></thead><tbody>
      ${hist.map((h) => `<tr><td>${fmtTime(h.started)}</td><td>${esc(h.mode)}${h.dry_run ? ` <span class="pill warn">dry</span>` : ""}</td><td>${fmtNum(h.done_artists)}</td><td>${fmtNum(h.totals.files)}</td><td>${fmtNum(h.totals.changed)}</td><td>${fmtNum(h.totals.errors)}</td><td><span class="pill ${h.state === "done" ? "ok" : h.state === "error" ? "bad" : ""}">${esc(h.state)}</span></td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty">No runs yet.</div>`}</div>`;
  $("#go").onclick = guard(async () => {
    const mode = document.querySelector("input[name=mode]:checked").value;
    await api("/apply", { method: "POST", json: { mode, dry_run: $("#dry").checked } });
    await apply();
  });
  const paint = (job) => {
    const box = $("#jobbox"); if (!box) return;
    if (!job) { box.innerHTML = ""; return; }
    const live = job.state === "running";
    const pct = job.total_artists ? Math.round((job.done_artists / job.total_artists) * 100) : (live ? 0 : 100);
    box.innerHTML = `<div class="card" style="margin-bottom:16px">
      <div class="row"><h2 style="margin:0">${live ? (job.phase === "checking" ? "Checking for changes…" : "Applying…") : job.state === "done" ? "Finished" : job.state === "cancelled" ? "Cancelled" : "Failed"}</h2>
        ${job.dry_run ? `<span class="pill warn">dry run</span>` : ""}<span class="pill">${esc(job.mode)}</span><span class="spacer"></span>
        <span class="muted small">${fmtDur(job.started, live ? null : job.finished)}</span>
        ${live ? `<button class="danger" id="cancel">Cancel</button>` : ""}</div>
      ${job.message ? `<p class="sub mt">${esc(job.message)}</p>` : ""}
      <div class="progressbar mt"><div style="width:${pct}%"></div></div>
      <div class="row small muted mt"><span>${job.done_artists}/${job.total_artists} artists</span>${live && job.current ? `<span>· ${esc(job.current)}</span>` : ""}<span class="spacer"></span>
        <span>${fmtNum(job.totals.files)} files · <b style="color:var(--ok)">${fmtNum(job.totals.changed)} ${job.dry_run ? "would change" : "changed"}</b> · ${fmtNum(job.totals.skipped)} already correct · <b style="color:${job.totals.errors ? "var(--bad)" : "inherit"}">${fmtNum(job.totals.errors)} errors</b></span></div>
      ${job.log.length ? `<div class="log mt"><table><tbody>${[...job.log].reverse().map((l) => `<tr><td>${esc(l.artist)}</td><td class="muted">${esc(l.genre)}</td><td class="muted">${l.files} file${l.files === 1 ? "" : "s"}</td><td>${l.changed ? `<span class="pill ok">${l.changed} ${job.dry_run ? "would change" : "changed"}</span>` : `<span class="muted">—</span>`}</td><td>${l.errors ? `<span class="pill bad">${l.errors} err</span>` : ""}</td></tr>`).join("")}</tbody></table></div>` : ""}
    </div>`;
    const c = $("#cancel"); if (c) c.onclick = guard(() => api("/job/cancel", { method: "POST" }));
    $("#go") && ($("#go").disabled = live);
  };
  paint(st.job);
  if (running) {
    S.poll = setInterval(async () => {
      try { const j = await api("/job"); paint(j); if (j && j.state !== "running") { clearInterval(S.poll); await loadState(); apply(); } } catch {}
    }, 1000);
  }
}

// ------------------------------------------------------------ import / export
async function dataView() {
  view.innerHTML = `
    <div class="card">
      <h2>Import definitions</h2>
      <p class="sub">Upload an <code>artist_genres.json</code> (a simple <code>{"Artist": "Genre"}</code> object). You'll see a preview before anything changes, and a backup is saved on commit.</p>
      <div class="drop" id="drop">Drop a JSON file here, or click to choose<input type="file" id="file" accept=".json,application/json" class="hidden"></div>
      <div id="preview"></div>
    </div>
    <div class="card"><h2>Export</h2><p class="sub">Download your current definitions as JSON — handy for backups or moving to another install.</p>
      <a href="/api/export" download><button>Download artist_genres.json</button></a></div>`;
  const drop = $("#drop"), file = $("#file");
  let chosen = null, mode = "merge";
  drop.onclick = () => file.click();
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); if (e.dataTransfer.files[0]) pick(e.dataTransfer.files[0]); };
  file.onchange = () => file.files[0] && pick(file.files[0]);
  const send = (commit) => { const fd = new FormData(); fd.append("file", chosen); fd.append("mode", mode); fd.append("commit", commit); return api("/import", { method: "POST", form: fd }); };
  const pick = guard(async (f) => { chosen = f; await show(); });
  const show = guard(async () => {
    const r = await send(false), p = $("#preview");
    const modeDesc = { merge: "Imported values win; everything else is kept.", fill: "Only add artists you haven't defined; never overwrite.", replace: "Throw away current definitions and use the file exactly." };
    p.innerHTML = `<div class="mt"><div class="row"><b>${esc(chosen.name)}</b><span class="spacer"></span>
        <div class="seg" id="mseg">${["merge", "fill", "replace"].map((m) => `<button data-m="${m}" class="${m === mode ? "on" : ""}">${m === "fill" ? "Add missing only" : m[0].toUpperCase() + m.slice(1)}</button>`).join("")}</div></div>
      <p class="sub small mt">${modeDesc[mode]}</p>
      <div class="grid stats">
        <div class="card stat"><div class="n">${fmtNum(r.added)}</div><div class="l">New artists</div></div>
        <div class="card stat"><div class="n">${fmtNum(mode === "fill" ? r.skipped_conflicts : r.changed_count)}</div><div class="l">${mode === "fill" ? "Conflicts (kept yours)" : "Genres changed"}</div></div>
        <div class="card stat"><div class="n">${fmtNum(r.unchanged)}</div><div class="l">Already identical</div></div>
        ${mode === "replace" ? `<div class="card stat"><div class="n">${fmtNum(r.removed)}</div><div class="l">Removed</div></div>` : ""}
      </div>
      ${r.not_on_disk ? `<p class="small muted mt">${fmtNum(r.not_on_disk)} artist(s) in the file have no matching folder in the library right now. They're kept and will apply if the folder appears.</p>` : ""}
      ${r.invalid.length ? `<p class="small mt" style="color:var(--bad)">${r.invalid.length} entr${r.invalid.length === 1 ? "y has" : "ies have"} an invalid genre and will be skipped: ${esc(r.invalid.slice(0, 5).join(", "))}${r.invalid.length > 5 ? "…" : ""}</p>` : ""}
      ${r.changed.length ? `<details class="mt"><summary class="small">Show changes (${r.changed.length}${r.changed_count > 200 ? "+" : ""})</summary><div class="log mt"><table><tbody>${r.changed.map((c) => `<tr><td>${esc(c.name)}</td><td class="muted">${esc(c.from)}</td><td>→</td><td>${esc(c.to)}</td></tr>`).join("")}</tbody></table></div></details>` : ""}
      <div class="row end mt"><span class="muted small">${fmtNum(r.total_after)} defined after import</span>
        <button class="primary" id="commit">Import</button></div></div>`;
    $("#mseg").onclick = (e) => { const b = e.target.closest("button"); if (b) { mode = b.dataset.m; show(); } };
    $("#commit").onclick = guard(async () => {
      if (mode === "replace" && !confirm("Replace ALL current definitions with this file? A backup is saved first.")) return;
      const done = await send(true);
      toast(`Imported — ${done.total_after} artists defined`); await loadState(); dataView();
    });
  });
}

// ------------------------------------------------------------ settings
async function settings() {
  const c = S.state.config;
  view.innerHTML = `<div class="card"><form id="sf">
    <label class="field"><span>Library path (inside the container)</span><input type="text" id="lib" value="${esc(c.library_base)}">
      <small>Expected layout: <code>&lt;library&gt;/&lt;A–Z or #&gt;/&lt;Artist&gt;/…</code>. <code>Various Artists</code> is treated as one artist.</small></label>
    <label class="field"><span>Excluded folders</span><textarea id="excl" rows="2" placeholder="Playlists">${esc(c.excluded_folders.join("\n"))}</textarea>
      <small>Top-level folders in the library to ignore completely (one per line, case-insensitive).</small></label>
    <label class="field"><span>Single-artist folders</span><textarea id="flat" rows="2" placeholder="Various Artists">${esc(c.flat_folders.join("\n"))}</textarea>
      <small>Top-level folders that contain albums directly (<code>&lt;folder&gt;/&lt;Album&gt;/…</code>). The whole folder counts as one artist with one genre, e.g. <code>Various Artists</code> → <code>Various</code>.</small></label>
    <label class="field"><span>Last.fm API key</span><input type="password" id="lfmkey" autocomplete="off" placeholder="${c.lastfm_configured ? "Saved — leave blank to keep" : "Paste your API key"}">
      <small>Enables “Check Last.fm” on the Review page. Free key at last.fm/api/account/create. ${c.lastfm_configured ? `<label style="font-weight:400"><input type="checkbox" id="lfmclear"> Remove saved key</label>` : ""}</small></label>
    <label class="field"><span>Audio file extensions</span><input type="text" id="exts" value="${esc(c.supported_exts.join(", "))}"><small>Comma-separated.</small></label>
    <label class="field"><span>Worker threads</span><input type="number" id="workers" min="1" max="32" value="${c.workers}" style="max-width:120px"><small>Files tagged in parallel per artist. Lower this on slow or network storage.</small></label>
    <label class="check"><input type="checkbox" id="dry" ${c.dry_run ? "checked" : ""}><span>Dry run by default<small>Applies preview changes without writing tags.</small></span></label>
    <div class="row mt"><button class="primary" type="submit">Save settings</button></div></form></div>`;
  $("#sf").onsubmit = guard(async (e) => {
    e.preventDefault();
    const r = await api("/config", { method: "PUT", json: { library_base: $("#lib").value, supported_exts: $("#exts").value.split(","), dry_run: $("#dry").checked, workers: +$("#workers").value || 8, excluded_folders: $("#excl").value.split(/[\n,]/), flat_folders: $("#flat").value.split(/[\n,]/), lastfm_api_key: $("#lfmclear")?.checked ? "" : ($("#lfmkey").value.trim() || null) } });
    toast(r.library_exists ? "Settings saved" : "Saved, but that path doesn't exist in the container", !r.library_exists);
    await loadState();
  });
}

route();
