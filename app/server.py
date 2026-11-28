#!/usr/bin/env python3
"""NixNet Job Command: a local application tracker.

Stdlib only (Python 3.9+): http.server + sqlite3. No pip installs.
Binds 127.0.0.1 by default, never 0.0.0.0 unless you opt in.
Profile, proofs, fit rules and daily goal come from config and profile files.
"""
import base64, csv, io, json, os, re, sqlite3, sys, threading, time, uuid, datetime
import custom_answers as CA
import duplicates as DUP
import alerts as AL
import queue_order as QO
import company_policy as CP
from config import CFG, VERSION
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote

ROOT = CFG.app_dir
DATA = CFG.data_dir
STATIC = os.path.join(ROOT, "static")
SHOTS = os.path.join(DATA, "screenshots")
DB = os.path.join(DATA, CFG.db_file)
SEED = CFG.seed_path
PORT = CFG.port
BIND = list(CFG.bind)
PROGRESS_START = CFG.progress_start  # may be None until first boot

def apply_runtime():
    """Reload config (used by tests that set env after import)."""
    global ROOT, DATA, STATIC, SHOTS, DB, SEED, PORT, BIND, PROGRESS_START
    CFG.reload()
    ROOT = CFG.app_dir
    DATA = CFG.data_dir
    STATIC = os.path.join(ROOT, "static")
    SHOTS = os.path.join(DATA, "screenshots")
    DB = os.path.join(DATA, CFG.db_file)
    SEED = CFG.seed_path
    PORT = CFG.port
    BIND = list(CFG.bind)
    PROGRESS_START = CFG.progress_start

STATUSES = ["To apply", "Applied", "Next steps", "Interview scheduled", "Offer", "Rejected", "Closed", "Withdrawn"]
# Refs and answer ids end up in URLs, HTML attributes and CSS selectors, so keep them to a safe alphabet.
REF_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
ANSWER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

def valid_ref(ref):
    return isinstance(ref, str) and bool(REF_RE.match(ref))

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
ANY_ADDR = {"0.0.0.0", "::"}

def _host_only(h):
    """'Example.com:8742' -> 'example.com', '[::1]:8742' -> '::1'."""
    h = (h or "").strip().lower()
    if h.startswith("["):
        return h[1:h.find("]")] if "]" in h else h[1:]
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h

def allowed_hosts():
    """Host names this server answers to. None means any (all-interfaces bind with no allowed_hosts set)."""
    extra = {h.lower() for h in CFG.allowed_hosts}
    binds = {b.lower() for b in BIND}
    if binds & ANY_ADDR and not extra:
        return None
    return LOOPBACK_HOSTS | (binds - ANY_ADDR) | extra

def request_problem(headers, mutating):
    """Why a request must be refused, or None.

    Host check: blocks DNS rebinding, where an attacker's domain resolves to 127.0.0.1 and reads the API.
    Origin check (writes only): blocks cross-site requests. A browser can POST text/plain to localhost
    from any page without a preflight; it always sends Origin on those, while curl and agents send none.
    """
    hosts = allowed_hosts()
    host = headers.get("Host")
    if host and hosts is not None and _host_only(host) not in hosts:
        return "unknown Host header %r; add it to allowed_hosts in config to use this name" % host
    if not mutating:
        return None
    origin = headers.get("Origin")
    if origin is not None:
        # Same origin only: the page's scheme://host:port must be this server as the browser addressed it.
        u = urlparse(origin)
        if u.scheme not in ("http", "https") or not host or u.netloc.lower() != host.strip().lower():
            return "cross-origin write refused (Origin %s)" % origin
    elif (headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
        return "cross-site write refused"
    return None
CORE = ["ref", "company", "role", "status", "fit_score", "priority", "source", "updated_at"]

_lock = threading.Lock()

def now():
    return CFG.now().isoformat(timespec="seconds")

def now_et():
    """Wall-clock time in the configured timezone (or local)."""
    return now()

def today_et():
    """Calendar date in the configured timezone (or local)."""
    return CFG.today()

def parse_iso_date(s):
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


# Optional Crustdata key for live "Find contacts". Keep it in this chmod-600
# file if one is added later; never log the value. Lookups stay agent-driven
# when the file is absent (the usual case: the key lives in the MCP connector).
CRUSTDATA_ENV = os.path.join(DATA, ".crustdata.env")

def _env_key(path, name):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == name:
                    v = v.strip().strip("'").strip('"')
                    return v or None
    except OSError:
        return None
    return None

def crustdata_live_available():
    return bool(os.environ.get("CRUSTDATA_API_KEY") or _env_key(CRUSTDATA_ENV, "CRUSTDATA_API_KEY"))

ROLE_KIND_ORDER = {"hiring_manager": 1, "peer": 2, "recruiter": 3}

def ensure_contacts_table(c):
    c.execute("""
    CREATE TABLE IF NOT EXISTS contacts(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ref TEXT NOT NULL,
      role_kind TEXT,
      name TEXT NOT NULL,
      title TEXT,
      linkedin_url TEXT,
      location TEXT,
      tenure TEXT,
      work_email TEXT,
      source TEXT DEFAULT 'Crustdata',
      crustdata_person_id TEXT,
      company TEXT,
      fetched_at TEXT,
      notes TEXT
    )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS contacts_ref ON contacts(ref)")

def contacts_for(c, ref):
    rows = c.execute(
        "SELECT id,ref,role_kind,name,title,linkedin_url,location,tenure,work_email,source,"
        "crustdata_person_id,company,fetched_at,notes FROM contacts WHERE ref=? ORDER BY id",
        (ref,),
    ).fetchall()
    out = [dict(r) for r in rows]
    out.sort(key=lambda r: (ROLE_KIND_ORDER.get(r.get("role_kind") or "", 9), r.get("id") or 0))
    return out

def replace_contacts(c, ref, people, actor="app"):
    """Replace stored contacts for one job. Only rows with a real name are kept."""
    if not c.execute("SELECT 1 FROM jobs WHERE ref=?", (ref,)).fetchone():
        return None
    row = c.execute("SELECT data FROM jobs WHERE ref=?", (ref,)).fetchone()
    company = ""
    if row:
        try:
            company = json.loads(row["data"]).get("company") or ""
        except Exception:
            company = ""
    old_n = c.execute("SELECT count(*) FROM contacts WHERE ref=?", (ref,)).fetchone()[0]
    c.execute("DELETE FROM contacts WHERE ref=?", (ref,))
    kept = []
    ts = now()
    for p in people or []:
        if not isinstance(p, dict):
            continue
        name = (p.get("name") or "").strip()
        if not name:
            continue
        rec = {
            "ref": ref,
            "role_kind": (p.get("role_kind") or p.get("kind") or "").strip() or "other",
            "name": name,
            "title": (p.get("title") or "").strip() or None,
            "linkedin_url": (p.get("linkedin_url") or p.get("profile_url") or "").strip() or None,
            "location": (p.get("location") or "").strip() or None,
            "tenure": (p.get("tenure") or "").strip() or None,
            "work_email": (p.get("work_email") or p.get("email") or "").strip() or None,
            "source": (p.get("source") or "Crustdata").strip() or "Crustdata",
            "crustdata_person_id": str(p["crustdata_person_id"]) if p.get("crustdata_person_id") not in (None, "") else None,
            "company": (p.get("company") or company or "").strip() or None,
            "fetched_at": (p.get("fetched_at") or ts),
            "notes": (p.get("notes") or "").strip() or None,
        }
        c.execute(
            "INSERT INTO contacts(ref,role_kind,name,title,linkedin_url,location,tenure,work_email,"
            "source,crustdata_person_id,company,fetched_at,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec["ref"], rec["role_kind"], rec["name"], rec["title"], rec["linkedin_url"],
             rec["location"], rec["tenure"], rec["work_email"], rec["source"],
             rec["crustdata_person_id"], rec["company"], rec["fetched_at"], rec["notes"]),
        )
        rec["id"] = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        kept.append(rec)
    c.execute(
        "INSERT INTO history(ref,ts,field,old,new,actor) VALUES(?,?,?,?,?,?)",
        (ref, ts, "contacts", str(old_n), "%d people" % len(kept), actor),
    )
    return contacts_for(c, ref)

def db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

# The answers table flags "needs a human decision". Databases migrated from the
# private Job Command Center call that column fred_decides; new ones use
# needs_review. Detect which one this database has so both work, and so the old
# app can still open the same file for a rollback.
ANS_FLAG = "needs_review"

def detect_answers_flag(c):
    global ANS_FLAG
    cols = {r[1] for r in c.execute("PRAGMA table_info(answers)")}
    ANS_FLAG = "needs_review" if "needs_review" in cols or "fred_decides" not in cols else "fred_decides"
    return ANS_FLAG

def answer_row(r):
    """API shape for an answers row: always both needs_review and fred_decides."""
    d = dict(r)
    flag = d.get("needs_review", d.get("fred_decides", 0))
    d["needs_review"] = flag
    d["fred_decides"] = flag
    return d


def init():
    os.makedirs(SHOTS, exist_ok=True)
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS jobs(ref TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT, ts TEXT, field TEXT, old TEXT, new TEXT, actor TEXT);
    CREATE TABLE IF NOT EXISTS shots(id TEXT PRIMARY KEY, ref TEXT, filename TEXT, caption TEXT, ts TEXT);
    CREATE TABLE IF NOT EXISTS answers(id TEXT PRIMARY KEY, category TEXT, question TEXT, answer TEXT, needs_review INTEGER DEFAULT 0, sort INTEGER DEFAULT 0, updated_at TEXT, used INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, text TEXT, ref TEXT);
    CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS alert_seen(email_id TEXT PRIMARY KEY, ts TEXT, found INTEGER DEFAULT 0, added INTEGER DEFAULT 0);
    """)
    ensure_contacts_table(c)
    CP.ensure_defaults(c)
    c.commit()
    detect_answers_flag(c)
    # Default goal and progress start if the user has not set them.
    if c.execute("SELECT 1 FROM settings WHERE k='daily_goal'").fetchone() is None:
        c.execute("INSERT INTO settings(k,v) VALUES(?,?)", ("daily_goal", json.dumps(CFG.daily_goal)))
    ident = (CFG.profile or {}).get("identity")
    if ident and c.execute("SELECT 1 FROM settings WHERE k='identity'").fetchone() is None:
        c.execute("INSERT INTO settings(k,v) VALUES(?,?)", ("identity", json.dumps(ident)))
    if SEED and os.path.exists(SEED):
        with open(SEED, encoding='utf-8') as _sf:
            seed = json.load(_sf)
        if seed.get("shift_dates_to_today"):
            seed = shift_seed_dates(seed)
        row = c.execute("SELECT v FROM meta WHERE k='seed_version'").fetchone()
        if not row or row["v"] != seed.get("generated_at"):
            res = seed_import(c, seed, actor="seed")
            c.execute("INSERT INTO meta(k,v) VALUES('seed_version',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (seed.get("generated_at"),))
            c.commit()
            sys.stderr.write("%s seed %s imported: %s\n" % (now(), seed.get("generated_at"), res))
    c.close()


def shift_seed_dates(seed):
    """Rebase example dates so the progress graph starts today."""
    today = today_et()
    anchor = parse_iso_date(seed.get("date_anchor"))
    if not anchor:
        return seed
    delta = today - anchor
    def bump(s):
        d = parse_iso_date(s)
        if not d:
            return s
        return (d + delta).isoformat()
    for j in seed.get("jobs") or []:
        for k in ("date_applied", "created_at", "updated_at", "posted", "crawled_at", "interview_at", "follow_up", "closed_at", "referral_date"):
            if j.get(k):
                raw = str(j[k])
                day = parse_iso_date(raw)
                if not day:
                    continue
                new_day = day + delta
                if "T" in raw:
                    j[k] = new_day.isoformat() + raw[10:]
                else:
                    j[k] = new_day.isoformat()
        if j.get("custom_answers") and isinstance(j["custom_answers"], dict) and j["custom_answers"].get("generated_at"):
            raw = j["custom_answers"]["generated_at"]
            day = parse_iso_date(raw)
            if day:
                j["custom_answers"]["generated_at"] = (day + delta).isoformat() + (raw[10:] if "T" in str(raw) else "")
    settings = seed.get("settings") or {}
    if settings.get("progress_start"):
        settings["progress_start"] = bump(settings["progress_start"])
    extras = settings.get("company_extra_applications") or []
    for e in extras:
        if e.get("date_applied"):
            e["date_applied"] = bump(e["date_applied"])
    seed["generated_at"] = "shifted-" + today.isoformat()
    return seed

def seed_import(c, seed, actor="import", overwrite=False):
    """Merge jobs/answers. Existing jobs keep the user's edits (status, notes, receipts...);
    only empty fields and crawl fields (jd, questions, crawl_*) are refreshed."""
    crawl_fields = {"jd", "questions", "crawl_status", "crawled_at", "comp", "posted", "location", "remote", "skills", "ats", "apply_url", "posting_url"}
    added = updated = 0
    rejected = []
    for j in seed.get("jobs", []):
        if not isinstance(j, dict):
            continue
        ref = j.get("ref")
        if ref and not valid_ref(ref):
            rejected.append(str(ref)[:80])
            continue
        if not ref:
            if not (j.get("company") and j.get("role")):
                continue
            refs = [r[0] for r in c.execute("SELECT ref FROM jobs").fetchall()]
            ref = "J-%03d" % (max([int(r[2:]) for r in refs if r[2:].isdigit()] + [200]) + 1)
            j["ref"] = ref
        row = c.execute("SELECT data FROM jobs WHERE ref=?", (ref,)).fetchone()
        if not row:
            j.setdefault("status", "To apply")
            j.setdefault("created_at", now())
            j["updated_at"] = now()
            c.execute("INSERT INTO jobs(ref,data,status,updated_at) VALUES(?,?,?,?)", (ref, json.dumps(j), j["status"], j["updated_at"]))
            c.execute("INSERT INTO history(ref,ts,field,old,new,actor) VALUES(?,?,?,?,?,?)", (ref, now(), "created", "", j["status"], actor))
            added += 1
        else:
            cur = json.loads(row["data"])
            changed = False
            # Never reopen or wipe a close: a later seed refresh must not put a
            # "Not accepting applications" job back in the apply queue.
            preserve_close = cur.get("status") in ("Closed", "Withdrawn", "Rejected")
            close_fields = {"closed_reason", "closed_at", "status_before_close"}
            for k, v in j.items():
                if k == "answers":
                    have = {a.get("q") for a in cur.get("answers", [])}
                    for a in v:
                        if a.get("q") not in have:
                            cur.setdefault("answers", []).append(a); changed = True
                    continue
                if k == "status" and cur.get("status") and not overwrite:
                    continue
                if k in close_fields and cur.get(k) and not overwrite:
                    continue
                if preserve_close and k == "status":
                    continue
                if overwrite or k in crawl_fields or cur.get(k) in (None, "", [], {}):
                    if cur.get(k) != v and not (k == "status" and cur.get("status")):
                        cur[k] = v; changed = True
            if changed:
                cur["updated_at"] = now()
                c.execute("UPDATE jobs SET data=?, status=?, updated_at=? WHERE ref=?", (json.dumps(cur), cur.get("status"), cur["updated_at"], ref))
                updated += 1
    for j in seed.get("jobs", []):
        ref = j.get("ref") if isinstance(j, dict) else None
        if valid_ref(ref):
            ensure_custom_answers(c, ref, force=False, actor=actor)
    for a in seed.get("answers", []):
        if not isinstance(a, dict) or not ANSWER_ID_RE.match(str(a.get("id") or "")):
            rejected.append("answer %s" % str(a.get("id") if isinstance(a, dict) else a)[:80])
            continue
        if not c.execute("SELECT 1 FROM answers WHERE id=?", (a["id"],)).fetchone():
            c.execute("INSERT INTO answers(id,category,question,answer,%s,sort,updated_at) VALUES(?,?,?,?,?,?,?)" % ANS_FLAG,
                      (a["id"], a.get("category", ""), a["question"], a.get("answer", ""), 1 if a.get("needs_review") or a.get("fred_decides") else 0, a.get("sort", 0), now()))
    for k, v in seed.get("settings", {}).items():
        c.execute("INSERT OR IGNORE INTO settings(k,v) VALUES(?,?)", (k, json.dumps(v)))
    contacts_n = 0
    for rec in seed.get("contacts") or []:
        if not isinstance(rec, dict):
            continue
        ref, name = rec.get("ref"), (rec.get("name") or "").strip()
        if not ref or not name:
            continue
        if not c.execute("SELECT 1 FROM jobs WHERE ref=?", (ref,)).fetchone():
            continue
        if c.execute("SELECT 1 FROM contacts WHERE ref=? AND name=?", (ref, name)).fetchone():
            continue
        c.execute(
            "INSERT INTO contacts(ref,role_kind,name,title,linkedin_url,location,tenure,work_email,"
            "source,crustdata_person_id,company,fetched_at,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ref, rec.get("role_kind") or "other", name, rec.get("title"), rec.get("linkedin_url"),
             rec.get("location"), rec.get("tenure"), rec.get("work_email"), rec.get("source") or "example",
             None, rec.get("company"), rec.get("fetched_at") or now(), rec.get("notes") or "Example contact"),
        )
        contacts_n += 1
    c.commit()
    out = {"added": added, "updated": updated, "contacts": contacts_n}
    if rejected:
        out["rejected"] = rejected
        out["rejected_reason"] = "refs may only use letters, digits and '-'; answer ids also allow '_' (max 64)"
    return out

def job_out(c, ref, full=True):
    row = c.execute("SELECT data FROM jobs WHERE ref=?", (ref,)).fetchone()
    if not row:
        return None
    d = json.loads(row["data"])
    if full:
        d["screenshots"] = [dict(r) for r in c.execute("SELECT id,filename,caption,ts FROM shots WHERE ref=? ORDER BY ts", (ref,))]
        d["history"] = [dict(r) for r in c.execute("SELECT ts,field,old,new,actor FROM history WHERE ref=? ORDER BY id DESC LIMIT 200", (ref,))]
        d["who_contacts"] = contacts_for(c, ref)
        d["contacts_live"] = crustdata_live_available()
    return d

def is_stretch(d):
    v = (d or {}).get("stretch")
    return v is True or v == 1 or v == "true" or v == "1"

def is_hot(d):
    v = (d or {}).get("hot")
    return v is True or v == 1 or v == "true" or v == "1"

def search_blob(d):
    parts = [d.get("ref"), d.get("company"), d.get("role"), d.get("location"),
             d.get("status"), d.get("remote"), d.get("jd") or "",
             d.get("stretch_reason") or "", "stretch" if is_stretch(d) else "",
             "hot" if is_hot(d) else "", d.get("due_at") or "", d.get("due_label") or ""]
    return " ".join(p for p in parts if p)

def _custom_count(ca):
    if not ca:
        return 0
    if isinstance(ca, dict):
        return len(ca.get("items") or ca.get("answers") or [])
    return len(ca)

def all_jobs(c, light=True):
    out = []
    for r in c.execute("SELECT data FROM jobs"):
        d = json.loads(r["data"])
        if light:
            d["search"] = search_blob(d)
            d["n_answers"] = len(d.pop("answers", []) or [])
            d["n_custom"] = _custom_count(d.pop("custom_answers", None))
            d["n_questions"] = len(d.get("questions") or [])
            d.pop("questions", None)
            d.pop("jd", None)
        out.append(d)
    if CFG.queue_order == "newest_best":
        QO.order(out, CFG.today(), is_hot)
    else:
        out.sort(key=lambda d: (0 if is_hot(d) else 1, d.get("queue_rank") or 9999, d.get("ref")))
    return out


def ensure_custom_answers(c, ref, force=False, actor="app"):
    """Generate paste-ready custom answers if missing (or when force=True). Never deletes the job."""
    row = c.execute("SELECT data FROM jobs WHERE ref=?", (ref,)).fetchone()
    if not row:
        return None
    d = json.loads(row["data"])
    if d.get("status") in ("Closed", "Withdrawn") and not force:
        return d
    if d.get("custom_answers") and not force:
        return d
    items = CA.generate(d)
    d["custom_answers"] = {"items": items, "generated_at": now(), "source": "auto"}
    d["custom_answers_at"] = now()
    d["updated_at"] = now()
    c.execute("UPDATE jobs SET data=?, updated_at=? WHERE ref=?", (json.dumps(d), d["updated_at"], ref))
    c.execute("INSERT INTO history(ref,ts,field,old,new,actor) VALUES(?,?,?,?,?,?)",
              (ref, now(), "custom_answers", "" if not force else "regenerated",
               "generated %d answers" % len(items), actor))
    return d

def generate_missing_custom(c, force=False, refs=None, actor="app"):
    generated = []
    skipped = []
    rows = c.execute("SELECT ref, data FROM jobs").fetchall()
    for r in rows:
        ref = r["ref"]
        if refs and ref not in refs:
            continue
        d = json.loads(r["data"])
        if d.get("status") in ("Closed", "Withdrawn") and not force:
            skipped.append(ref)
            continue
        if d.get("custom_answers") and not force:
            skipped.append(ref)
            continue
        ensure_custom_answers(c, ref, force=True, actor=actor)
        generated.append(ref)
    c.commit()
    return {"generated": generated, "skipped": skipped, "count": len(generated)}

def alerts_ingest(c, b, actor="job-alerts"):
    """Add roles from job-alert emails. Body: {"emails":[{"id","subject","text"}], "items":[parsed roles],
    "dry_run":bool, "force":bool}. Dedupes against every tracked role (any status), triages by title
    (main queue or Stretch), remembers which emails it has read, and never deletes or changes a tracked role."""
    dry = bool(b.get("dry_run"))
    seen = {r[0] for r in c.execute("SELECT email_id FROM alert_seen")}
    items, read_ids, skipped = [dict(i) for i in (b.get("items") or []) if isinstance(i, dict)], [], []
    for e in b.get("emails") or []:
        eid = str(e.get("id") or "")
        if eid and eid in seen and not b.get("force"):
            skipped.append(eid)
            continue
        parsed = AL.parse_email(e.get("text") or "", eid, e.get("subject") or "")
        items.extend(parsed)
        read_ids.append((eid, len(parsed)))
    for i in items:
        i.setdefault("email_id", "")
    add, dups = AL.plan(items, all_jobs(c, light=False), b.get("source") or None)
    out = {"dry_run": dry, "emails_read": len(read_ids), "emails_already_read": skipped, "roles_found": len(items),
           "duplicates": dups, "added": []}
    if not dry and add:
        seed_import(c, {"jobs": add}, actor=actor)
    out["added"] = [{"ref": r.get("ref"), "company": r["company"], "role": r["role"],
                     "lane": "stretch" if r.get("stretch") else "main"} for r in add]
    if not dry:
        per = {}
        for r in add:
            per[r.get("alert_email_id")] = per.get(r.get("alert_email_id"), 0) + 1
        for eid, n in read_ids:
            if eid:
                c.execute("INSERT OR REPLACE INTO alert_seen(email_id,ts,found,added) VALUES(?,?,?,?)", (eid, now(), n, per.get(eid, 0)))
        for eid in {i["email_id"] for i in items if i.get("email_id")} - {r[0] for r in read_ids}:
            c.execute("INSERT OR IGNORE INTO alert_seen(email_id,ts,found,added) VALUES(?,?,?,?)", (eid, now(), 0, per.get(eid, 0)))
        c.commit()
    out["main"] = sum(1 for r in out["added"] if r["lane"] == "main")
    out["stretch"] = len(out["added"]) - out["main"]
    return out


def patch_job(c, ref, body, actor="web"):
    row = c.execute("SELECT data FROM jobs WHERE ref=?", (ref,)).fetchone()
    if not row:
        return None
    d = json.loads(row["data"])
    body = dict(body or {})
    if body.pop("regenerate_custom_answers", False):
        items = CA.generate(d)
        body["custom_answers"] = {"items": items, "generated_at": now(), "source": "auto"}
        body["custom_answers_at"] = now()
    if body.pop("undo_close", False):
        prev = d.get("status_before_close") or "To apply"
        if prev not in STATUSES:
            prev = "To apply"
        body = {"status": prev, "closed_reason": "", "closed_at": "", "status_before_close": ""}
    elif body.get("status") == "Closed" and d.get("status") != "Closed":
        body.setdefault("closed_at", now_et())
        body.setdefault("status_before_close", d.get("status") or "To apply")
    for k, v in body.items():
        if k in ("ref", "history", "screenshots", "undo_close"):
            continue
        old = d.get(k)
        if old == v:
            continue
        if k == "status":
            if v not in STATUSES:
                raise ValueError("bad status " + str(v))
            if v == "Applied" and not d.get("date_applied"):
                d["date_applied"] = today_et().isoformat()
                c.execute("INSERT INTO history(ref,ts,field,old,new,actor) VALUES(?,?,?,?,?,?)", (ref, now(), "date_applied", "", d["date_applied"], actor))
        d[k] = v
        o = old if isinstance(old, str) or old is None else json.dumps(old)
        n = v if isinstance(v, str) else json.dumps(v)
        c.execute("INSERT INTO history(ref,ts,field,old,new,actor) VALUES(?,?,?,?,?,?)", (ref, now(), k, (o or "")[:2000], (n or "")[:2000], actor))
    d["updated_at"] = now()
    c.execute("UPDATE jobs SET data=?, status=?, updated_at=? WHERE ref=?", (json.dumps(d), d.get("status"), d["updated_at"], ref))
    c.commit()
    return job_out(c, ref)


def format_ref(n):
    return "J-%03d" % n

def ref_inventory(jobs):
    """J-id span and unused ids (never issued, not deleted)."""
    nums = []
    for j in jobs:
        m = re.match(r"^J-(\d+)$", j.get("ref") or "")
        if m:
            nums.append(int(m.group(1)))
    if not nums:
        return {"ref_min": None, "ref_max": None, "unused_count": 0, "unused_ranges": []}
    lo, hi = min(nums), max(nums)
    have = set(nums)
    unused = [i for i in range(lo, hi + 1) if i not in have]
    ranges = []
    if unused:
        a = b = unused[0]
        for n in unused[1:]:
            if n == b + 1:
                b = n
            else:
                ranges.append((a, b))
                a = b = n
        ranges.append((a, b))
    def fmt(a, b):
        return format_ref(a) if a == b else "%s to %s" % (format_ref(a), format_ref(b))
    return {
        "ref_min": lo,
        "ref_max": hi,
        "unused_count": len(unused),
        "unused_ranges": [fmt(a, b) for a, b in ranges],
    }


def stats(c):
    jobs = all_jobs(c)
    by_status = {s: 0 for s in STATUSES}
    by_source = {}
    per_day = {}
    for j in jobs:
        by_status[j.get("status", "To apply")] = by_status.get(j.get("status", "To apply"), 0) + 1
        by_source[j.get("source_group") or j.get("source") or "Unknown"] = by_source.get(j.get("source_group") or j.get("source") or "Unknown", 0) + 1
        da = j.get("date_applied")
        if da:
            per_day[da[:10]] = per_day.get(da[:10], 0) + 1
    today_d = today_et()
    today = today_d.isoformat()
    # streak = consecutive days up to today with >=1 application
    streak, day = 0, today_d
    while per_day.get(day.isoformat()):
        streak += 1; day -= datetime.timedelta(days=1)
    goal_row = c.execute("SELECT v FROM settings WHERE k='daily_goal'").fetchone()
    goal = json.loads(goal_row["v"]) if goal_row else CFG.daily_goal
    applied_total = sum(1 for j in jobs if j.get("date_applied"))
    funnel = [
        ["Tracked", len(jobs)],
        ["Applied", applied_total],
        ["Next steps+", sum(1 for j in jobs if j.get("status") in ("Next steps", "Interview scheduled", "Offer"))],
        ["Interview", sum(1 for j in jobs if j.get("status") in ("Interview scheduled", "Offer") or j.get("interview_at"))],
        ["Offer", by_status.get("Offer", 0)],
    ]
    upcoming = sorted([{"ref": j["ref"], "company": j.get("company"), "role": j.get("role"), "at": j.get("interview_at")} for j in jobs if j.get("interview_at") and j["interview_at"][:10] >= today], key=lambda x: x["at"])
    followups = sorted([{"ref": j["ref"], "company": j.get("company"), "role": j.get("role"), "at": j.get("follow_up")} for j in jobs if j.get("follow_up") and j.get("status") not in ("Rejected", "Closed", "Withdrawn")], key=lambda x: x["at"])
    stale = [ {"ref": j["ref"], "company": j.get("company"), "role": j.get("role"), "date_applied": j.get("date_applied")} for j in jobs
              if j.get("status") == "Applied" and j.get("date_applied") and (today_d - datetime.date.fromisoformat(j["date_applied"][:10])).days >= 10 ]
    pol = CP.company_policy(c, jobs, today=today_d)
    queued = pol["queued_to_apply"]
    paused = pol["paused_to_apply"]
    stretch_n = pol.get("stretch_to_apply", 0)
    next_plus = sum(1 for j in jobs if j.get("status") in ("Next steps", "Interview scheduled", "Offer"))
    applied_all_time = sum(1 for j in jobs if j.get("date_applied") or j.get("gmail_receipt_url") or j.get("receipt_url"))
    inv = ref_inventory(jobs)
    census = [
        ["In queue", queued],
        ["Stretch", stretch_n],
        ["Paused (limit / skip)", paused],
        ["Applied (open)", by_status.get("Applied", 0)],
        ["Next steps + interviews", next_plus],
        ["Rejected", by_status.get("Rejected", 0)],
        ["Closed / skipped", by_status.get("Closed", 0)],
    ]
    if by_status.get("Withdrawn", 0):
        census.append(["Withdrawn", by_status.get("Withdrawn", 0)])
    census_sum = sum(v for _, v in census)
    tracked_line = "Total tracked: %d" % len(jobs)
    if inv["ref_min"] is not None:
        span = "%s to %s" % (format_ref(inv["ref_min"]), format_ref(inv["ref_max"]))
        if inv["unused_count"]:
            tracked_line += " (%s, %d ids unused: %s)" % (span, inv["unused_count"], ", ".join(inv["unused_ranges"]))
        else:
            tracked_line += " (%s)" % span
    if census_sum == len(jobs):
        tracked_line += ". The status tiles below sum to %d." % census_sum
    else:
        tracked_line += ". Status tiles sum to %d, not %d." % (census_sum, len(jobs))
    disp = CFG.display()
    return {"total": len(jobs), "by_status": by_status, "by_source": by_source, "per_day": per_day, "today": today,
            "app_name": disp["app_name"], "display_name": disp["display_name"], "legal_name": disp["legal_name"],
            "brand": disp["brand"], "identity_vault": disp["identity_vault"], "assistant_name": disp["assistant_name"],
            "first_name": disp["first_name"], "logo": disp["logo"], "favicon": disp["favicon"],
            "default_resume": disp["default_resume"], "resume_note": disp["resume_note"], "timezone_label": disp["timezone_label"],
            "applied_today": per_day.get(today, 0), "goal": goal, "streak": streak, "funnel": funnel,
            "upcoming": upcoming, "followups": followups, "stale": stale, "statuses": STATUSES, "version": VERSION,
            "queued_to_apply": queued, "paused_to_apply": paused, "stretch_to_apply": stretch_n,
            "policy_banners": pol["banners"],
            "applied_all_time": applied_all_time,
            "census": census, "census_sum": census_sum,
            "ref_min": inv["ref_min"], "ref_max": inv["ref_max"],
            "unused_count": inv["unused_count"], "unused_ranges": inv["unused_ranges"],
            "tracked_line": tracked_line}


def resolve_progress_start(c):
    """Use the stored start date, then config, then today. Persist the first value."""
    row = c.execute("SELECT v FROM settings WHERE k='progress_start'").fetchone()
    start = None
    if row:
        try:
            raw = json.loads(row["v"])
        except Exception:
            raw = row["v"]
        start = parse_iso_date(raw)
    if start is None:
        start = PROGRESS_START or today_et()
        c.execute(
            "INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            ("progress_start", json.dumps(start.isoformat())),
        )
        c.commit()
    return start

def progress(c):
    """Daily application counts from the progress start date through today.
    A job counts on its date_applied. Jobs applied before the start are summarized
    only, not charted. Unmapped extra applications are a footnote, not in the bars.
    """
    jobs = all_jobs(c)
    start = resolve_progress_start(c)
    today = today_et()
    if today < start:
        today = start
    per_day = {}
    before_refs = []
    for j in jobs:
        da = parse_iso_date(j.get("date_applied"))
        if not da:
            continue
        rec = {"ref": j.get("ref"), "company": j.get("company") or ""}
        if da < start:
            before_refs.append(rec)
        else:
            per_day.setdefault(da.isoformat(), []).append(rec)
    days = []
    cumulative = 0
    best = {"date": today.isoformat(), "count": 0}
    d = start
    while d <= today:
        key = d.isoformat()
        refs = sorted(per_day.get(key, []), key=lambda r: r["ref"] or "")
        count = len(refs)
        cumulative += count
        days.append({
            "date": key,
            "count": count,
            "cumulative": cumulative,
            "refs": [r["ref"] for r in refs],
        })
        if count > best["count"]:
            best = {"date": key, "count": count}
        d += datetime.timedelta(days=1)
    total = cumulative
    n_days = len(days) or 1
    weekday = today.weekday()
    week_start = today - datetime.timedelta(days=weekday)
    if week_start < start:
        week_start = start
    this_week = sum(x["count"] for x in days if week_start.isoformat() <= x["date"] <= today.isoformat())
    streak, day = 0, today
    while day >= start and per_day.get(day.isoformat()):
        streak += 1
        day -= datetime.timedelta(days=1)
    goal_row = c.execute("SELECT v FROM settings WHERE k='daily_goal'").fetchone()
    try:
        goal = int(json.loads(goal_row["v"])) if goal_row else CFG.daily_goal
    except Exception:
        goal = CFG.daily_goal
    if goal < 1:
        goal = CFG.daily_goal or 5
    extras = []
    try:
        row = c.execute("SELECT v FROM settings WHERE k='company_extra_applications'").fetchone()
        raw = json.loads(row["v"]) if row else []
        for e in raw or []:
            da = parse_iso_date(e.get("date_applied"))
            if da and da >= start:
                extras.append({"company": e.get("company") or "", "date_applied": da.isoformat()})
    except Exception:
        extras = []
    disp = CFG.display()
    return {
        "version": VERSION,
        "app_name": disp["app_name"],
        "display_name": disp["display_name"],
        "start_date": start.isoformat(),
        "today": today.isoformat(),
        "goal": goal,
        "total": total,
        "today_count": len(per_day.get(today.isoformat(), [])),
        "this_week": this_week,
        "streak": streak,
        "best_day": best,
        "daily_average": round(total / n_days, 2),
        "before_reset": len(before_refs),
        "before_reset_refs": [r["ref"] for r in before_refs],
        "days": days,
        "unmapped_extras": extras,
    }

def ics_for(j):
    at = j.get("interview_at")
    if not at:
        return None
    try:
        start = datetime.datetime.fromisoformat(at)
    except ValueError:
        return None
    if start.tzinfo is None:
        start = start.astimezone()
    mins = int(j.get("interview_minutes") or 30)
    end = start + datetime.timedelta(minutes=mins)
    f = lambda t: t.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    esc = lambda s: (s or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")
    title = "Interview: %s, %s" % (j.get("company"), j.get("role"))
    desc = "Job %s. %s\nApply/posting: %s\nNotes: %s" % (j["ref"], j.get("interview_with") or "", j.get("posting_url") or j.get("apply_url") or "", j.get("interview_notes") or "")
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//nixfred//nixnet-job-command//EN", "BEGIN:VEVENT",
                        "UID:%s-%s@nixnet-job-command" % (j["ref"], f(start)), "DTSTAMP:" + f(datetime.datetime.now().astimezone()),
                        "DTSTART:" + f(start), "DTEND:" + f(end), "SUMMARY:" + esc(title), "DESCRIPTION:" + esc(desc),
                        "LOCATION:" + esc(j.get("interview_location") or ""), "END:VEVENT", "END:VCALENDAR", ""])

CSV_COLS = ["ref", "queue_rank", "company", "role", "status", "hot", "due_at", "due_label", "stretch", "stretch_reason", "closed_reason", "closed_at", "fit_score", "tier", "source", "referral_date", "comp", "location", "remote",
            "posted", "apply_url", "posting_url", "date_applied", "gmail_receipt_url", "interview_at", "follow_up", "onepassword_identity",
            "resume_version", "cover_letter", "contacts", "notes"]

class H(BaseHTTPRequestHandler):
    server_version = "NixNetJobCommand/" + VERSION

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (now(), fmt % args))

    def send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, indent=None).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 40 * 1024 * 1024:
            raise ValueError("too large")
        raw = self.rfile.read(n) if n else b""
        return json.loads(raw or b"{}")

    def static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        rel = path.lstrip("/")
        fp = None
        # An overlay folder (config static_overlay) may shadow any static file.
        ov = CFG.static_overlay
        if ov:
            cand = os.path.normpath(os.path.join(ov, rel))
            if cand.startswith(os.path.normpath(ov) + os.sep) and os.path.isfile(cand):
                fp = cand
        if fp is None:
            fp = os.path.normpath(os.path.join(STATIC, rel))
            if not fp.startswith(STATIC) or not os.path.isfile(fp):
                fp = os.path.join(STATIC, "index.html")
        ext = os.path.splitext(fp)[1]
        ct = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
              ".png": "image/png", ".svg": "image/svg+xml", ".json": "application/json", ".webmanifest": "application/manifest+json"}.get(ext, "application/octet-stream")
        with open(fp, "rb") as _f:
            body = _f.read()
        if ext == ".html":
            disp = CFG.display()
            text = body.decode("utf-8")
            for k, v in (("app_name", disp["app_name"]), ("brand", disp["brand"]), ("logo", disp["logo"]),
                         ("favicon", disp["favicon"]), ("version", VERSION)):
                text = text.replace("{{%s}}" % k, str(v).replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;"))
            body = text.encode("utf-8")
        self.send(200, body, ct)

    def do_HEAD(self):
        self.do_GET()

    def refuse(self, mutating):
        """Send 403 and return True when the request fails the Host / Origin checks."""
        why = request_problem(self.headers, mutating)
        if why:
            self.send(403, {"error": why})
            return True
        return False

    def do_GET(self):
        if self.refuse(False):
            return
        u = urlparse(self.path); p = u.path; q = parse_qs(u.query)
        try:
            with _lock:
                c = db()
                try:
                    return self.route_get(c, p, q)
                finally:
                    c.close()
        except Exception as e:
            self.send(500, {"error": str(e)})

    def route_get(self, c, p, q):
        if p == "/api/health":
            disp = CFG.display()
            payload = {"ok": True, "version": VERSION, "app_name": disp["app_name"], "display_name": disp["display_name"],
                       "jobs": c.execute("SELECT count(*) FROM jobs").fetchone()[0], "time": now(), "contacts_live": crustdata_live_available()}
            return self.send(200, payload)
        if p == "/api/jobs":
            jobs = all_jobs(c, light=q.get("full", ["0"])[0] != "1")
            pol = CP.company_policy(c, jobs)
            CP.annotate_jobs(jobs, pol)
            st = q.get("status", [None])[0]
            if st:
                jobs = [j for j in jobs if j.get("status") == st]
            stretch_q = (q.get("stretch") or ["0"])[0]
            if stretch_q in ("1", "true", "yes"):
                jobs = [j for j in jobs if is_stretch(j)]
            elif stretch_q in ("0", "false") and "stretch" in q:
                jobs = [j for j in jobs if not is_stretch(j)]
            return self.send(200, jobs)
        m = re.match(r"^/api/jobs/([A-Za-z0-9\-]+)$", p)
        if m:
            j = job_out(c, m.group(1))
            return self.send(200, j) if j else self.send(404, {"error": "not found"})
        m = re.match(r"^/api/jobs/([A-Za-z0-9\-]+)/ics$", p)
        if m:
            j = job_out(c, m.group(1), full=False)
            ics = ics_for(j) if j else None
            if not ics:
                return self.send(404, {"error": "no interview time set"})
            return self.send(200, ics, "text/calendar; charset=utf-8", {"Content-Disposition": "attachment; filename=%s-interview.ics" % m.group(1)})
        m = re.match(r"^/api/jobs/([A-Za-z0-9\-]+)/contacts$", p)
        if m:
            ref = m.group(1)
            if not c.execute("SELECT 1 FROM jobs WHERE ref=?", (ref,)).fetchone():
                return self.send(404, {"error": "not found"})
            return self.send(200, {"ref": ref, "contacts": contacts_for(c, ref), "contacts_live": crustdata_live_available()})
        if p == "/api/stats":
            return self.send(200, stats(c))
        if p == "/api/progress":
            return self.send(200, progress(c))
        if p == "/api/company-policy":
            jobs = all_jobs(c)
            return self.send(200, CP.company_policy(c, jobs))
        if p == "/api/answers":
            return self.send(200, [answer_row(r) for r in c.execute("SELECT * FROM answers ORDER BY sort, category, question")])
        if p == "/api/alerts/seen":
            return self.send(200, {"email_ids": [r[0] for r in c.execute("SELECT email_id FROM alert_seen ORDER BY ts DESC")]})
        if p == "/api/duplicates":
            jobs = all_jobs(c, light=False)
            later_only = (q.get("later") or ["0"])[0] == "1"
            pairs = DUP.audit_pairs(jobs, require_later=later_only)
            return self.send(200, {"pairs": pairs, "count": len(pairs), "later_only": later_only})
        if p == "/api/feedback":
            return self.send(200, [dict(r) for r in c.execute("SELECT * FROM feedback ORDER BY id DESC")])
        if p == "/api/history":
            return self.send(200, [dict(r) for r in c.execute("SELECT * FROM history ORDER BY id DESC LIMIT 500")])
        if p == "/api/settings":
            return self.send(200, {r["k"]: json.loads(r["v"]) for r in c.execute("SELECT k,v FROM settings")})
        if p == "/api/export.json":
            data = {"exported_at": now(), "version": VERSION, "jobs": [job_out(c, j["ref"]) for j in all_jobs(c)],
                    "answers": [answer_row(r) for r in c.execute("SELECT * FROM answers")], "feedback": [dict(r) for r in c.execute("SELECT * FROM feedback")]}
            return self.send(200, json.dumps(data, indent=1), "application/json", {"Content-Disposition": "attachment; filename=nixnet-job-command-%s.json" % datetime.date.today()})
        if p == "/api/export.csv":
            buf = io.StringIO(); w = csv.writer(buf); w.writerow(CSV_COLS)
            for j in all_jobs(c):
                w.writerow([j.get(k) if not isinstance(j.get(k), (list, dict)) else json.dumps(j.get(k)) for k in CSV_COLS])
            return self.send(200, buf.getvalue(), "text/csv; charset=utf-8", {"Content-Disposition": "attachment; filename=nixnet-job-command-%s.csv" % datetime.date.today()})
        m = re.match(r"^/shots/([A-Za-z0-9\-_.]+)$", p)
        if m:
            fp = os.path.join(SHOTS, m.group(1))
            if os.path.isfile(fp):
                ct = "image/png" if fp.endswith(".png") else "image/jpeg" if fp.endswith((".jpg", ".jpeg")) else "image/webp" if fp.endswith(".webp") else "application/octet-stream"
                with open(fp, "rb") as _f:
                    return self.send(200, _f.read(), ct)
            return self.send(404, {"error": "no file"})
        return self.static(p)

    def do_POST(self):
        self.mutate("POST")

    def do_PATCH(self):
        self.mutate("PATCH")

    def do_DELETE(self):
        # Deliberately no hard deletes: screenshots are archived, jobs are Closed/Withdrawn.
        if self.refuse(True):
            return
        u = urlparse(self.path)
        m = re.match(r"^/api/shots/([A-Za-z0-9\-]+)$", u.path)
        if not m:
            return self.send(405, {"error": "no deletes; set status to Closed or Withdrawn instead"})
        with _lock:
            c = db()
            r = c.execute("SELECT ref,filename FROM shots WHERE id=?", (m.group(1),)).fetchone()
            if r:
                os.makedirs(os.path.join(SHOTS, "archive"), exist_ok=True)
                try:
                    os.replace(os.path.join(SHOTS, r["filename"]), os.path.join(SHOTS, "archive", r["filename"]))
                except OSError:
                    pass
                c.execute("DELETE FROM shots WHERE id=?", (m.group(1),))
                c.execute("INSERT INTO history(ref,ts,field,old,new,actor) VALUES(?,?,?,?,?,?)", (r["ref"], now(), "screenshot archived", r["filename"], "", "web"))
                c.commit()
            c.close()
        self.send(200, {"ok": True})

    def mutate(self, method):
        if self.refuse(True):
            return
        u = urlparse(self.path); p = u.path
        try:
            b = self.body()
        except Exception as e:
            return self.send(400, {"error": "bad json: %s" % e})
        actor = self.headers.get("X-Actor") or "web"
        try:
            with _lock:
                c = db()
                try:
                    return self.route_mutate(c, method, p, b, actor)
                finally:
                    c.close()
        except ValueError as e:
            self.send(400, {"error": str(e)})
        except Exception as e:
            self.send(500, {"error": str(e)})

    def route_mutate(self, c, method, p, b, actor):
        m = re.match(r"^/api/jobs/([A-Za-z0-9\-]+)$", p)
        if m and method in ("PATCH", "POST"):
            j = patch_job(c, m.group(1), b, actor)
            return self.send(200, j) if j else self.send(404, {"error": "not found"})
        if p == "/api/jobs" and method == "POST":
            refs = [r[0] for r in c.execute("SELECT ref FROM jobs WHERE ref LIKE 'J-%'")]
            nxt = max([int(r[2:]) for r in refs if r[2:].isdigit()] + [0]) + 1
            b["ref"] = b.get("ref") or "J-%03d" % nxt
            if not valid_ref(b["ref"]):
                raise ValueError("bad ref: use letters, digits and '-' only (max 64)")
            b.setdefault("status", "To apply")
            b.setdefault("source", "Added by hand")
            res = seed_import(c, {"jobs": [b]}, actor=actor)
            return self.send(201, job_out(c, b["ref"]))
        m = re.match(r"^/api/jobs/([A-Za-z0-9\-]+)/screenshots$", p)
        if m and method == "POST":
            ref = m.group(1)
            if not c.execute("SELECT 1 FROM jobs WHERE ref=?", (ref,)).fetchone():
                return self.send(404, {"error": "no job"})
            durl = b.get("data_url", "")
            mm = re.match(r"^data:image/(png|jpeg|jpg|webp|gif);base64,(.+)$", durl, re.S)
            if not mm:
                return self.send(400, {"error": "expected data_url of an image"})
            ext = "jpg" if mm.group(1) in ("jpeg", "jpg") else mm.group(1)
            sid = uuid.uuid4().hex[:12]
            fn = "%s-%s-%s.%s" % (ref, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"), sid, ext)
            with open(os.path.join(SHOTS, fn), "wb") as f:
                f.write(base64.b64decode(mm.group(2)))
            c.execute("INSERT INTO shots(id,ref,filename,caption,ts) VALUES(?,?,?,?,?)", (sid, ref, fn, b.get("caption", ""), now()))
            c.execute("INSERT INTO history(ref,ts,field,old,new,actor) VALUES(?,?,?,?,?,?)", (ref, now(), "screenshot", "", fn, actor))
            c.commit()
            return self.send(201, {"id": sid, "filename": fn})
        if p == "/api/answers" and method == "POST":
            aid = b.get("id") or "a-" + uuid.uuid4().hex[:8]
            if not ANSWER_ID_RE.match(str(aid)):
                raise ValueError("bad answer id: use letters, digits, '-' and '_' only (max 64)")
            c.execute("INSERT INTO answers(id,category,question,answer,%s,sort,updated_at) VALUES(?,?,?,?,?,?,?)" % ANS_FLAG,
                      (aid, b.get("category", "Custom"), b.get("question", ""), b.get("answer", ""), 1 if b.get("needs_review") or b.get("fred_decides") else 0, b.get("sort", 500), now()))
            c.commit()
            return self.send(201, {"id": aid})
        m = re.match(r"^/api/answers/([A-Za-z0-9\-_]+)$", p)
        if m:
            sets = []; vals = []
            if "fred_decides" in b and "needs_review" not in b:
                b["needs_review"] = b.get("fred_decides")
            for k in ("category", "question", "answer", "needs_review", "sort", "used"):
                if k in b:
                    sets.append((ANS_FLAG if k == "needs_review" else k) + "=?"); vals.append(b[k])
            if sets:
                c.execute("UPDATE answers SET %s, updated_at=? WHERE id=?" % ",".join(sets), vals + [now(), m.group(1)])
                c.commit()
            return self.send(200, {"ok": True})
        if p == "/api/feedback" and method == "POST":
            c.execute("INSERT INTO feedback(ts,kind,text,ref) VALUES(?,?,?,?)", (now(), b.get("kind", "idea"), b.get("text", ""), b.get("ref")))
            c.commit()
            return self.send(201, {"ok": True})
        if p == "/api/settings" and method in ("POST", "PATCH"):
            for k, v in b.items():
                c.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))
            c.commit()
            return self.send(200, {"ok": True})
        if p == "/api/alerts/ingest" and method == "POST":
            return self.send(200, alerts_ingest(c, b, actor if actor != "web" else "job-alerts"))
        if p == "/api/import" and method == "POST":
            # Accepts {"jobs":[...], "answers":[...]} (e.g. a seed refresh or an export). Never deletes.
            return self.send(200, seed_import(c, b, actor=actor, overwrite=bool(b.get("overwrite"))))
        m = re.match(r"^/api/jobs/([A-Za-z0-9\-]+)/stretch$", p)
        if m and method == "POST":
            ref = m.group(1)
            row = c.execute("SELECT data FROM jobs WHERE ref=?", (ref,)).fetchone()
            if not row:
                return self.send(404, {"error": "not found"})
            want = b.get("stretch")
            if want is None:
                want = True
            reason = b.get("stretch_reason")
            body = {"stretch": bool(want)}
            if reason is not None:
                body["stretch_reason"] = reason
            # Reopen a closed fit-reason role into Stretch when asked.
            dcur = json.loads(row["data"])
            if body["stretch"] and dcur.get("status") == "Closed":
                body["status"] = "To apply"
                body["status_before_close"] = dcur.get("status_before_close") or dcur.get("status") or "To apply"
            j = patch_job(c, ref, body, actor)
            return self.send(200, j) if j else self.send(404, {"error": "not found"})
        m = re.match(r"^/api/jobs/([A-Za-z0-9\-]+)/custom-answers$", p)
        if m and method == "POST":
            ref = m.group(1)
            d = ensure_custom_answers(c, ref, force=bool(b.get("force", True)), actor=actor)
            if not d:
                return self.send(404, {"error": "not found"})
            c.commit()
            return self.send(200, job_out(c, ref))
        if p == "/api/custom-answers/generate" and method == "POST":
            refs = b.get("refs")
            force = bool(b.get("force"))
            res = generate_missing_custom(c, force=force, refs=refs, actor=actor)
            return self.send(200, res)
        if p == "/api/company-skips" and method == "POST":
            CP.upsert_skip(c, b.get("company"), until=b.get("until"), source=b.get("source") or "manual",
                           note=b.get("note"), now_iso=now())
            c.commit()
            return self.send(200, CP.company_policy(c, all_jobs(c)))
        if p == "/api/company-skips/resume" and method == "POST":
            CP.resume_skip(c, b.get("company"))
            c.commit()
            return self.send(200, CP.company_policy(c, all_jobs(c)))
        if p == "/api/company-apply-limits" and method in ("POST", "PATCH"):
            if isinstance(b.get("limits"), list) and "company" not in b:
                CP.replace_limits(c, b.get("limits"))
            else:
                CP.upsert_limit(c, b)
            c.commit()
            return self.send(200, CP.company_policy(c, all_jobs(c)))
        if p == "/api/company-apply-limits/remove" and method == "POST":
            CP.remove_limit(c, b.get("company"))
            c.commit()
            return self.send(200, CP.company_policy(c, all_jobs(c)))
        if p == "/api/company-extra-applications" and method == "POST":
            CP.upsert_extra(c, b)
            c.commit()
            return self.send(200, CP.company_policy(c, all_jobs(c)))
        m = re.match(r"^/api/jobs/([A-Za-z0-9\-]+)/contacts/refresh$", p)
        if m and method == "POST":
            ref = m.group(1)
            if not c.execute("SELECT 1 FROM jobs WHERE ref=?", (ref,)).fetchone():
                return self.send(404, {"error": "not found"})
            people = b.get("contacts") if isinstance(b, dict) else None
            if people:
                stored = replace_contacts(c, ref, people, actor=actor)
                c.commit()
                return self.send(200, {"ok": True, "ref": ref, "source": "stored", "contacts": stored, "contacts_live": crustdata_live_available()})
            if crustdata_live_available():
                return self.send(501, {"ok": False, "error": "A Crustdata key is present on this server, but live search is not wired in this build. Store contacts through POST /api/jobs/<ref>/contacts after a lookup.", "agent_driven": True, "ref": ref, "contacts": contacts_for(c, ref)})
            return self.send(200, {"ok": False, "agent_driven": True, "ref": ref, "contacts": contacts_for(c, ref), "contacts_live": False, "message": "Crustdata is not configured on this server. Look people up and store the results here. No one is messaged."})
        m = re.match(r"^/api/jobs/([A-Za-z0-9\-]+)/contacts$", p)
        if m and method == "POST":
            ref = m.group(1)
            people = (b.get("contacts") if isinstance(b, dict) else None) or (b if isinstance(b, list) else None)
            if people is None:
                return self.send(400, {"error": "expected {\"contacts\":[...]}"})
            stored = replace_contacts(c, ref, people, actor=actor)
            if stored is None:
                return self.send(404, {"error": "not found"})
            c.commit()
            return self.send(200, {"ok": True, "ref": ref, "contacts": stored})
        return self.send(404, {"error": "no route"})

def serve(addr):
    while True:
        try:
            httpd = ThreadingHTTPServer((addr, PORT), H)
            sys.stderr.write("%s listening on http://%s:%d\n" % (now(), addr, PORT))
            httpd.serve_forever()
        except OSError as e:
            sys.stderr.write("%s bind %s:%d failed (%s), retrying in 30s\n" % (now(), addr, PORT, e))
            time.sleep(30)

if __name__ == "__main__":
    CFG.require_python()
    apply_runtime()
    init()
    ts = []
    for a in BIND:
        t = threading.Thread(target=serve, args=(a,), daemon=True); t.start(); ts.append(t)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        sys.stderr.write("\n%s stopped\n" % now())
