/* NixNet Job Command front end. Vanilla JS, no CDN, works offline. */
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const app = $("#app");
let STATE = { jobs: [], stats: null, answers: [], settings: {}, policy: { companies: {}, banners: [] } };
const displayName = () => (STATE.stats && STATE.stats.display_name) || "there";
const firstName = () => (STATE.stats && STATE.stats.first_name) || displayName();
const logoUrl = () => (STATE.stats && STATE.stats.logo) || "/logo.svg";
const defaultResume = () => (STATE.stats && STATE.stats.default_resume) || "resume.pdf";
const resumeNote = () => (STATE.stats && STATE.stats.resume_note) || "";
const tzLabel = () => (STATE.stats && STATE.stats.timezone_label) ? " " + STATE.stats.timezone_label : "";
const legalName = () => (STATE.stats && STATE.stats.legal_name) || displayName();
const appName = () => (STATE.stats && STATE.stats.app_name) || "NixNet Job Command";
const brandName = () => (STATE.stats && STATE.stats.brand) || "nixfred";
const vaultName = () => (STATE.stats && STATE.stats.identity_vault) || "Identity";
const assistantName = () => (STATE.stats && STATE.stats.assistant_name) || "your assistant";
const STATUSES = ["To apply","Applied","Next steps","Interview scheduled","Offer","Rejected","Closed","Withdrawn"];

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "content-type": "application/json" }, ...opts,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
  if (!r.ok) { const t = await r.text(); toast("Error: " + t.slice(0, 120)); throw new Error(t); }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
}
function toast(msg, opts = {}) {
  const t = $("#toast");
  t.innerHTML = esc(msg) + (opts.undoRef ? ` <button class="btn s ok" id="toastUndo" type="button">Undo</button>` : "");
  t.classList.toggle("has-undo", !!opts.undoRef);
  t.classList.add("on");
  clearTimeout(t._h);
  if (opts.undoRef) {
    const ref = opts.undoRef;
    $("#toastUndo").onclick = async ev => {
      ev.preventDefault(); ev.stopPropagation();
      t.classList.remove("on", "has-undo");
      await api("/api/jobs/" + ref, { method: "PATCH", body: { undo_close: true } });
      toast("Restored previous status");
      route();
    };
  }
  t._h = setTimeout(() => t.classList.remove("on", "has-undo"), opts.undoRef ? 8000 : 1600);
}
const CLOSE_REASON = "Not accepting applications";
const isClosedOff = j => j && j.status === "Closed";
const canMarkNla = j => j && !["Closed", "Withdrawn", "Rejected"].includes(j.status);
function nlaButtons(j, { size = "", big = false } = {}) {
  const cls = "btn" + (size ? " " + size : "") + (big ? " big" : "");
  if (isClosedOff(j)) return `<button class="${cls} ok" type="button" data-undo-close="${esc(j.ref)}">Undo</button>`;
  if (!canMarkNla(j)) return "";
  return `<button class="${cls} danger" type="button" data-nla="${esc(j.ref)}">No longer accepting</button>`;
}

/* COPY: navigator.clipboard needs a secure context (localhost). Over the tailnet IP we fall back to execCommand. */
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
  } catch (e) {}
  const ta = document.createElement("textarea");
  ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.top = "-1000px";
  document.body.appendChild(ta); ta.select(); ta.setSelectionRange(0, text.length);
  let ok = false; try { ok = document.execCommand("copy"); } catch (e) {}
  ta.remove(); return ok;
}
document.addEventListener("click", async e => {
  const b = e.target.closest("[data-copy]");
  if (!b) return;
  e.preventDefault();
  const src = b.dataset.copy;
  let text = src.startsWith("#") ? ($(src)?.innerText ?? $(src)?.value ?? "") : src;
  const ok = await copyText(text.trim());
  const old = b.textContent; b.textContent = ok ? "Copied ✓" : "Copy failed"; setTimeout(() => b.textContent = old, 1200);
  toast(ok ? "Copied to clipboard" : "Select and copy by hand");
  if (b.dataset.answerId) api("/api/answers/" + b.dataset.answerId, { method: "PATCH", body: { used: (+b.dataset.used || 0) + 1 } }).catch(() => {});
});
document.addEventListener("click", async e => {
  const nla = e.target.closest("[data-nla]");
  const undo = e.target.closest("[data-undo-close]");
  if (!nla && !undo) return;
  e.preventDefault();
  const ref = (nla || undo).dataset.nla || (nla || undo).dataset.undoClose;
  const j = jobBy(ref);
  if (!j) return;
  if (nla) {
    if (!confirm("Mark " + j.company + " · " + j.role + " as no longer accepting applications?")) return;
    await api("/api/jobs/" + ref, { method: "PATCH", body: { status: "Closed", closed_reason: CLOSE_REASON } });
    toast("No longer accepting · " + j.company, { undoRef: ref });
  } else {
    await api("/api/jobs/" + ref, { method: "PATCH", body: { undo_close: true } });
    toast("Restored previous status");
  }
  const onQueue = location.hash.startsWith("#/queue");
  const onStretch = location.hash.startsWith("#/stretch");
  const prevHash = location.hash;
  await load();
  if (nla && (onQueue || onStretch)) {
    const Q = onStretch ? stretchApplyQueue() : toApplyQueue();
    const next = Q[0];
    const dest = next ? (onStretch ? "#/stretch/" : "#/queue/") + next.ref : (onStretch ? "#/stretch" : "#/queue");
    if (dest !== prevHash) location.hash = dest;
    else await route();
    scrollToTop();
  } else {
    await route();
  }
});

async function load() {
  const [jobs, stats, answers, settings, policy] = await Promise.all([
    api("/api/jobs"), api("/api/stats"), api("/api/answers"), api("/api/settings"), api("/api/company-policy")]);
  STATE = { jobs, stats, answers, settings, policy };
  $("#todayPill").innerHTML = `Today: <b>${stats.applied_today}</b> / ${stats.goal} applied · streak <b>${stats.streak}</b>`;
}
const jobBy = ref => STATE.jobs.find(j => j.ref === ref);
const coKey = n => String(n || "").trim().toLowerCase();
function companyState(name) {
  const map = (STATE.policy && STATE.policy.companies) || {};
  return map[coKey(name)] || null;
}
function isPaused(name) {
  const s = companyState(name);
  return !!(s && s.paused);
}
function isStretch(j) {
  const v = j && j.stretch;
  return v === true || v === 1 || v === "true" || v === "1";
}
function stretchBadge(j) {
  return isStretch(j) ? `<span class="pill stretch" title="${esc(j.stretch_reason || "Stretch role")}">Stretch</span>` : "";
}
function isHot(j) {
  const v = j && j.hot;
  return v === true || v === 1 || v === "true" || v === "1";
}
function hotBadge(j) {
  if (!isHot(j)) return "";
  const tip = j.due_label || j.due_at || "Hot lead";
  return `<span class="pill hot" title="${esc(tip)}">HOT</span>`;
}
function dueCallout(j) {
  if (!isHot(j) && !j.due_at && !j.due_label) return "";
  const when = j.due_label || j.due_at || "";
  return `<div class="callout hot-due" style="margin:12px 0 0"><b>HOT</b> Apply by hand${when ? ` before ${esc(when)}` : ""}.</div>`;
}
function toApplyQueue(jobs) {
  return (jobs || STATE.jobs).filter(j => j.status === "To apply" && !isPaused(j.company) && !isStretch(j));
}
function stretchApplyQueue(jobs) {
  return (jobs || STATE.jobs).filter(j => j.status === "To apply" && isStretch(j));
}
async function moveStretch(j, want, reason) {
  const body = { stretch: !!want };
  if (reason != null) body.stretch_reason = reason;
  const out = await api("/api/jobs/" + j.ref + "/stretch", { method: "POST", body });
  toast(want ? "Moved to Stretch" : "Moved to the main queue");
  return out;
}
function stretchReasonBox(j) {
  if (!isStretch(j) || !j.stretch_reason) return "";
  return `<div class="callout amber stretch-reason" style="margin:12px 0 0"><b>Stretch</b> ${esc(j.stretch_reason)}</div>`;
}
function stretchMoveBtn(j, cls = "btn") {
  if (j.status !== "To apply" && j.status !== "Closed") return "";
  if (isStretch(j)) {
    return `<button class="${cls}" type="button" data-stretch-off="${esc(j.ref)}">Move to main queue</button>`;
  }
  return `<button class="${cls}" type="button" data-stretch-on="${esc(j.ref)}">Move to Stretch</button>`;
}
async function onStretchMove(j, want) {
  let reason = j.stretch_reason || "";
  if (want && !reason) {
    reason = (prompt("Why is this a stretch? (pay, location, travel, role family, referral-only)") || "").trim();
    if (!reason) reason = "Moved to Stretch";
  }
  await moveStretch(j, want, want ? reason : j.stretch_reason);
  await load();
  await route();
}
function policyBanners(forCompany) {
  const companies = Object.values((STATE.policy && STATE.policy.companies) || {});
  const list = forCompany
    ? companies.filter(c => coKey(c.company) === coKey(forCompany) && c.banner)
    : companies.filter(c => c.banner && c.at_cap && !c.skip); // skipped companies are not listed (screen space); at-cap limits stay
  return list.map(c => {
    const resume = c.skip ? ` <button class="btn s" type="button" data-resume-co="${esc(c.company)}">Resume company</button>` : "";
    const policy = c.policy_url ? ` <a target="_blank" rel="noopener" href="${esc(c.policy_url)}">policy ↗</a>` : "";
    return `<div class="callout amber limit-banner" role="status"><b>${esc(c.banner)}</b>${policy}${resume}</div>`;
  }).join("");
}
function skipCompanyBtn(company, cls = "btn") {
  const st = companyState(company);
  if (st && st.skip) {
    return `<button class="${cls}" type="button" data-resume-co="${esc(company)}">Resume company</button>`;
  }
  return `<button class="${cls}" type="button" data-skip-co="${esc(company)}">Skip company</button>`;
}
async function openSkipCompany(company) {
  return new Promise(resolve => {
    const back = document.createElement("div");
    back.className = "modal-back";
    back.innerHTML = `<div class="modal" role="dialog" aria-labelledby="skipCoTitle">
      <h3 id="skipCoTitle">Skip company</h3>
      <p>Pause every To apply role at <b>${esc(company)}</b>. They leave the queue but stay To apply, not closed.</p>
      <label>Resume on (optional)</label>
      <input type="date" id="skipUntil">
      <div class="row" style="margin-top:14px">
        <button class="btn" type="button" id="skipCancel">Cancel</button>
        <button class="btn warn right" type="button" id="skipGo">Pause company</button>
      </div></div>`;
    document.body.appendChild(back);
    const close = async (ok) => {
      if (ok) {
        const until = ($("#skipUntil", back).value || "").trim();
        STATE.policy = await api("/api/company-skips", { method: "POST", body: { company, until: until || null } });
        toast(until ? `Paused ${company} until ${until}` : `Paused ${company}`);
      }
      back.remove();
      resolve(ok);
    };
    $("#skipCancel", back).onclick = () => close(false);
    $("#skipGo", back).onclick = () => close(true);
    back.addEventListener("click", e => { if (e.target === back) close(false); });
  });
}
async function resumeCompany(company) {
  STATE.policy = await api("/api/company-skips/resume", { method: "POST", body: { company } });
  toast("Resumed " + company);
  await load();
  await route();
}
const statusPill = s => `<span class="pill st-${esc(String(s).replace(/ /g, "-"))}">${esc(s)}</span>`;
const tierTag = t => t ? `<span class="tier ${esc(t)}">TIER ${esc(t)}</span>` : "";
const gcalLink = j => {
  if (!j.interview_at) return "";
  const s = new Date(j.interview_at), e = new Date(s.getTime() + (j.interview_minutes || 30) * 60000);
  const f = d => d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const p = new URLSearchParams({ action: "TEMPLATE", text: `Interview: ${j.company}, ${j.role}`, dates: `${f(s)}/${f(e)}`,
    details: `Job ${j.ref}. ${j.interview_with || ""}\n${j.posting_url || j.apply_url || ""}\n${j.interview_notes || ""}`, location: j.interview_location || "" });
  return "https://calendar.google.com/calendar/render?" + p.toString();
};

/* ---------- Router ---------- */
const routes = { "": dash, "progress": progressView, "queue": queue, "stretch": stretchQueue, "jobs": jobsView, "job": jobView, "answers": answersView, "referrals": referrals, "identity": identity, "feedback": feedback, "data": dataView };
function scrollToTop() {
  const jump = () => {
    try { window.scrollTo(0, 0); } catch (e) {}
    try { window.scrollTo({ top: 0, left: 0, behavior: "auto" }); } catch (e) {}
    const roots = [document.documentElement, document.body, document.scrollingElement,
      document.getElementById("app"), document.querySelector("main"), document.querySelector(".queue")];
    const nodes = roots.slice();
    try {
      document.querySelectorAll("*").forEach(el => {
        if (!el) return;
        let oy = false;
        try {
          const s = getComputedStyle(el);
          oy = s && /auto|scroll/.test(s.overflowY + s.overflow);
        } catch (e) {}
        if (oy || el.scrollTop > 0 || el.scrollLeft > 0) nodes.push(el);
      });
    } catch (e) {}
    for (const el of nodes) {
      if (!el) continue;
      try { if (el.scrollTo) el.scrollTo(0, 0); } catch (e) {}
      try { el.scrollTop = 0; el.scrollLeft = 0; } catch (e) {}
    }
  };
  const focusTop = () => {
    const h = document.getElementById("pageTop") || document.querySelector("#app h1, .queue .big, .hero h1, main h1");
    if (!h) { jump(); return; }
    if (!h.hasAttribute("tabindex")) h.setAttribute("tabindex", "-1");
    try { h.focus({ preventScroll: true }); } catch (e) { try { h.focus(); } catch (e2) {} }
    jump();
  };
  jump();
  requestAnimationFrame(() => { jump(); requestAnimationFrame(() => { focusTop(); jump(); }); });
  setTimeout(() => { jump(); focusTop(); }, 0);
  setTimeout(() => { jump(); focusTop(); }, 50);
  setTimeout(() => { jump(); focusTop(); }, 200);
  setTimeout(() => { jump(); focusTop(); }, 400);
}
async function route() {
  if (typeof exitScreenshotMode === "function") exitScreenshotMode();
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  const r = parts[0] || "";
  $$("nav a").forEach(a => a.classList.toggle("on", (a.dataset.r === (r || "dash")) || (r === "job" && a.dataset.r === "jobs")));
  $("#nav").classList.remove("open");
  try { await load(); } catch (e) { app.innerHTML = `<div class="card">Can't reach the server: ${esc(e.message)}</div>`; return; }
  const view = routes[r] || dash;
  await view(parts[1] ? decodeURIComponent(parts[1]) : undefined);
  scrollToTop();
}
window.addEventListener("hashchange", route);
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && document.body.classList.contains("screenshot-mode")) exitScreenshotMode();
});
if ("scrollRestoration" in history) history.scrollRestoration = "manual";

const squash = t => String(t || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
function jobMatches(j, q) {
  if (!q) return true;
  const hay = (j.search || [j.ref, j.company, j.role, j.location, j.status, j.remote || "", j.jd || ""].join(" ")).toLowerCase();
  const needle = String(q).trim().toLowerCase();
  if (hay.includes(needle)) return true;
  // "neo 4j", "neo-4j" and "Neo4j" are the same search: compare again with spaces and punctuation removed.
  const flat = squash(needle);
  if (flat === needle || flat.length < 4) return false;   // only when the search itself has a space or punctuation
  if (j._flat === undefined || j._flatSrc !== hay.length) { j._flat = squash(hay); j._flatSrc = hay.length; }
  return j._flat.includes(flat);
}
// Roles that match a search but are not in the list being searched (applied, closed, paused, stretch...).
// The queue only lists "To apply" roles, so without this an applied role vanishes from its own search.
function outsideMatches(q, inList) {
  if (!q || !String(q).trim()) return [];
  const have = new Set((inList || []).map(j => j.ref));
  return STATE.jobs.filter(j => !have.has(j.ref) && jobMatches(j, q));
}
function outsideCard(q, inList, stretchMode) {
  const L = outsideMatches(q, inList);
  if (!L.length) return "";
  const rows = L.slice(0, 12).map(j => `<div class="row small" style="margin:5px 0"><span class="ref">${esc(j.ref)}</span>${statusPill(j.status)}<a href="#/job/${esc(j.ref)}">${esc(j.company)} · ${esc(j.role)}</a>${j.date_applied ? `<span class="muted right">applied ${esc(j.date_applied)}</span>` : ""}</div>`).join("");
  return `<div class="card" id="outsideMatches" style="margin:12px 0"><div class="row"><h3 style="margin:0">Also tracked, not in the ${stretchMode ? "stretch" : "apply"} queue (${L.length})</h3><button class="btn s right" type="button" id="openInJobs">Open all in Jobs</button></div>
    <p class="muted small" style="margin:6px 0">The queue only lists roles still to apply. These already left it (applied, closed, paused or in the other queue).</p>${rows}${L.length > 12 ? `<div class="muted small">+${L.length - 12} more in Jobs</div>` : ""}</div>`;
}
function bindOutside(q) {
  const b = $("#openInJobs");
  if (b) b.onclick = () => { localStorage.setItem("jobsFilter", JSON.stringify({ q })); location.hash = "#/jobs"; };
}
function bindEscSearch(input, ondraw) {
  if (!input) return;
  input.addEventListener("input", ondraw);
  input.addEventListener("keydown", e => {
    if (e.key === "Escape") { e.preventDefault(); input.value = ""; ondraw(); }
  });
}
const LATER = new Set(["Applied", "Next steps", "Interview scheduled", "Offer", "Rejected"]);
function normUrl(u) {
  if (!u) return "";
  try {
    const x = new URL(u, "https://example.invalid");
    return (x.origin + x.pathname).replace(/\/+$/, "").toLowerCase();
  } catch (e) {
    return String(u).split("?")[0].split("#")[0].replace(/\/+$/, "").toLowerCase();
  }
}
function jobUrl(j) { return normUrl(j.apply_url) || normUrl(j.posting_url); }
function normRole(r) { return String(r || "").toLowerCase().replace(/&/g, " and ").replace(/[^a-z0-9]+/g, " ").trim(); }
function roleRatio(a, b) {
  if (!a || !b) return 0;
  if (a === b) return 1;
  const la = a.length, lb = b.length, max = Math.max(la, lb);
  if (!max) return 0;
  const dp = new Array(lb + 1);
  for (let j = 0; j <= lb; j++) dp[j] = j;
  for (let i = 1; i <= la; i++) {
    let prev = dp[0];
    dp[0] = i;
    for (let j = 1; j <= lb; j++) {
      const cur = dp[j];
      dp[j] = a[i - 1] === b[j - 1] ? prev : 1 + Math.min(prev, dp[j], dp[j - 1]);
      prev = cur;
    }
  }
  return 1 - dp[lb] / max;
}
function roleNear(a, b) {
  if (!a || !b) return false;
  if (a === b) return true;
  if (a.includes(b) || b.includes(a)) {
    const shorter = a.length < b.length ? a : b;
    return shorter.split(" ").filter(Boolean).length >= 3 || shorter.length >= 20;
  }
  const A = new Set(a.split(" ").filter(Boolean)), B = new Set(b.split(" ").filter(Boolean));
  let inter = 0; A.forEach(t => { if (B.has(t)) inter++; });
  const uni = new Set([...A, ...B]).size;
  const jacc = uni ? inter / uni : 0;
  const ra = roleRatio(a, b);
  return (jacc >= 0.85 && ra >= 0.8) || ra >= 0.95;
}
function isLater(j) { return LATER.has(j.status) || (!!j.date_applied && j.status !== "To apply"); }
function findDuplicates(job, jobs) {
  const url = jobUrl(job), role = normRole(job.role), company = String(job.company || "").trim().toLowerCase();
  return (jobs || []).filter(o => {
    if (!o || o.ref === job.ref) return false;
    if (!isLater(o)) return false;
    const urlHit = url && jobUrl(o) && url === jobUrl(o);
    const roleHit = company && company === String(o.company || "").trim().toLowerCase() && roleNear(role, normRole(o.role));
    return urlHit || roleHit;
  });
}
function dupBanner(j) {
  const dups = findDuplicates(j, STATE.jobs);
  if (!dups.length) return "";
  const bits = dups.map(d => {
    const when = d.date_applied ? ` applied on ${esc(d.date_applied)}` : ` (${esc(d.status)})`;
    return `<a href="#/job/${esc(d.ref)}">${esc(d.ref)}</a>${when}`;
  });
  return `<div class="callout danger dup-banner" role="alert"><b>Possible duplicate:</b> ${bits.join("; ")}.</div>`;
}
function confirmDuplicates(j) {
  const dups = findDuplicates(j, STATE.jobs);
  if (!dups.length) return true;
  const msg = dups.map(d => `${d.ref} ${d.company} · ${d.role} (${d.status}${d.date_applied ? ", applied " + d.date_applied : ""})`).join("\n");
  return confirm("Possible duplicate. Already on file:\n" + msg + "\n\nMark " + j.ref + " as Applied anyway?");
}
$("#menuBtn").onclick = () => $("#nav").classList.toggle("open");

/* ---------- Charts (inline SVG) ---------- */
function barChart(pairs, { h = 160, color = "#2bb39a", goal } = {}) {
  const w = Math.max(320, pairs.length * 26), max = Math.max(1, goal || 0, ...pairs.map(p => p[1])), bw = w / pairs.length;
  let s = `<svg viewBox="0 0 ${w} ${h + 22}" width="100%" preserveAspectRatio="none" role="img">`;
  if (goal) { const y = h - (goal / max) * (h - 14); s += `<line x1="0" x2="${w}" y1="${y}" y2="${y}" stroke="#f0b44c" stroke-dasharray="4 4"/><text x="4" y="${y - 3}">goal ${goal}</text>`; }
  pairs.forEach(([l, v], i) => {
    const bh = (v / max) * (h - 14), x = i * bw + 3;
    s += `<rect x="${x}" y="${h - bh}" width="${bw - 6}" height="${bh}" rx="4" fill="${color}" opacity="${v ? 1 : .25}"><title>${esc(l)}: ${v}</title></rect>`;
    if (v) s += `<text x="${x + (bw - 6) / 2}" y="${h - bh - 3}" text-anchor="middle" style="fill:#e6edf1">${v}</text>`;
    if (pairs.length <= 31 && (i % Math.ceil(pairs.length / 12) === 0)) s += `<text x="${x}" y="${h + 16}">${esc(l)}</text>`;
  });
  return s + "</svg>";
}
function hbars(pairs, color = "#4aa3ff") {
  const max = Math.max(1, ...pairs.map(p => p[1]));
  return pairs.map(([l, v]) => `<div class="row small" style="margin:6px 0"><span style="width:170px" class="muted">${esc(l)}</span><div class="bar" style="flex:1"><i style="width:${(v / max) * 100}%;background:${color}"></i></div><b style="width:34px;text-align:right">${v}</b></div>`).join("");
}
function ring(v, goal) {
  const p = Math.min(1, v / Math.max(1, goal)), r = 44, c = 2 * Math.PI * r;
  return `<svg width="120" height="120" viewBox="0 0 120 120"><circle cx="60" cy="60" r="${r}" stroke="#1d2a33" stroke-width="12" fill="none"/><circle cx="60" cy="60" r="${r}" stroke="#5fe0b5" stroke-width="12" fill="none" stroke-linecap="round" stroke-dasharray="${c * p} ${c}" transform="rotate(-90 60 60)"/><text x="60" y="58" text-anchor="middle" style="fill:#e6edf1;font-size:26px;font-weight:800">${v}</text><text x="60" y="78" text-anchor="middle">of ${goal} today</text></svg>`;
}


function fmtShortDate(iso) {
  const [y, m, d] = String(iso).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
function fmtLongDate(iso) {
  const [y, m, d] = String(iso).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}
function progressChart(days, goal) {
  const n = Math.max(1, days.length);
  const padL = 40, padR = 48, padT = 28, padB = 40;
  const cols = Math.max(n, 12);
  const plotW = Math.max(cols * 40, 560);
  const plotH = 230;
  const w = padL + plotW + padR;
  const h = padT + plotH + padB;
  const maxBar = Math.max(goal || 0, 1, ...days.map(d => d.count));
  const maxCum = Math.max(1, ...days.map(d => d.cumulative));
  const yBar = v => padT + plotH - (v / maxBar) * plotH;
  const yCum = v => padT + plotH - (v / maxCum) * plotH;
  const slot = plotW / cols;
  const bw = Math.min(40, Math.max(cols > n ? 36 : 14, slot * 0.62));
  const xAt = i => padL + (i + 0.5) * slot;
  let s = `<svg class="progress-svg" viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="Applications per day and running total">`;
  const ticks = 4;
  for (let i = 0; i <= ticks; i++) {
    const v = (maxBar / ticks) * i;
    const y = yBar(v);
    s += `<line x1="${padL}" x2="${padL + plotW}" y1="${y}" y2="${y}" stroke="#243039" stroke-width="1"/>`;
    s += `<text x="${padL - 8}" y="${y + 4}" text-anchor="end" style="fill:#8aa0ad;font-size:11px">${Math.round(v)}</text>`;
    const cv = (maxCum / ticks) * i;
    s += `<text x="${padL + plotW + 8}" y="${y + 4}" text-anchor="start" style="fill:#4aa3ff;font-size:11px">${Math.round(cv)}</text>`;
  }
  if (goal) {
    const y = yBar(goal);
    s += `<line x1="${padL}" x2="${padL + plotW}" y1="${y}" y2="${y}" stroke="#f0b44c" stroke-width="1.5" stroke-dasharray="5 4"/>`;
    s += `<text x="${padL + plotW - 6}" y="${y - 6}" text-anchor="end" style="fill:#f0b44c;font-size:11px;font-weight:700">goal ${goal}</text>`;
  }
  (days.length ? days : [{ date: "", count: 0, cumulative: 0 }]).forEach((d, i) => {
    const x = xAt(i) - bw / 2;
    const y = yBar(d.count);
    const bh = Math.max(d.count ? 3 : 0, padT + plotH - y);
    const fill = goal && d.count >= goal ? "#5fe0b5" : "#2bb39a";
    s += `<rect x="${x}" y="${y}" width="${bw}" height="${bh}" rx="5" fill="${fill}" opacity="${d.count ? 1 : .22}"><title>${esc(d.date)}: ${d.count} applied, ${d.cumulative} total</title></rect>`;
    if (d.count) {
      const labelY = Math.abs(y - yCum(d.cumulative)) < 16 && bh > 22 ? y + 16 : y - 6;
      const labelFill = labelY > y ? "#04110d" : "#e6edf1";
      s += `<text x="${xAt(i)}" y="${labelY}" text-anchor="middle" style="fill:${labelFill};font-size:12px;font-weight:700">${d.count}</text>`;
    }
    const step = n <= 14 ? 1 : Math.ceil(n / 12);
    if (d.date && (i % step === 0 || i === n - 1)) {
      s += `<text x="${xAt(i)}" y="${padT + plotH + 18}" text-anchor="middle" style="fill:#8aa0ad;font-size:11px">${esc(fmtShortDate(d.date))}</text>`;
    }
  });
  if (days.length) {
    const pts = days.map((d, i) => `${xAt(i)},${yCum(d.cumulative)}`).join(" ");
    s += `<polyline fill="none" stroke="#4aa3ff" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" points="${pts}"/>`;
    days.forEach((d, i) => {
      s += `<circle cx="${xAt(i)}" cy="${yCum(d.cumulative)}" r="4" fill="#0b0f12" stroke="#4aa3ff" stroke-width="2"><title>Running total ${d.cumulative}</title></circle>`;
    });
  }
  return s + "</svg>";
}
function exitScreenshotMode() {
  document.body.classList.remove("screenshot-mode");
  const b = $("#shotMode");
  if (b) b.textContent = "Prepare for screenshot";
}
async function progressView() {
  exitScreenshotMode();
  const p = await api("/api/progress");
  const range = p.start_date === p.today ? `Since ${fmtLongDate(p.start_date)}` : `${fmtLongDate(p.start_date)} – ${fmtLongDate(p.today)}`;
  const avg = Number(p.daily_average).toLocaleString("en-US", { maximumFractionDigits: 1 });
  const bestLabel = p.best_day && p.best_day.date ? "Best day · " + fmtShortDate(p.best_day.date) : "Best day";
  const before = p.before_reset
    ? `<p class="muted small progress-note">Reset started ${fmtLongDate(p.start_date)}. ${p.before_reset} application${p.before_reset === 1 ? "" : "s"} before that are not in this chart.</p>`
    : "";
  const extras = (p.unmapped_extras || []).length
    ? `<p class="muted small progress-note">Plus ${p.unmapped_extras.length} application${p.unmapped_extras.length === 1 ? "" : "s"} not yet tied to a job card.</p>`
    : "";
  app.innerHTML = `
  <div class="progress-page" id="pageTop" tabindex="-1">
    <div class="progress-tools row">
      <button class="btn s" id="shotMode" type="button">Prepare for screenshot</button>
      <span class="muted small right">Daily goal</span>
      <input id="goalP" type="number" min="1" max="60" value="${p.goal}" style="width:80px">
      <button class="btn s" id="saveGoalP" type="button">Save</button>
    </div>
    <div class="card progress-share" id="progressShare">
      <div class="progress-brand">
        <img src="${esc(logoUrl())}" alt="">
        <div>
          <h1>${esc(displayName())} · Job Hunt Progress</h1>
          <p class="sub" style="margin:4px 0 0">${esc(range)} · ${p.total} application${p.total === 1 ? "" : "s"} since the reset</p>
        </div>
      </div>
      <div class="progress-kpis">
        <div class="card kpi t"><div class="n">${p.total}</div><div class="l">Since ${fmtShortDate(p.start_date)}</div></div>
        <div class="card kpi b"><div class="n">${p.today_count}</div><div class="l">Today</div></div>
        <div class="card kpi v"><div class="n">${p.this_week}</div><div class="l">This week</div></div>
        <div class="card kpi a"><div class="n">${p.streak}</div><div class="l">Day streak</div></div>
        <div class="card kpi t"><div class="n">${p.best_day ? p.best_day.count : 0}</div><div class="l">${esc(bestLabel)}</div></div>
        <div class="card kpi b"><div class="n">${avg}</div><div class="l">Daily average</div></div>
      </div>
      <div class="progress-chart-card">
        <div class="row" style="justify-content:space-between;align-items:baseline;margin-bottom:8px;flex-wrap:wrap">
          <h3 style="margin:0">Applications per day</h3>
          <div class="progress-legend">
            <span><i class="lg-bar"></i> Daily</span>
            <span><i class="lg-line"></i> Running total</span>
            <span><i class="lg-goal"></i> Daily goal (${p.goal})</span>
          </div>
        </div>
        <div class="progress-chart-wrap">${progressChart(p.days || [], p.goal)}</div>
      </div>
      ${before}${extras}
    </div>
    <button class="btn s shot-exit" id="shotExit" type="button">Exit screenshot mode</button>
  </div>`;
  $("#saveGoalP").onclick = async () => {
    const v = +$("#goalP").value;
    if (!v || v < 1) return toast("Set a goal of at least 1");
    await api("/api/settings", { method: "POST", body: { daily_goal: v } });
    toast("Goal saved");
    route();
  };
  const toggleShot = on => {
    document.body.classList.toggle("screenshot-mode", on);
    $("#shotMode").textContent = on ? "Exit screenshot mode" : "Prepare for screenshot";
    if (on) toast("Chrome hidden. Screenshot the card, then press Esc.");
  };
  $("#shotMode").onclick = () => toggleShot(!document.body.classList.contains("screenshot-mode"));
  $("#shotExit").onclick = () => { exitScreenshotMode(); };
}

/* ---------- Dashboard ---------- */
function dash() {
  const s = STATE.stats, bs = s.by_status;
  const days = []; for (let i = 20; i >= 0; i--) { const d = new Date(); d.setDate(d.getDate() - i); const k = d.toLocaleDateString("en-CA"); days.push([k.slice(5), s.per_day[k] || 0]); }
  const next = toApplyQueue().slice(0, 6);
  const queued = (s.queued_to_apply != null) ? s.queued_to_apply : next.length;
  const pausedN = s.paused_to_apply || 0;
  const stretchN = s.stretch_to_apply || 0;
  const appliedAll = (s.applied_all_time != null) ? s.applied_all_time : ((s.funnel && s.funnel[1]) ? s.funnel[1][1] : 0);
  const census = s.census || [];
  const censusHtml = census.map((row, i) => {
    const label = row[0], n = row[1];
    const klass = ["t", "a", "b", "v", "r", "m", "m"][i] || "m";
    return `<div class="card kpi ${klass}"><div class="n">${n}</div><div class="l">${esc(label)}</div></div>`;
  }).join("");
  const trackedLine = s.tracked_line || ("Total tracked: " + (s.total || 0));
  app.innerHTML = `
  <div class="hero"><div><h1>Good ${new Date().getHours() < 12 ? "morning" : new Date().getHours() < 18 ? "afternoon" : "evening"}, ${esc(firstName())}.</h1>
  <p class="sub">${queued} roles are in the apply queue${stretchN ? ` · ${stretchN} stretch` : ""}${pausedN ? ` · ${pausedN} paused by company skip or apply limit` : ""}. Best fit and brand names are first. One click opens the application, the 1Password identity fills it, and every answer has a COPY button.</p></div>
  <div class="row">${queued ? `<a class="btn p big" href="#/queue">Start today's apply queue →</a>` : `<a class="btn p big" href="#/queue">Apply queue</a>`}${stretchN ? ` <a class="btn big" href="#/stretch">Work Stretch queue →</a>` : ""}</div></div>
  ${policyBanners()}
  <div class="grid g4">
    <div class="card kpi a"><div class="n">${bs["To apply"]}</div><div class="l">To apply</div><div class="s">${queued} in queue · ${stretchN} stretch · ${pausedN} paused</div></div>
    <div class="card kpi b"><div class="n">${appliedAll}</div><div class="l">Applied, all time</div><div class="s">includes interviews and rejects after applying</div></div>
    <div class="card kpi t"><div class="n">${(bs["Interview scheduled"] || 0) + (bs["Next steps"] || 0) + (bs["Offer"] || 0)}</div><div class="l">Next steps + interviews</div></div>
    <div class="card kpi v"><div class="n">${s.streak}</div><div class="l">Day streak</div></div>
  </div>
  <div class="census">${censusHtml}</div>
  <p class="census-total">${esc(trackedLine)}</p>
  <div class="grid g3" style="margin-top:14px">
    <div class="card" style="grid-column:span 2"><h3>Applications per day (last 21 days) <a href="#/progress" class="small" style="font-weight:600;text-transform:none;letter-spacing:0;float:right">Progress page →</a></h3>${barChart(days, { goal: s.goal })}</div>
    <div class="card" style="text-align:center"><h3>Today's goal</h3>${ring(s.applied_today, s.goal)}
      <div class="row" style="justify-content:center;margin-top:6px"><span class="muted small">Daily goal</span><input id="goal" type="number" min="1" max="60" value="${s.goal}" style="width:80px"><button class="btn s" id="saveGoal">Save</button></div></div>
  </div>
  <div class="grid g3" style="margin-top:14px">
    <div class="card"><h3>Pipeline funnel</h3>${hbars(s.funnel, "#2bb39a")}</div>
    <div class="card"><h3>By status</h3>${hbars(STATUSES.map(x => [x, bs[x] || 0]))}</div>
    <div class="card"><h3>Where they came from</h3>${hbars(Object.entries(s.by_source).sort((a, b) => b[1] - a[1]), "#a78bfa")}</div>
  </div>
  <div class="grid g3" style="margin-top:14px">
    <div class="card"><h3>Up next</h3>${next.map(j => `<div class="row" style="margin:7px 0"><span class="ref">${j.ref}</span><a href="#/job/${j.ref}"><b>${esc(j.company)}</b> ${esc(j.role)}</a><a class="btn s p right" target="_blank" rel="noopener" href="${esc(j.apply_url || j.posting_url)}">Apply ↗</a></div>`).join("") || '<p class="muted">Queue is empty. Nice.</p>'}</div>
    <div class="card"><h3>Upcoming interviews</h3>${s.upcoming.map(u => `<div style="margin:7px 0"><a href="#/job/${u.ref}"><b>${esc(u.company)}</b></a> <span class="muted small">${esc(new Date(u.at).toLocaleString())}</span></div>`).join("") || '<p class="muted">None scheduled yet.</p>'}</div>
    <div class="card"><h3>Follow up</h3>${s.followups.map(u => `<div style="margin:7px 0"><a href="#/job/${u.ref}"><b>${esc(u.company)}</b></a> <span class="muted small">${esc(u.at)}</span></div>`).join("")}${s.stale.map(u => `<div style="margin:7px 0"><a href="#/job/${u.ref}"><b>${esc(u.company)}</b></a> <span class="muted small">applied ${esc(u.date_applied)}, no reply in 10+ days</span></div>`).join("") || (s.followups.length ? "" : '<p class="muted">Nothing due.</p>')}</div>
  </div>`;
  $("#saveGoal").onclick = async () => { await api("/api/settings", { method: "POST", body: { daily_goal: +$("#goal").value } }); toast("Goal saved"); route(); };
}

/* ---------- Jobs list ---------- */
function jobsView() {
  const srcs = [...new Set(STATE.jobs.map(j => j.source_group || j.source).filter(Boolean))].sort();
  // Only the search text and sort order are remembered. The status, fit, source and tier dropdowns always start
  // at "All", so a filter left over from an earlier visit can never hide a role you are looking for.
  const saved = (() => { const o = JSON.parse(localStorage.getItem("jobsFilter") || "{}"); return { q: o.q || "", sort: o.sort || "" }; })();
  app.innerHTML = `<h1 id="pageTop" tabindex="-1">Jobs</h1>
  <div class="search-row"><input id="q" class="search-box" type="search" placeholder="Search company, role, ref, location, status or JD…" value="${esc(saved.q || "")}" autocomplete="off"><span class="match-count" id="matchCount"></span></div>
  <p class="sub">${STATE.jobs.length} opportunities. Every one is an object with dozens of attributes; open one for answers, the JD, screenshots and the timeline.</p>
  <div class="toolbar">
    <select id="fs"><option value="">All statuses</option>${STATUSES.map(s => `<option ${saved.fs === s ? "selected" : ""}>${s}</option>`).join("")}</select>
    <select id="fst"><option value="">All fits</option><option value="stretch"${saved.fst === "stretch" ? " selected" : ""}>Stretch only</option><option value="main"${saved.fst === "main" ? " selected" : ""}>Main queue only</option></select>
    <select id="fsrc"><option value="">All sources</option>${srcs.map(s => `<option ${saved.fsrc === s ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
    <select id="ft"><option value="">All tiers</option><option ${saved.ft === "A" ? "selected" : ""}>A</option><option ${saved.ft === "B" ? "selected" : ""}>B</option><option ${saved.ft === "C" ? "selected" : ""}>C</option></select>
    <select id="sort">${[["rank", "Sort: apply order"], ["fit", "Fit score"], ["company", "Company"], ["comp", "Top of pay range"], ["updated", "Recently updated"]].map(([v, t]) => `<option value="${v}"${saved.sort === v ? " selected" : ""}>${t}</option>`).join("")}</select>
    <a class="btn" href="#/data">+ Add / import</a>
  </div><div class="callout amber" id="hiddenNote" style="display:none;margin:0 0 12px"></div><div class="card list" id="list"></div>`;
  const draw = () => {
    const q = $("#q").value, fs = $("#fs").value, fsrc = $("#fsrc").value, ft = $("#ft").value, fst = $("#fst").value, sort = $("#sort").value;
    localStorage.setItem("jobsFilter", JSON.stringify({ q, sort }));
    let L = STATE.jobs.filter(j => (!fs || j.status === fs) && (!fsrc || (j.source_group || j.source) === fsrc) && (!ft || j.tier === ft) && (fst !== "stretch" || isStretch(j)) && (fst !== "main" || !isStretch(j)) && jobMatches(j, q));
    const top = j => +(String(j.comp || "").match(/\$?([\d.]+)K?\s*(?:$|[^\d]*$)/) || [0, 0])[1] || 0;
    if (sort === "fit") L.sort((a, b) => (b.fit_score || 0) - (a.fit_score || 0));
    if (sort === "company") L.sort((a, b) => a.company.localeCompare(b.company));
    if (sort === "comp") L.sort((a, b) => (b.comp_max || 0) - (a.comp_max || 0));
    if (sort === "updated") L.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
    const mc = $("#matchCount"); if (mc) mc.textContent = `Showing ${L.length} of ${STATE.jobs.length} roles`;
    const filtered = !!(fs || fsrc || ft || fst);
    const hiddenN = filtered && q ? STATE.jobs.filter(j => jobMatches(j, q)).length - L.length : 0;
    const hn = $("#hiddenNote");
    if (hn) { hn.innerHTML = hiddenN > 0 ? `<b>${hiddenN} more match "${esc(q)}"</b> but are hidden by the filters above. <a href="#" id="clearFilters">Clear filters</a>` : ""; hn.style.display = hiddenN > 0 ? "" : "none"; const cf = $("#clearFilters"); if (cf) cf.onclick = e => { e.preventDefault(); ["fs", "fsrc", "ft", "fst"].forEach(id => { $("#" + id).value = ""; }); draw(); }; }
    $("#list").innerHTML = L.map(j => `<div class="jobrow"><span class="ref">${j.ref}</span>
      <div><a class="t" href="#/job/${j.ref}">${esc(j.company)} · ${esc(j.role)}</a>
      <div class="m">${statusPill(j.status)} ${hotBadge(j)}${stretchBadge(j)} ${isPaused(j.company) && j.status === "To apply" ? `<span class="pill">Paused</span>` : ""} ${tierTag(j.tier)} <span>Fit ${j.fit_score ?? "?"}</span><span>${esc(j.comp || "Pay not posted")}</span><span>${esc(j.location || "")}${j.remote ? " · " + esc(j.remote) : ""}</span><span>${esc(j.source || "")}</span>${j.n_questions ? `<span>${j.n_questions} form questions</span>` : ""}</div></div>
      <div class="acts"><a class="btn ${j.status === "To apply" ? "p" : ""}" target="_blank" rel="noopener" href="${esc(j.apply_url || j.posting_url)}">${j.status === "To apply" ? "Apply ↗" : "Posting ↗"}</a><a class="btn" href="#/job/${j.ref}">Open</a>${nlaButtons(j, { size: "s" })}</div></div>`).join("") || '<p class="muted" style="padding:16px">No matches.</p>';
  };
  ["fs", "fsrc", "ft", "sort"].forEach(id => $("#" + id).addEventListener("input", draw));
  bindEscSearch($("#q"), draw);
  draw();
}

/* ---------- Job detail ---------- */
async function jobView(ref) {
  const j = await api("/api/jobs/" + ref);
  const lib = STATE.answers;
  const fld = (k, label, type = "text", ph = "") => `<label>${label}</label>${type === "textarea"
    ? `<textarea data-f="${k}" placeholder="${esc(ph)}">${esc(j[k] || "")}</textarea>`
    : `<input data-f="${k}" type="${type}" value="${esc(j[k] || "")}" placeholder="${esc(ph)}">`}`;
  const answers = (j.answers || []);
  app.innerHTML = `
  <p class="small"><a href="#/jobs">← All jobs</a> · <span class="ref">${j.ref}</span> · queue #${((STATE.jobs.find(x => x.ref === j.ref) || j).queue_rank) || "-"}</p>
  <div class="hero"><div style="flex:1;min-width:280px"><h1 id="pageTop" tabindex="-1">${esc(j.company)} · ${esc(j.role)}</h1>
    <div class="row">${statusPill(j.status)} ${hotBadge(j)}${stretchBadge(j)} ${isPaused(j.company) && j.status === "To apply" ? `<span class="pill">Paused</span>` : ""} ${tierTag(j.tier)} <span class="tag">Fit ${j.fit_score ?? "?"}/100</span><span class="tag">${esc(j.comp || "Pay not posted")}</span><span class="tag">${esc(j.location || "")}</span>${j.remote ? `<span class="tag">${esc(j.remote)}</span>` : ""}${j.posted ? `<span class="tag">Posted ${esc(j.posted)}</span>` : ""}${j.ats ? `<span class="tag">${esc(j.ats)}</span>` : ""}</div></div>
    <div class="row"><a class="btn p big" target="_blank" rel="noopener" href="${esc(j.apply_url || j.posting_url)}">Open application ↗</a>${j.posting_url && j.posting_url !== j.apply_url ? `<a class="btn" target="_blank" rel="noopener" href="${esc(j.posting_url)}">Posting ↗</a>` : ""}${skipCompanyBtn(j.company)}${stretchMoveBtn(j)}${nlaButtons(j, { big: true })}</div></div>
  ${policyBanners(j.company)}
  ${dueCallout(j)}${stretchReasonBox(j)}
  ${j.status === "Closed" && (j.closed_reason || j.closed_at) ? `<div class="callout amber" style="margin:12px 0 0"><b>Closed</b> ${j.closed_at ? esc(j.closed_at.replace("T", " ")) + tzLabel() : ""}: ${esc(j.closed_reason || "Closed")}. Use Undo if that was a mistake.</div>` : ""}
  <div class="stepper" id="stepper">${STATUSES.map(s => `<button data-s="${s}" class="${s === j.status ? "on" : ""}">${s}</button>`).join("")}</div>
  ${dupBanner(j)}
  ${whoContactsCard(j)}
  ${customAnswersCard(j, "jca")}
  <div class="grid g3">
    <div class="callout"><h3>1 · Fill with 1Password</h3><div class="row"><b id="opid">${esc(j.onepassword_identity || legalName())}</b><button class="btn s copy" data-copy="#opid">Copy name</button></div><p class="small muted">Vault: ${esc(vaultName())}. Click the form's first field, then pick this Identity from the 1Password menu.</p></div>
    <div class="callout"><h3>2 · Resume and letter</h3><div><b>${esc(j.resume_version || defaultResume())}</b></div><div class="small muted">${esc(j.cover_letter || "No cover letter needed")}</div><p class="small muted">${esc(resumeNote())}</p></div>
    <div class="callout amber"><h3>3 · Copy the answers below</h3><p class="small">${answers.length} pre-answered questions for this job${(j.questions || []).length ? `, from the real ${esc(j.ats || "")} form (${j.questions.length} fields)` : ""}. Then paste the confirmation screenshot and mark Applied.</p></div>
  </div>
  <div class="grid g2" style="margin-top:14px;align-items:start">
    <div>
      <div class="card"><h2>Answers for this application</h2>
        ${answers.map((a, i) => `<div class="ans ${a.needs_review ? "fd" : ""}"><div class="q"><span>${esc(a.q)}${a.required ? ' <span class="pill">required</span>' : ""}</span><button class="btn s copy" data-copy="#ja${i}">COPY</button></div><div class="a" id="ja${i}" contenteditable="true" data-i="${i}">${esc(a.a)}</div></div>`).join("") || '<p class="muted">No job-specific answers yet. Use the library.</p>'}
        <p class="small muted">Answers are editable: click into one, change it, and it saves when you click away. Ask ${esc(assistantName())} to help rewrite a question if you want a second pass.</p>
        <div class="row"><button class="btn s" data-copy="Help me answer the application questions for ${j.ref} (${esc(j.company)}, ${esc(j.role)}).">Copy a rewrite request</button></div>
      </div>
      ${(j.questions || []).length ? `<div class="card" style="margin-top:14px"><h2>The form, field by field (${esc(j.ats || "")})</h2><table><tr><th>Field</th><th>Type</th><th>Req</th><th>Fill with</th></tr>${j.questions.map(q => `<tr><td>${esc(q.label)}</td><td class="muted small">${esc(q.type || "")}</td><td>${q.required ? "yes" : ""}</td><td class="small">${esc(q.fill || "")}</td></tr>`).join("")}</table></div>` : ""}
      <div class="card" style="margin-top:14px"><h2>Why it fits</h2><p>${esc(j.fit_notes || "")}</p>${j.gaps ? `<p class="small"><b>Gaps to address:</b> ${esc(j.gaps)}</p>` : ""}${j.demo_idea ? `<p class="small"><b>Demo lab idea:</b> ${esc(j.demo_idea)}</p>` : ""}${(j.skills || []).length ? `<div class="row">${j.skills.map(s => `<span class="tag">${esc(s)}</span>`).join("")}</div>` : ""}</div>
      <div class="card" style="margin-top:14px"><details ${j.jd ? "" : "open"}><summary>Job description ${j.jd ? `(${j.jd.length.toLocaleString()} chars, crawled ${esc(j.crawled_at || "")})` : "(not crawled)"}</summary><div class="row" style="margin:10px 0"><button class="btn s" data-copy="#jdtext">Copy JD</button></div><div class="jd" id="jdtext">${esc(j.jd || "The posting could not be crawled. Open it with the Posting button.")}</div></details></div>
    </div>
    <div>
      <div class="card"><h2>Tracking</h2><div class="kv" id="kv">
        ${fld("date_applied", "Date applied", "date")}
        ${fld("gmail_receipt_url", "Gmail receipt URL", "url", "https://mail.example.com/receipt/…")}
        ${fld("interview_at", "Interview date/time", "datetime-local")}
        ${fld("interview_minutes", "Interview length (min)", "number")}
        ${fld("interview_with", "Interviewer(s)")}
        ${fld("interview_location", "Zoom / Meet / place")}
        ${fld("follow_up", "Follow-up date", "date")}
        ${fld("contacts", "Recruiter / contacts", "textarea", "Name, role, email")}
        ${fld("onepassword_identity", "1Password Identity")}
        ${fld("resume_version", "Resume used")}
        ${fld("cover_letter", "Cover letter")}
        ${fld("notes", "Notes", "textarea")}
        ${fld("feedback", "Outcome feedback", "textarea", "What they said, what to change next time")}
      </div>
      <div class="row" style="margin-top:12px">${(j.gmail_receipt_url || j.receipt_url) ? `<a class="btn ok" target="_blank" rel="noopener" href="${esc(j.gmail_receipt_url || j.receipt_url)}">Open receipt ↗</a>` : ""}
        ${j.interview_at ? `<a class="btn ok" target="_blank" rel="noopener" href="${esc(gcalLink(j))}">Add to Google Calendar ↗</a><a class="btn" href="/api/jobs/${j.ref}/ics">Download .ics</a>` : `<span class="muted small">Set an interview time to get the Google Calendar link and .ics.</span>`}</div></div>
      <div class="card" style="margin-top:14px"><h2>Saved links</h2><div id="urls">${(j.saved_urls || []).map((u, i) => `<div class="row" style="margin:5px 0"><a target="_blank" rel="noopener" href="${esc(u.url)}">${esc(u.label || u.url)}</a><button class="btn s right" data-rmurl="${i}">Remove</button></div>`).join("") || '<p class="muted small">None yet.</p>'}</div>
        <div class="row" style="margin-top:8px"><input id="nuLabel" placeholder="Label" style="width:140px"><input id="nuUrl" placeholder="https://…" style="flex:1"><button class="btn s" id="addUrl">Add</button></div></div>
      <div class="card" style="margin-top:14px"><h2>Screenshots</h2><div class="drop" id="drop" tabindex="0">Paste a screenshot here (⌘V), drop an image, or <label style="color:var(--teal2);cursor:pointer">pick a file<input type="file" accept="image/*" id="file" hidden multiple></label></div>
        <div class="shots">${(j.screenshots || []).map(s => `<figure><img src="/shots/${esc(s.filename)}" data-big="/shots/${esc(s.filename)}" alt=""><figcaption>${esc(s.caption || s.ts.slice(0, 16).replace("T", " "))} · <a href="#" data-rmshot="${s.id}">archive</a></figcaption></figure>`).join("")}</div></div>
      <div class="card" style="margin-top:14px"><h2>Source and referral</h2><div class="kv"><label>Source</label><div>${esc(j.source || "")}</div><label>Referred by</label><div>${esc(j.referrer || "")}</div><label>Referral date</label><div>${esc(j.referral_date || "")}</div>${j.source_url ? `<label>Source link</label><div><a target="_blank" rel="noopener" href="${esc(j.source_url)}">open ↗</a></div>` : ""}</div></div>
      <div class="card" style="margin-top:14px"><h2>Timeline</h2><div class="timeline">${(j.history || []).map(h => `<div><b>${esc(h.ts.slice(0, 16).replace("T", " "))}</b> ${esc(h.field)}: ${esc((h.old || "").slice(0, 40))} → ${esc((h.new || "").slice(0, 80))} <span class="tag">${esc(h.actor)}</span></div>`).join("")}</div></div>
    </div>
  </div>`;
  const patch = async (body, msg = "Saved") => { await api("/api/jobs/" + j.ref, { method: "PATCH", body }); toast(msg); };
  bindCustomAnswers(j, "jca");
  bindWhoContacts(j);
  $$("#stepper button").forEach(b => b.onclick = async () => {
    const body = { status: b.dataset.s };
    await patch(body, "Status: " + b.dataset.s);
    if (b.dataset.s === "Interview scheduled" && !j.interview_at) toast("Set the interview time below for the calendar link");
    jobView(ref);
  });
  $$("#kv [data-f]").forEach(el => el.addEventListener("change", async () => {
    let v = el.value; if (el.type === "number") v = v ? +v : null;
    await patch({ [el.dataset.f]: v }); if (["interview_at", "gmail_receipt_url"].includes(el.dataset.f)) jobView(ref);
  }));
  $$("[contenteditable][data-i]").forEach(el => el.addEventListener("blur", async () => {
    const A = j.answers.map(a => ({ ...a })); const i = +el.dataset.i;
    if (A[i].a === el.innerText) return; A[i].a = el.innerText; j.answers = A; await patch({ answers: A }, "Answer saved");
  }));
  $("#addUrl").onclick = async () => { const url = $("#nuUrl").value.trim(); if (!url) return; await patch({ saved_urls: [...(j.saved_urls || []), { url, label: $("#nuLabel").value.trim(), added: new Date().toISOString() }] }, "Link saved"); jobView(ref); };
  $$("[data-rmurl]").forEach(b => b.onclick = async () => { const U = [...(j.saved_urls || [])]; U.splice(+b.dataset.rmurl, 1); await patch({ saved_urls: U }); jobView(ref); });
  $$("[data-rmshot]").forEach(a => a.onclick = async e => { e.preventDefault(); if (!confirm("Archive this screenshot? The file moves to screenshots/archive.")) return; await api("/api/shots/" + a.dataset.rmshot, { method: "DELETE" }); jobView(ref); });
  $$(".shots img").forEach(im => im.onclick = () => { const d = document.createElement("div"); d.className = "lightbox"; d.innerHTML = `<img src="${im.dataset.big}">`; d.onclick = () => d.remove(); document.body.appendChild(d); });
  const upload = async file => { const url = await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(file); });
    await api(`/api/jobs/${j.ref}/screenshots`, { method: "POST", body: { data_url: url, caption: "" } }); toast("Screenshot saved"); };
  const drop = $("#drop");
  const onPaste = async e => { if (!location.hash.startsWith("#/job/")) return; const items = [...(e.clipboardData?.items || [])].filter(i => i.type.startsWith("image/")); if (!items.length) return; e.preventDefault(); for (const it of items) await upload(it.getAsFile()); jobView(ref); };
  document.onpaste = onPaste;
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("hot"); }; drop.ondragleave = () => drop.classList.remove("hot");
  drop.ondrop = async e => { e.preventDefault(); for (const f of e.dataTransfer.files) if (f.type.startsWith("image/")) await upload(f); jobView(ref); };
  $("#file").onchange = async e => { for (const f of e.target.files) await upload(f); jobView(ref); };
}


/* ---------- Who to contact (Crustdata, read-only research) ---------- */
const WHO_KIND = { hiring_manager: "Likely hiring manager", peer: "Peer", recruiter: "Technical recruiter", other: "Contact" };
function whoContactsCard(j) {
  const people = j.who_contacts || [];
  const live = !!j.contacts_live;
  const kindLabel = k => WHO_KIND[k] || (k ? k.replace(/_/g, " ") : "Contact");
  const head = `<div class="row"><h2 style="margin:0">Who to contact</h2><span class="tag">Source: Crustdata</span><span class="muted small">Research only. Nobody is messaged from here.</span><button class="btn s right" type="button" data-find-contacts="${esc(j.ref)}">Find contacts</button></div>`;
  if (!people.length) {
    return `<div class="card who-card" style="margin:14px 0">${head}<p class="muted small" style="margin:10px 0 0">No contacts stored yet. Find contacts looks them up if a Crustdata key is on this server; otherwise store the results here after a lookup.</p></div>`;
  }
  const rows = people.map((p, i) => {
    const loc = [p.location, p.tenure ? `Tenure: ${p.tenure}` : ""].filter(Boolean).join(" · ");
    const li = (p.linkedin_url || "").trim();
    const em = (p.work_email || "").trim();
    const acts = [
      li ? `<a class="btn s" target="_blank" rel="noopener" href="${esc(li)}">LinkedIn ↗</a><button class="btn s copy" type="button" data-copy="${esc(li)}">Copy LinkedIn</button>` : "",
      em ? `<button class="btn s copy" type="button" data-copy="${esc(em)}">Copy email</button>` : "",
    ].filter(Boolean).join("");
    return `<div class="who-person"><div class="who-top"><div><div class="who-name">${esc(p.name)}</div><div class="who-title">${esc(p.title || "")}</div></div><span class="pill">${esc(kindLabel(p.role_kind))}</span></div>${loc ? `<div class="who-meta">${esc(loc)}</div>` : ""}${em ? `<div class="who-meta">${esc(em)}</div>` : ""}${acts ? `<div class="who-acts">${acts}</div>` : ""}</div>`;
  }).join("");
  const when = people[0] && people[0].fetched_at ? `<p class="small muted" style="margin:8px 0 0">Updated ${esc(String(people[0].fetched_at).slice(0, 16).replace("T", " "))}</p>` : "";
  return `<div class="card who-card" style="margin:14px 0">${head}<div style="margin-top:12px">${rows}</div>${when}</div>`;
}
function bindWhoContacts(j) {
  $$("[data-find-contacts]").forEach(b => b.onclick = async () => {
    b.disabled = true;
    const old = b.textContent;
    b.textContent = "Looking…";
    try {
      const r = await api("/api/jobs/" + b.dataset.findContacts + "/contacts/refresh", { method: "POST", body: {} });
      if (r && r.ok && (r.contacts || []).length) {
        toast("Contacts updated");
        if (location.hash.startsWith("#/queue")) await route();
        else await jobView(j.ref);
        return;
      }
      if (r && r.agent_driven) {
        toast(r.message || "Lookups are not configured on this server. Ask your assistant to refresh this company.");
      } else {
        toast("No new contacts yet");
      }
    } catch (e) {
      toast("Could not refresh contacts");
    } finally {
      b.disabled = false;
      b.textContent = old;
    }
  });
}

/* ---------- Custom answers (per job, written for that JD and company) ---------- */
function caItems(j) { const ca = j.custom_answers; return !ca ? [] : Array.isArray(ca) ? ca : (ca.items || []); }
function customAnswersCard(j, pfx) {
  const items = caItems(j), ca = j.custom_answers || {};
  const wc = t => (String(t || "").trim().match(/\S+/g) || []).length;
  if (!items.length) return `<div class="card ca custom-answers" style="margin:14px 0"><div class="row"><h2 style="margin:0">Custom answers</h2><button class="btn s right" type="button" data-gen-custom="${esc(j.ref)}">Generate</button></div><p class="muted small">No custom answers for this job yet. Generate a set written for ${esc(j.company)} and this JD.</p></div>`;
  return `<div class="card ca custom-answers" style="margin:14px 0"><div class="row"><h2 style="margin:0">Custom answers for ${esc(j.company)}</h2><span class="tag">${items.length} answers</span>${ca.source === "auto" ? '<span class="pill st-To-apply">Auto-drafted from the JD, review before pasting</span>' : ""}<button class="btn s right" type="button" data-regen-custom="${esc(j.ref)}">Regenerate</button></div>
    <p class="small muted">Written for this JD and company. Click to edit, saves when you click away.</p>
    <div class="ca-list">${items.map((a, i) => `<div class="ans ${a.needs_review ? "fd" : ""}"><div class="q"><span>${esc(a.q)} ${a.screening ? '<span class="tag">form question</span>' : ""} <span class="tag" id="${pfx}w${i}">${wc(a.a)} words</span></span><button class="btn s copy" data-copy="#${pfx}${i}">COPY</button></div>${a.note ? `<div class="small" style="color:var(--amber);margin-bottom:6px">${esc(a.note)}</div>` : ""}<div class="a" id="${pfx}${i}" contenteditable="true" data-ci="${i}">${esc(a.a)}</div></div>`).join("")}</div>
    ${ca.generated_at ? `<p class="small muted" style="margin:6px 0 0">Generated ${esc(String(ca.generated_at).slice(0, 16).replace("T", " "))}${ca.edited_at ? ` · edited ${esc(String(ca.edited_at).slice(0, 16).replace("T", " "))}` : ""}</p>` : ""}</div>`;
}
function bindCustomAnswers(j, pfx) {
  const reload = async () => {
    if (location.hash.startsWith("#/queue")) await route();
    else await jobView(j.ref);
  };
  $$("[data-gen-custom]").forEach(b => b.onclick = async () => {
    await api("/api/jobs/" + b.dataset.genCustom + "/custom-answers", { method: "POST", body: { force: true } });
    toast("Custom answers generated");
    await reload();
  });
  $$("[data-regen-custom]").forEach(b => b.onclick = async () => {
    if (!confirm("Regenerate custom answers for " + j.company + "? Your edits to this set will be replaced.")) return;
    await api("/api/jobs/" + b.dataset.regenCustom + "/custom-answers", { method: "POST", body: { force: true } });
    toast("Custom answers regenerated");
    await reload();
  });
  $$(`[data-ci][id^="${pfx}"]`).forEach(el => el.addEventListener("blur", async () => {
    const items = caItems(j).map(a => ({ ...a })); const i = +el.dataset.ci;
    if (!items[i] || items[i].a === el.innerText) return;
    items[i].a = el.innerText; items[i].edited = true;
    const base = Array.isArray(j.custom_answers) ? {} : { ...(j.custom_answers || {}) };
    j.custom_answers = { ...base, items, edited_at: new Date().toISOString() };
    await api("/api/jobs/" + j.ref, { method: "PATCH", body: { custom_answers: j.custom_answers } });
    const w = $("#" + pfx + "w" + i); if (w) w.textContent = (el.innerText.trim().match(/\S+/g) || []).length + " words";
    toast("Custom answer saved");
  }));
}

/* ---------- Apply queue ---------- */
let queueQuery = "";
let stretchQuery = "";
async function stretchQueue(refArg) { return queue(refArg, true); }
async function queue(refArg, stretchMode = false) {
  const listFn = stretchMode ? stretchApplyQueue : toApplyQueue;
  const hashBase = stretchMode ? "#/stretch" : "#/queue";
  const getQ = () => stretchMode ? stretchQuery : queueQuery;
  const setQ = (v) => { if (stretchMode) stretchQuery = v; else queueQuery = v; };
  const all = listFn();
  const pausedN = (STATE.stats && STATE.stats.paused_to_apply) || (STATE.policy && STATE.policy.paused_to_apply) || 0;
  const stretchN = (STATE.stats && STATE.stats.stretch_to_apply) || 0;
  const Q = all.filter(j => jobMatches(j, getQ()));
  const searchBar = `<div class="search-row"><input id="q" class="search-box" type="search" placeholder="${stretchMode ? "Search stretch roles…" : "Search the apply queue…"}" value="${esc(getQ())}" autocomplete="off"><span class="match-count" id="matchCount">${getQ() ? `${Q.length} of ${all.length}` : `${all.length}`} ${stretchMode ? "stretch" : "in queue"}${!stretchMode && pausedN ? ` · ${pausedN} paused` : ""}${!stretchMode && stretchN ? ` · ${stretchN} stretch` : ""}</span></div>`;
  const bindQ = () => bindEscSearch($("#q"), () => {
    setQ($("#q").value);
    const next = listFn().filter(x => jobMatches(x, getQ()));
    const stay = next.some(x => x.ref === refArg);
    const dest = next.length && !stay ? hashBase + "/" + next[0].ref : (location.hash || hashBase);
    if (!next.length || dest === location.hash) queue(refArg, stretchMode);
    else location.hash = dest;
  });
  if (!all.length) {
    const emptyTitle = stretchMode ? "No stretch roles" : (pausedN ? "Queue paused" : "Queue cleared 🎉");
    const emptyBody = stretchMode
      ? "No To apply roles are in Stretch. Weak-fit roles land here so you can still apply, and you can move any role between Stretch and the main queue from its job page."
      : (pausedN ? `${pausedN} To apply role${pausedN === 1 ? " is" : "s are"} paused by a company skip or apply limit. They are not closed and will return to the queue when the skip or limit lifts.` : "Every tracked role is applied or closed. Add a new role when you are ready for fresh leads.");
    app.innerHTML = searchBar + outsideCard(getQ(), all, stretchMode) + policyBanners() + `<div class="card"><h1 id="pageTop" tabindex="-1">${emptyTitle}</h1><p>${emptyBody}</p></div>`;
    bindQ(); bindOutside(getQ());
    return;
  }
  if (!Q.length) { app.innerHTML = searchBar + outsideCard(getQ(), all, stretchMode) + policyBanners() + `<div class="card"><h1 id="pageTop" tabindex="-1">No matches in the ${stretchMode ? "stretch" : "apply"} queue</h1><p>Press Esc to clear the search.</p></div>`; bindQ(); bindOutside(getQ()); return; }
  let idx = Math.max(0, Q.findIndex(j => j.ref === refArg)); const j = await api("/api/jobs/" + Q[idx].ref);
  const ans = (j.answers || []);
  app.innerHTML = `<div class="queue">
   ${searchBar}
   ${outsideCard(getQ(), all, stretchMode)}
   ${policyBanners()}
   <div class="row small muted"><span>${stretchMode ? "Stretch queue" : "Apply queue"} · ${idx + 1} of ${Q.length}${getQ() ? ` matches (${all.length} total)` : ""}</span><span class="right">Keys: <span class="kbd">o</span> open · <span class="kbd">a</span> applied · <span class="kbd">s</span> skip · <span class="kbd">←</span><span class="kbd">→</span></span></div>
   <div class="bar" style="margin:8px 0 18px"><i style="width:${(STATE.stats.applied_today / Math.max(1, STATE.stats.goal)) * 100}%"></i></div>
   <div class="card"><div class="hero"><div><span class="ref">${j.ref}</span> ${hotBadge(j)}${stretchBadge(j)} ${isPaused(j.company) ? `<span class="pill">Paused</span>` : ""} ${tierTag(j.tier)}<div class="big" id="pageTop" tabindex="-1">${esc(j.company)}</div><div style="font-size:19px">${esc(j.role)}</div>
    <div class="row muted small" style="margin-top:6px"><span>${esc(j.comp || "Pay not posted")}</span><span>${esc(j.location || "")}</span><span>Fit ${j.fit_score ?? "?"}</span><span>${esc(j.source || "")}</span></div></div>
    <div class="row"><a id="openApply" class="btn p big" target="_blank" rel="noopener" href="${esc(j.apply_url || j.posting_url)}">Open application ↗</a>${skipCompanyBtn(j.company)}${stretchMoveBtn(j)}</div></div>
    ${dueCallout(j)}${stretchReasonBox(j)}
    <p>${esc(j.fit_notes || "")}</p>
    <ol class="steps">
      <li>Open the application. ${j.ats ? `It's on <b>${esc(j.ats)}</b>.` : ""}</li>
      <li>1Password: fill with Identity <b id="qop">${esc(j.onepassword_identity || legalName())}</b> <button class="btn s" data-copy="#qop">Copy</button></li>
      <li>Upload <b>${esc(j.resume_version || defaultResume())}</b>${j.cover_letter ? ` and <b>${esc(j.cover_letter)}</b>` : ""}.</li>
      <li>Copy each answer below into the form.</li>
      <li>Submit, paste the confirmation screenshot on the job page, then press <b>Mark applied</b>.</li>
    </ol></div>
   ${dupBanner(j)}
   ${whoContactsCard(j)}
   ${customAnswersCard(j, "qca")}
   <div class="card" style="margin-top:14px"><h2>Answers</h2>${ans.map((a, i) => `<div class="ans ${a.needs_review ? "fd" : ""}"><div class="q"><span>${esc(a.q)}</span><button class="btn s copy" data-copy="#qa${i}">COPY</button></div><div class="a" id="qa${i}">${esc(a.a)}</div></div>`).join("")}</div>
   <div class="row" style="margin-top:16px"><button class="btn" id="prev">← Previous</button><button class="btn warn" id="skip">Skip for now</button>${skipCompanyBtn(j.company, "btn warn")}${nlaButtons(j)}<a class="btn" href="#/job/${j.ref}">Full job page</a><button class="btn p big right" id="applied">Mark applied ✓</button></div></div>`;
  const go = d => { const n = Q[(idx + d + Q.length) % Q.length]; const dest = hashBase + "/" + n.ref; if (location.hash === dest) { route(); } else { location.hash = dest; } };
  $("#prev").onclick = () => go(-1); $("#skip").onclick = () => go(1);
  bindQ(); bindOutside(getQ());
  bindCustomAnswers(j, "qca");
  bindWhoContacts(j);
  $("#applied").onclick = async () => {
    if (!confirmDuplicates(j)) return;
    await api("/api/jobs/" + j.ref, { method: "PATCH", body: { status: "Applied" } });
    toast("Applied ✓ " + j.company);
    await load();
    const nextQ = listFn().filter(x => jobMatches(x, getQ()));
    const nx = nextQ.find(x => x.ref !== j.ref) || nextQ[0];
    const dest = !nx || nx.ref === j.ref ? "#/" : hashBase + "/" + nx.ref;
    if (location.hash === dest) await route();
    else location.hash = dest;
  };
  document.onkeydown = e => { if (!(location.hash.startsWith("#/queue") || location.hash.startsWith("#/stretch")) || /INPUT|TEXTAREA/.test(e.target.tagName) || e.target.isContentEditable) return;
    if (e.key === "o") $("#openApply").click(); if (e.key === "a") $("#applied").click(); if (e.key === "s" || e.key === "ArrowRight") go(1); if (e.key === "ArrowLeft") go(-1); };
}

/* ---------- Answer library ---------- */
function answersView() {
  const cats = [...new Set(STATE.answers.map(a => a.category))];
  app.innerHTML = `<h1>Answer library</h1><p class="sub">Grounded in your profile. Amber cards need your review. Edit any answer in place; it saves when you click away.</p>
  <div class="toolbar"><input id="aq" placeholder="Search questions and answers…" style="flex:1"><select id="ac"><option value="">All categories</option>${cats.map(c => `<option>${esc(c)}</option>`).join("")}</select><button class="btn" id="addA">+ New answer</button></div><div id="alist"></div>`;
  const draw = () => { const q = $("#aq").value.toLowerCase(), c = $("#ac").value;
    $("#alist").innerHTML = STATE.answers.filter(a => (!c || a.category === c) && (!q || (a.question + a.answer).toLowerCase().includes(q))).map(a => `
    <div class="ans ${a.needs_review ? "fd" : ""}"><div class="q"><span>${esc(a.question)} <span class="tag">${esc(a.category)}</span>${a.used ? ` <span class="tag">copied ${a.used}×</span>` : ""}</span><button class="btn s copy" data-copy="#an-${a.id}" data-answer-id="${a.id}" data-used="${a.used || 0}">COPY</button></div>
    <div class="a" id="an-${a.id}" contenteditable="true" data-id="${a.id}">${esc(a.answer)}</div></div>`).join("");
    $$("#alist [contenteditable]").forEach(el => el.addEventListener("blur", async () => { const a = STATE.answers.find(x => x.id === el.dataset.id); if (a.answer === el.innerText) return; a.answer = el.innerText; await api("/api/answers/" + a.id, { method: "PATCH", body: { answer: el.innerText } }); toast("Saved"); }));
  };
  $("#aq").oninput = draw; $("#ac").oninput = draw;
  $("#addA").onclick = async () => { const q = prompt("Question?"); if (!q) return; await api("/api/answers", { method: "POST", body: { question: q, answer: "", category: "Custom" } }); route(); };
  draw();
}

/* ---------- Referrals ---------- */
function referrals() {
  const groups = {};
  STATE.jobs.forEach(j => { const k = j.referrer || j.source_group || j.source || "Unknown"; (groups[k] = groups[k] || []).push(j); });
  const rows = Object.entries(groups).sort((a, b) => b[1].length - a[1].length);
  app.innerHTML = `<h1>Referrals and sources</h1><p class="sub">Who sent what, and how it's going. Thank the people whose leads turn into interviews.</p>
  ${rows.map(([k, L]) => { const ap = L.filter(j => j.date_applied).length, iv = L.filter(j => ["Next steps", "Interview scheduled", "Offer"].includes(j.status)).length;
    return `<div class="card" style="margin-bottom:14px"><div class="row"><h2 style="margin:0">${esc(k)}</h2><span class="tag">${L.length} roles</span><span class="tag">${ap} applied</span><span class="tag">${iv} moving</span></div>
    <div class="bar" style="margin:10px 0"><i style="width:${(ap / L.length) * 100}%"></i></div>
    ${L.map(j => `<div class="row small" style="margin:4px 0"><span class="ref">${j.ref}</span>${statusPill(j.status)}<a href="#/job/${j.ref}">${esc(j.company)} · ${esc(j.role)}</a><span class="muted right">${esc(j.referral_date || "")}</span></div>`).join("")}</div>`; }).join("")}`;
}

/* ---------- 1Password ---------- */
function identity() {
  const I = STATE.settings.identity || { fields: [], identities: [], steps: [], status: "" };
  app.innerHTML = `<h1>1Password identities</h1><p class="sub">Vault <b>${esc(vaultName())}</b>. One main Identity, plus variants where application forms differ. Nothing here is a password.</p>
  <div class="callout amber" style="margin-bottom:14px"><b>Status:</b> ${esc(I.status || "")}</div>
  <div class="grid g2" style="align-items:start"><div class="card"><h2>Identity: ${esc((I.identities && I.identities[0] && I.identities[0].name) || legalName())}</h2><table>${I.fields.map((f, i) => `<tr><td class="muted small" style="width:42%">${esc(f.label)}${f.section ? `<div class="small">${esc(f.section)}</div>` : ""}</td><td id="idf${i}">${esc(f.value)}</td><td style="width:70px">${f.value ? `<button class="btn s" data-copy="#idf${i}">COPY</button>` : ""}</td></tr>`).join("")}</table></div>
  <div><div class="card"><h2>Identities to create</h2>${I.identities.map(x => `<div style="margin:8px 0"><b>${esc(x.name)}</b><div class="small muted">${esc(x.use)}</div></div>`).join("")}</div>
  <div class="card" style="margin-top:14px"><h2>Steps</h2><ol>${I.steps.map(s => `<li style="margin:6px 0">${esc(s)}</li>`).join("")}</ol></div></div></div>`;
}

/* ---------- Feedback ---------- */
async function feedback() {
  const F = await api("/api/feedback");
  app.innerHTML = `<h1>Feedback</h1><p class="sub">Ideas for the app, notes on how an application or interview went, or anything you want to remember. The notes stay in your local database and are available through the API.</p>
  <div class="card"><div class="row"><select id="fk"><option value="idea">App idea</option><option value="bug">Bug</option><option value="interview">Interview debrief</option><option value="answer">Answer wording</option></select><input id="fref" placeholder="Job ref (optional), e.g. J-003" style="width:220px"></div>
  <textarea id="ft" style="margin-top:10px" placeholder="What should change?"></textarea><button class="btn p" id="fsend" style="margin-top:10px">Save feedback</button></div>
  <div style="margin-top:14px">${F.map(f => `<div class="card" style="margin-bottom:10px"><div class="row small muted"><span class="tag">${esc(f.kind)}</span>${f.ref ? `<a href="#/job/${esc(f.ref)}">${esc(f.ref)}</a>` : ""}<span class="right">${esc(f.ts.slice(0, 16).replace("T", " "))}</span></div><div style="white-space:pre-wrap;margin-top:6px">${esc(f.text)}</div></div>`).join("")}</div>`;
  $("#fsend").onclick = async () => { if (!$("#ft").value.trim()) return; await api("/api/feedback", { method: "POST", body: { kind: $("#fk").value, text: $("#ft").value, ref: $("#fref").value.trim() || null } }); toast("Thanks, saved"); feedback(); };
}

/* ---------- Data & API ---------- */
function limitEditor() {
  const pol = STATE.policy || {};
  const limits = pol.limits || [];
  const skips = pol.skips || [];
  const extras = pol.extras || [];
  const rows = limits.map(l => `<tr data-lim="${esc(l.company)}">
    <td><input data-f="company" value="${esc(l.company)}"></td>
    <td><input data-f="max_apps" type="number" min="1" value="${esc(l.max_apps)}"></td>
    <td><input data-f="window_days" type="number" min="1" value="${esc(l.window_days)}"></td>
    <td><select data-f="window_kind"><option value="rolling"${l.window_kind !== "calendar_month" ? " selected" : ""}>rolling days</option><option value="calendar_month"${l.window_kind === "calendar_month" ? " selected" : ""}>calendar month</option></select></td>
    <td><input data-f="policy_url" value="${esc(l.policy_url || "")}" placeholder="https://"></td>
    <td><input data-f="note" value="${esc(l.note || "")}"></td>
    <td><button class="btn s" data-save-lim="${esc(l.company)}">Save</button> <button class="btn s danger" data-rm-lim="${esc(l.company)}">Remove</button></td>
  </tr>`).join("");
  const skipRows = skips.map(s => `<div class="row" style="margin:6px 0"><b>${esc(s.company)}</b> <span class="muted small">${s.until ? "until " + esc(s.until) : "until you resume"}</span><button class="btn s right" data-resume-co="${esc(s.company)}">Resume</button></div>`).join("") || '<p class="muted small">No manual company skips.</p>';
  const extraRows = extras.map(e => `<div class="row" style="margin:6px 0"><span>${esc(e.company)} · ${esc(e.date_applied)}</span> ${(e.gmail_receipt_url || e.receipt_url) ? `<a class="small" target="_blank" rel="noopener" href="${esc(e.gmail_receipt_url || e.receipt_url)}">receipt ↗</a>` : ""} <span class="muted small">${esc(e.note || "")}</span></div>`).join("");
  return `<div class="card" style="margin:14px 0"><h2>Company apply limits</h2>
    <p class="small muted">Caps pause every To apply role at that company until a slot opens. They stay To apply, just out of the queue. Google is 3 applications per rolling 30 days.</p>
    <table class="limits-table"><tr><th>Company</th><th>Max</th><th>Window</th><th>Kind</th><th>Policy URL</th><th>Note</th><th></th></tr>
    ${rows}
    <tr id="limNew"><td><input data-f="company" placeholder="Company"></td><td><input data-f="max_apps" type="number" min="1" value="3"></td><td><input data-f="window_days" type="number" min="1" value="30"></td><td><select data-f="window_kind"><option value="rolling" selected>rolling days</option><option value="calendar_month">calendar month</option></select></td><td><input data-f="policy_url" placeholder="https://"></td><td><input data-f="note"></td><td><button class="btn s p" id="limAdd">Add company</button></td></tr>
    </table>
    <h3 style="margin-top:18px">Manual skips</h3>${skipRows}
    ${extras.length ? `<h3 style="margin-top:18px">Unmapped receipts counted toward a limit</h3>${extraRows}` : ""}
  </div>`;
}
function readLimitRow(tr) {
  const get = f => {
    const el = tr.querySelector(`[data-f="${f}"]`);
    return el ? el.value.trim() : "";
  };
  return { company: get("company"), max_apps: +get("max_apps"), window_days: +get("window_days"), window_kind: get("window_kind") || "rolling", policy_url: get("policy_url"), note: get("note") };
}
function bindLimitEditor() {
  $$("[data-save-lim]").forEach(b => b.onclick = async () => {
    const body = readLimitRow(b.closest("tr"));
    if (!body.company) return toast("Company is required");
    if (coKey(body.company) !== coKey(b.dataset.saveLim) && b.dataset.saveLim) {
      await api("/api/company-apply-limits/remove", { method: "POST", body: { company: b.dataset.saveLim } });
    }
    STATE.policy = await api("/api/company-apply-limits", { method: "POST", body });
    toast("Saved " + body.company);
    await load(); route();
  });
  $$("[data-rm-lim]").forEach(b => b.onclick = async () => {
    if (!confirm("Remove the apply limit for " + b.dataset.rmLim + "?")) return;
    STATE.policy = await api("/api/company-apply-limits/remove", { method: "POST", body: { company: b.dataset.rmLim } });
    toast("Removed " + b.dataset.rmLim);
    await load(); route();
  });
  const add = $("#limAdd");
  if (add) add.onclick = async () => {
    const body = readLimitRow($("#limNew"));
    if (!body.company) return toast("Company is required");
    STATE.policy = await api("/api/company-apply-limits", { method: "POST", body });
    toast("Added " + body.company);
    await load(); route();
  };
}
function dataView() {
  app.innerHTML = `<h1>Data and API</h1><p class="sub">Your data lives in a local SQLite database (njc.db in the data directory). Screenshots are files beside it. Nothing is ever hard-deleted.</p>
  ${limitEditor()}
  <div class="grid g2" style="align-items:start"><div class="card"><h2>Export</h2><div class="row"><a class="btn p" href="/api/export.csv">Download CSV</a><a class="btn" href="/api/export.json">Download JSON (everything)</a></div>
  <h2 style="margin-top:18px">Add a job by hand</h2><div class="kv"><label>Company</label><input id="nc"><label>Role</label><input id="nr"><label>Apply URL</label><input id="nu"><label>Source</label><input id="ns" placeholder="e.g. a referral or a job board"><label>Comp</label><input id="ncomp"><label>Location</label><input id="nl"></div><button class="btn p" id="nadd" style="margin-top:10px">Add job</button>
  <h2 style="margin-top:18px">Import JSON</h2><p class="small muted">Paste {"jobs":[…]} (an export or a crawl refresh). Existing jobs keep your edits; only empty and crawled fields refresh.</p><textarea id="imp"></textarea><button class="btn" id="impgo" style="margin-top:8px">Import</button></div>
  <div class="card"><h2>JSON API</h2><table>
  <tr><td><code>GET /api/jobs</code></td><td>All jobs (add <code>?status=Applied</code>, <code>?full=1</code>, <code>?stretch=1</code>)</td></tr>
  <tr><td><code>GET /api/jobs/J-001</code></td><td>One job with answers, JD, screenshots, history, and Who to contact</td></tr>
  <tr><td><code>PATCH /api/jobs/J-001</code></td><td>Any field, e.g. <code>{"status":"Applied","gmail_receipt_url":"…"}</code> or <code>{"status":"Closed","closed_reason":"Not accepting applications"}</code>. Undo a close with <code>{"undo_close":true}</code>. Move a role with <code>POST /api/jobs/J-001/stretch</code> and <code>{"stretch":true,"stretch_reason":"…"}</code>. Send header <code>X-Actor: assistant</code> so the timeline shows who changed it.</td></tr>
  <tr><td><code>POST /api/jobs</code></td><td>Create a job (ref auto-assigned)</td></tr>
  <tr><td><code>GET /api/jobs/J-001/ics</code></td><td>Interview calendar file</td></tr>
  <tr><td><code>GET /api/stats</code></td><td>Counts, per-day, funnel, streak</td></tr>
  <tr><td><code>GET /api/progress</code></td><td>Daily application counts since your progress start date, with running totals and streak</td></tr>
  <tr><td><code>GET|POST /api/answers</code>, <code>PATCH /api/answers/:id</code></td><td>Answer library</td></tr>
  <tr><td><code>POST /api/jobs/J-001/custom-answers</code></td><td>Generate or regenerate custom answers for one job (<code>{"force":true}</code>)</td></tr>
  <tr><td><code>POST /api/custom-answers/generate</code></td><td>Generate custom answers for every non-closed job that is missing them</td></tr>
  <tr><td><code>GET /api/duplicates</code></td><td>Audit near-duplicate jobs (add <code>?later=1</code> for Applied-or-later only)</td></tr>
  <tr><td><code>GET|POST /api/feedback</code></td><td>Feedback</td></tr>
  <tr><td><code>POST /api/alerts/ingest</code></td><td>Job-alert emails in, roles out: parse, dedupe against every tracked role, triage to the queue or Stretch (<code>dry_run</code> to preview). <code>GET /api/alerts/seen</code> lists emails already read</td></tr>
  <tr><td><code>POST /api/import</code></td><td>Merge jobs and answers, never deletes</td></tr>
  <tr><td><code>GET /api/export.csv|json</code></td><td>Exports</td></tr>
  <tr><td><code>GET /api/history</code></td><td>Every change, newest first</td></tr>
  <tr><td><code>GET /api/company-policy</code></td><td>Skips, apply limits, and computed caps</td></tr>
  <tr><td><code>POST /api/company-skips</code></td><td>Pause a company (<code>{"company":"Acme","until":"2026-12-18"}</code>). <code>until</code> is optional.</td></tr>
  <tr><td><code>POST /api/company-skips/resume</code></td><td>Clear a manual company skip</td></tr>
  <tr><td><code>POST /api/company-apply-limits</code></td><td>Add or update one company cap, or replace the table with <code>{"limits":[…]}</code></td></tr></table></div></div>`;
  bindLimitEditor();
  $("#nadd").onclick = async () => { const b = { company: $("#nc").value, role: $("#nr").value, apply_url: $("#nu").value, posting_url: $("#nu").value, source: $("#ns").value || "Added by hand", source_group: $("#ns").value || "Added by hand", comp: $("#ncomp").value, location: $("#nl").value, referral_date: new Date().toLocaleDateString("en-CA"), queue_rank: 900 };
    if (!b.company || !b.role) return toast("Company and role please"); const j = await api("/api/jobs", { method: "POST", body: b }); location.hash = "#/job/" + j.ref; };
  $("#impgo").onclick = async () => { try { const r = await api("/api/import", { method: "POST", body: $("#imp").value }); toast(`Added ${r.added}, updated ${r.updated}`); } catch (e) {} };
}

document.addEventListener("click", async (e) => {
  const skip = e.target.closest("[data-skip-co]");
  if (skip) {
    e.preventDefault();
    const ok = await openSkipCompany(skip.dataset.skipCo);
    if (ok) { await load(); await route(); }
    return;
  }
  const resume = e.target.closest("[data-resume-co]");
  if (resume) {
    e.preventDefault();
    await resumeCompany(resume.dataset.resumeCo);
    return;
  }
  const stretchOn = e.target.closest("[data-stretch-on]");
  const stretchOff = e.target.closest("[data-stretch-off]");
  if (stretchOn || stretchOff) {
    e.preventDefault();
    const ref = (stretchOn || stretchOff).dataset.stretchOn || (stretchOn || stretchOff).dataset.stretchOff;
    const j = jobBy(ref);
    if (!j) return;
    await onStretchMove(j, !!stretchOn);
  }
});

route();
