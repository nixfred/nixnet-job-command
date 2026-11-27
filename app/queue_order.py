#!/usr/bin/env python3
"""Queue ordering: newest and best first.

queue_order = "manual" (default) keeps the stored queue_rank order.
queue_order = "newest_best" ranks every job by a score, recomputed on every read, so a fresh strong role
rises on its own and a stale one sinks without anyone re-ranking by hand:

  score = fit (the fit_score, or a tier stand-in when unscored) + a freshness bonus from how new the role is.

HOT roles always stay first. Stdlib only.
"""
from __future__ import annotations

import datetime
import re

TIER_STANDIN = {"A": 85, "B": 75, "C": 65}
UNSCORED = 65
# (max age in days, bonus). Anything older than the last row gets the last bonus.
FRESHNESS = [(2, 15), (7, 10), (14, 5), (30, 0), (60, -5), (10 ** 6, -10)]
_UNITS = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}


def _date(s):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s or "")
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def posted_date(d):
    """Best guess at when the role went up: a posted date, a relative LinkedIn age, else when we found it."""
    p = (d.get("posted") or "").strip()
    iso = _date(p)
    if iso:
        return iso
    m = re.search(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", p, re.I)
    if m:
        asof = None
        a = re.search(r"as of\s+([A-Za-z]{3})\w*\s+(\d{1,2})", p)
        created = _date(d.get("created_at"))
        if a and created:
            try:
                asof = datetime.datetime.strptime("%s %s %d" % (a.group(1), a.group(2), created.year), "%b %d %Y").date()
            except ValueError:
                asof = None
        base = asof or created
        if base:
            return base - datetime.timedelta(days=int(m.group(1)) * _UNITS[m.group(2).lower()])
    return _date(d.get("created_at"))


def fit_base(d):
    f = d.get("fit_score")
    if isinstance(f, (int, float)):
        return float(f)
    return float(TIER_STANDIN.get((d.get("tier") or "").upper(), UNSCORED))


def freshness(d, today):
    when = posted_date(d)
    if when is None:
        return 0
    age = max(0, (today - when).days)
    for limit, bonus in FRESHNESS:
        if age <= limit:
            return bonus
    return FRESHNESS[-1][1]


def score(d, today):
    return fit_base(d) + freshness(d, today)


def sort_key(d, today, is_hot):
    when = posted_date(d) or datetime.date.min
    return (0 if is_hot(d) else 1, -score(d, today), -when.toordinal(), d.get("ref") or "")


def order(jobs, today, is_hot):
    """Sort in place, best first, then number queue_rank 1..n in that order."""
    jobs.sort(key=lambda d: sort_key(d, today, is_hot))
    for i, d in enumerate(jobs, 1):
        d["queue_rank"] = i
    return jobs
