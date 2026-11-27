#!/usr/bin/env python3
"""Newest-and-best queue ordering: pure functions, no server."""
import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import queue_order as QO

TODAY = datetime.date(2026, 11, 26)
hot = lambda d: bool(d.get("hot"))


def job(ref, fit=None, tier="", posted="", created="2026-11-26T10:00:00-05:00", **kw):
    d = {"ref": ref, "fit_score": fit, "tier": tier, "posted": posted, "created_at": created}
    d.update(kw)
    return d


class QueueOrderTests(unittest.TestCase):
    def test_posted_date_forms(self):
        self.assertEqual(QO.posted_date(job("a", posted="2026-10-08")), datetime.date(2026, 10, 8))
        self.assertEqual(QO.posted_date(job("a", posted="2026-10-08 · req 123")), datetime.date(2026, 10, 8))
        rel = job("a", posted="LinkedIn: 2 weeks ago (as of Nov 24)", created="2026-11-24T10:00:00-05:00")
        self.assertEqual(QO.posted_date(rel), datetime.date(2026, 11, 10))
        self.assertEqual(QO.posted_date(job("a", posted="", created="2026-11-20T09:00:00")), datetime.date(2026, 11, 20))
        self.assertEqual(QO.posted_date(job("a", posted="req 99", created="")), None)

    def test_fresh_strong_beats_stale_strong(self):
        fresh = job("J-2", fit=80, posted="2026-11-25")
        stale = job("J-1", fit=80, posted="2025-08-08")
        out = QO.order([stale, fresh], TODAY, hot)
        self.assertEqual([d["ref"] for d in out], ["J-2", "J-1"])

    def test_best_fit_beats_slightly_newer(self):
        best = job("J-1", fit=90, posted="2026-11-10")  # 90 + 5
        meh = job("J-2", fit=70, posted="2026-11-25")   # 70 + 15
        out = QO.order([meh, best], TODAY, hot)
        self.assertEqual(out[0]["ref"], "J-1")

    def test_unscored_new_role_surfaces(self):
        new = job("J-9", fit=None, tier="C")  # 65 + 15 = 80
        old = job("J-3", fit=72, posted="2026-09-01")  # 72 - 5
        out = QO.order([old, new], TODAY, hot)
        self.assertEqual(out[0]["ref"], "J-9")

    def test_hot_stays_first_and_ranks_are_numbered(self):
        h = job("J-5", fit=40, posted="2025-01-01", hot=True)
        top = job("J-6", fit=95, posted="2026-11-26")
        out = QO.order([top, h], TODAY, hot)
        self.assertEqual([d["ref"] for d in out], ["J-5", "J-6"])
        self.assertEqual([d["queue_rank"] for d in out], [1, 2])

    def test_ties_go_newest_first(self):
        a = job("J-1", fit=80, posted="2026-11-20")
        b = job("J-2", fit=80, posted="2026-11-22")
        out = QO.order([a, b], TODAY, hot)
        self.assertEqual(out[0]["ref"], "J-2")


if __name__ == "__main__":
    unittest.main()
