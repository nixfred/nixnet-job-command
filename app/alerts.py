#!/usr/bin/env python3
"""Job-alert intake for NixNet Job Command.

Alert emails from LinkedIn, Dice and Built In are read (see parse_email).
A LinkedIn alert lists roles in a regular layout:

    Your job alert for <search name>
    <job title>
    <company>
    <location>
    <badge lines such as "Fast growing">
    View job: https://www.linkedin.com/comm/jobs/view/<id>/?tracking...
    ----------------------------------------

This module turns that text into role dicts, drops the ones already tracked
(same LinkedIn job id, same apply URL, or same company plus a near-identical
title), and triages the rest by title so nothing is lost: roles that look like
your target work go to the main apply queue, everything else goes to Stretch
with a reason. It never applies to anything and never reads a mailbox itself.
Whoever has the mail (an agent, a script with its own Gmail login) hands the
text to POST /api/alerts/ingest.
"""
import re
from urllib.parse import unquote

import duplicates as DUP

JOB_ID_URL = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", re.I)
VIEW_JOB = re.compile(r"^View job:\s*(\S+)", re.I)
ALERT_HEAD = re.compile(r"^Your job alert for\s+(.+)$", re.I)
DASHES = re.compile(r"^-{5,}$")
URL_ONLY = re.compile(r"^https?://\S+$", re.I)
INLINE_LINK_LABEL = re.compile(r"^(View all jobs|See all jobs on LinkedIn|Manage your job alerts|Unsubscribe|Learn why we included this)\b", re.I)
SALARY = re.compile(r"^\$[\d,.]+\s*[kKmM]?(?:\s*/\s*(?:yr|year|hr|hour))?(?:\s*[-–to]+\s*\$?[\d,.]+\s*[kKmM]?(?:\s*/\s*(?:yr|year|hr|hour))?)?(?:\s*\S.*)?$")
BADGE = re.compile(
    r"^(fast growing|this company is actively hiring|actively (?:hiring|recruiting)|apply with resume.*|easy apply|promoted|"
    r"be an early applicant|new|reposted.*|\d+\s+(?:connections?|school alumni.*|alumni.*|applicants?.*|company alumni.*)|"
    r".*\bconnections?)$", re.I)

# Roles that look like the target work: solutions / forward deployed / applied AI.
TARGET = re.compile(
    r"solutions?\s+(?:engineer|architect|consultant)|sales\s+engineer|pre-?sales|forward[- ]deployed|\bfde\b|"
    r"deployed\s+engineer|customer\s+(?:engineer|solutions)|field\s+(?:engineer|cto|solutions)|applied\s+ai|"
    r"ai\s+(?:solutions?|architect|engineer|forward)|technical\s+(?:account|solutions?|sales)|"
    r"partner\s+(?:solutions?|engineer)|developer\s+(?:relations|advocate)|enterprise\s+(?:se|solutions)|"
    r"solution\s+(?:engineer|architect)", re.I)
JUNIOR = re.compile(r"\b(?:intern|internship|entry[- ]level|new grad|graduate|apprentice|associate|junior|jr\.?)\b", re.I)
CLEARANCE = re.compile(r"\b(?:classified|clearance|ts\s*/\s*sci|top secret|polygraph)\b", re.I)
AGENCY = re.compile(r"^\s*(?:hiring|urgent(?:ly)?(?: hiring)?|immediate(?:ly)? hiring)\b[\s:-]+(?:for\b)?", re.I)
LEADERSHIP = re.compile(r"\b(?:vice president|vp|svp|evp|chief|head of|director|manager|president|cell lead)\b", re.I)


def linkedin_job_id(*urls):
    for u in urls:
        m = JOB_ID_URL.search(u or "")
        if m:
            return m.group(1)
    return ""


def job_link(job_id):
    return "https://www.linkedin.com/jobs/view/%s/" % job_id


def parse_linkedin(text, email_id="", subject=""):
    """Return [{title, company, location, comp, job_id, alert, email_id}] from one alert email body."""
    out, seen = [], set()
    alert = ""
    buf = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if DASHES.match(line) or URL_ONLY.match(line) or INLINE_LINK_LABEL.match(line):
            buf = []
            continue
        m = ALERT_HEAD.match(line)
        if m:
            alert = m.group(1).strip()
            buf = []
            continue
        m = VIEW_JOB.match(line)
        if m:
            jid = linkedin_job_id(m.group(1))
            facts = [b for b in buf if not BADGE.match(b)]
            comp = next((b for b in facts if SALARY.match(b)), "")
            facts = [b for b in facts if b != comp]
            buf = []
            if jid and jid not in seen and len(facts) >= 2:
                seen.add(jid)
                out.append({"title": facts[0], "company": facts[1], "location": facts[2] if len(facts) > 2 else "",
                            "comp": comp, "job_id": jid, "alert": alert or subject, "email_id": email_id})
            continue
        buf.append(line)
    return out


BUILTIN_LINK = re.compile(r"\[\]\((\S+?)\)")
BUILTIN_JOB = re.compile(r"builtin\.com/job/([a-z0-9-]+)/(\d+)", re.I)


def parse_builtin(text, email_id="", subject=""):
    """Built In job-preference digests: one table row per role, 'Company Title Workplace Location Pay [](link)'.
    The link carries the role slug and id, which splits the company from the title reliably."""
    out, seen = [], set()
    alert = ""
    for raw in (text or "").splitlines():
        line = raw.strip().strip("|").strip()
        if line.lower().startswith("job preferences"):
            alert = line[len("job preferences"):].split("[](")[0].strip()
            continue
        links = BUILTIN_LINK.findall(line)
        m = next((BUILTIN_JOB.search(unquote(u)) for u in links if BUILTIN_JOB.search(unquote(u))), None)
        if not m:
            continue
        slug, rid = m.group(1).lower(), m.group(2)
        if rid in seen:
            continue
        words = line.split("[](")[0].split()
        norm = [re.sub(r"[^a-z0-9]", "", w.lower()) for w in words]
        sw = [w for w in slug.split("-") if w]
        idx = None
        for i in range(len(words)):
            seq = [n for n in norm[i:] if n][:len(sw)]
            if seq == sw:
                idx = i
                break
        if idx is None or idx == 0:
            continue
        j, n = idx, 0
        while n < len(sw) and j < len(words):
            n += 1 if norm[j] else 0
            j += 1
        rest = words[j:]
        pay = next((i for i, w in enumerate(rest) if w.startswith("$")), None)
        comp = " ".join(rest[pay:]) if pay is not None else ""
        where = " ".join(rest[:pay] if pay is not None else rest)
        seen.add(rid)
        out.append({"title": " ".join(words[idx:j]), "company": " ".join(words[:idx]), "location": where, "comp": comp,
                    "job_id": "", "url": "https://builtin.com/job/%s/%s" % (slug, rid), "source": "built in",
                    "alert": alert or subject, "email_id": email_id})
    return out


DICE_JOB = re.compile(r"<(https://www\.dice\.com/job-detail/([0-9a-f-]{36}))")


def parse_dice(text, email_id="", subject=""):
    """Dice alerts: a title block, then the job link, then company, location and 'Posted:' blocks."""
    blocks, cur = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            if cur:
                blocks.append(cur)
            cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)
    out, seen = [], set()
    for bi, blk in enumerate(blocks):
        joined = "\n".join(blk)
        m = DICE_JOB.search(joined)
        if not m or m.group(2) in seen:
            continue
        before = joined[:m.start()].replace("\n", " ").strip()
        title = before
        if not title and bi > 0 and not blocks[bi - 1][0].startswith("<"):
            title = " ".join(blocks[bi - 1])
        nxt = [b for b in blocks[bi + 1:bi + 4] if not b[0].startswith("<")]
        company = nxt[0][0] if nxt else ""
        location = nxt[1][0] if len(nxt) > 1 and not nxt[1][0].startswith("Posted:") else ""
        if not (title and company):
            continue
        seen.add(m.group(2))
        out.append({"title": title, "company": company, "location": location, "comp": "", "job_id": "",
                    "url": "https://www.dice.com/job-detail/" + m.group(2), "source": "dice",
                    "alert": subject, "email_id": email_id})
    return out


def parse_email(text, email_id="", subject=""):
    """Pick the reader that fits the email body."""
    if "dice.com/job-detail/" in (text or ""):
        return parse_dice(text, email_id, subject)
    if "builtin.com" in (text or "") and BUILTIN_JOB.search(unquote(text or "")):
        return parse_builtin(text, email_id, subject)
    return parse_linkedin(text, email_id, subject)


def triage(title):
    """('main'|'stretch', reason). Title-only, so it is a sort order, not a judgment of fit."""
    t = title or ""
    if JUNIOR.search(t):
        return "stretch", "Entry-level or associate title. Kept in case the scope is bigger than the title."
    if CLEARANCE.search(t):
        return "stretch", "The title mentions a security clearance or classified work. Check you qualify before you apply."
    if AGENCY.search(t):
        return "stretch", "Looks like a staffing-agency post, not the employer's own listing. Find the real posting first."
    if LEADERSHIP.search(t):
        return "stretch", "Management or executive title, not the individual-contributor solutions work you target."
    if TARGET.search(t):
        return "main", ""
    return "stretch", "The title does not name your target work (solutions, forward deployed, applied AI). Read the posting before you spend an application."


def default_source_label(item):
    """'Dice job alert', 'Built In job alert'. LinkedIn when the role has a LinkedIn job id or names no source."""
    src = (item.get("source") or "").strip()
    if item.get("job_id") or not src:
        return "LinkedIn job alert"
    return "%s job alert" % src.title()


def role_record(item, source_label=None):
    jid = item.get("job_id") or ""
    url = item.get("url") or (job_link(jid) if jid else "")
    lane, reason = triage(item.get("title"))
    rec = {
        "company": (item.get("company") or "").strip(),
        "role": (item.get("title") or "").strip(),
        "location": (item.get("location") or "").strip(),
        "status": "To apply",
        "tier": "C",
        "fit_score": 65 if lane == "main" else 40,
        "apply_url": url, "posting_url": url,
        "ats": "LinkedIn" if jid else (item.get("source") or "").title(),
        "source": source_label or default_source_label(item), "source_group": "Job alerts",
        "alert_name": item.get("alert") or "", "alert_email_id": item.get("email_id") or "",
        "triage": "auto-title",
        "fit_notes": "Added from a job alert and triaged by title only. Not scored: read the posting before you spend an application on it.",
    }
    if item.get("comp"):
        rec["comp"] = item["comp"]
    if jid:
        rec["linkedin_job_id"] = jid
    if lane == "stretch":
        rec["stretch"] = True
        rec["stretch_reason"] = reason
    return rec


def tracked_index(jobs):
    ids = {}
    for j in jobs:
        jid = j.get("linkedin_job_id") or linkedin_job_id(j.get("apply_url"), j.get("posting_url"))
        if jid:
            ids[jid] = j.get("ref")
    return ids


def plan(items, jobs, source_label=None):
    """Split alert items into roles to add and duplicates already tracked. Pure: touches no database."""
    ids = tracked_index(jobs)
    pool = [dict(j) for j in jobs]
    add, dups = [], []
    for it in items:
        if not (it.get("company") and it.get("title")):
            continue
        rec = role_record(it, source_label)
        jid = rec.get("linkedin_job_id", "")
        hit = ids.get(jid) if jid else None
        if not hit:
            # A LinkedIn job id is a posting. Two postings with the same title (one per city) are two roles
            # and both appear. A title match only counts as "already have it" when the tracked role is one you
            # applied to or closed, has the same apply URL, or came from a source with no LinkedIn id to compare.
            near = [o for o in DUP.find_duplicates(rec, pool, require_later=False)
                    if "url" in o.get("_dup_via", "") or DUP.is_later(o)
                    or o.get("status") in ("Closed", "Withdrawn")
                    or (o.get("triage") != "auto-title"
                        and not (o.get("linkedin_job_id") or linkedin_job_id(o.get("apply_url"), o.get("posting_url"))))]
            hit = near[0].get("ref") if near else None
        if hit:
            dups.append({"company": rec["company"], "role": rec["role"], "tracked_as": hit, "job_id": jid})
            continue
        add.append(rec)
        pool.append(dict(rec, ref="(batch-%d)" % len(pool)))   # later items in this batch dedupe against it
        if jid:
            ids[jid] = "(this batch)"
    return add, dups
