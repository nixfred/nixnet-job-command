#!/usr/bin/env python3
"""Boot the server on a temp database and hit /api/health, /api/jobs, /api/progress."""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "app"
sys.path.insert(0, str(APP))


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="njc-test-")
        data = Path(cls.tmp.name) / "data"
        data.mkdir()
        cls.port = _free_port()
        os.environ["NJC_ROOT"] = str(REPO)
        # Hermetic: never pick up a local config.toml with real settings.
        os.environ["NJC_CONFIG"] = str(REPO / "config.example.toml")
        os.environ["NJC_DB_FILE"] = "njc.db"
        os.environ["NJC_DATA"] = str(data)
        os.environ["NJC_PORT"] = str(cls.port)
        os.environ["NJC_BIND"] = "127.0.0.1"
        os.environ["NJC_PROFILE"] = str(REPO / "profile.example.json")
        os.environ["NJC_SEED"] = str(REPO / "seed" / "example_seed.json")
        os.environ["NJC_TIMEZONE"] = "local"
        import config
        config.CFG.reload()
        import server
        server.apply_runtime()
        server.init()
        cls.server_mod = server
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", cls.port), server.H)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.port
        # Wait until health answers.
        deadline = time.time() + 8
        last = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(cls.base + "/api/health", timeout=1) as r:
                    if r.status == 200:
                        return
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = e
                time.sleep(0.1)
        raise RuntimeError("server did not start: %s" % last)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.httpd.shutdown()
        except Exception:
            pass
        cls.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            body = r.read()
            ctype = r.headers.get("Content-Type", "")
            if "json" in ctype:
                return r.status, json.loads(body)
            return r.status, body

    def test_health(self):
        status, data = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertIn("version", data)
        self.assertGreaterEqual(data.get("jobs", 0), 1)
        self.assertEqual(data.get("app_name"), "NixNet Job Command")

    def test_jobs(self):
        status, data = self.get("/api/jobs")
        self.assertEqual(status, 200)
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 8)
        refs = {j.get("ref") for j in data}
        self.assertIn("J-001", refs)
        acme = next(j for j in data if j["ref"] == "J-001")
        self.assertEqual(acme.get("company"), "Acme Cloud")
        self.assertNotIn("jd", acme)  # light list omits the job description

    def test_progress(self):
        status, data = self.get("/api/progress")
        self.assertEqual(status, 200)
        self.assertIn("days", data)
        self.assertIn("goal", data)
        self.assertIn("start_date", data)
        self.assertIsInstance(data["days"], list)
        self.assertGreaterEqual(len(data["days"]), 1)
        self.assertGreaterEqual(data.get("total", 0), 1)

    def test_index(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"NixNet Job Command", body)


    def _send(self, method, path, body=None):
        req = urllib.request.Request(
            self.base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")

    def test_hot_leads_sort_first_and_are_searchable(self):
        self._send("PATCH", "/api/jobs/J-003", {"hot": True, "due_label": "Friday noon"})
        try:
            _, jobs = self.get("/api/jobs")
            self.assertEqual(jobs[0]["ref"], "J-003")
            self.assertTrue(self.server_mod.is_hot(jobs[0]))
        finally:
            self._send("PATCH", "/api/jobs/J-003", {"hot": False, "due_label": ""})

    def test_answers_flag_has_both_names(self):
        _, made = self._send("POST", "/api/answers", {"question": "q", "answer": "a", "fred_decides": True})
        _, rows = self.get("/api/answers")
        row = next(r for r in rows if r["id"] == made["id"])
        self.assertEqual(row["needs_review"], 1)
        self.assertEqual(row["fred_decides"], 1)

    def test_legacy_fred_decides_column_is_detected(self):
        import sqlite3
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE answers(id TEXT, fred_decides INTEGER)")
        self.assertEqual(self.server_mod.detect_answers_flag(c), "fred_decides")
        c.execute("DROP TABLE answers")
        c.execute("CREATE TABLE answers(id TEXT, needs_review INTEGER)")
        self.assertEqual(self.server_mod.detect_answers_flag(c), "needs_review")
        # restore for the live test database
        real = sqlite3.connect(self.server_mod.DB)
        self.server_mod.detect_answers_flag(real)
        real.close()

    def test_dashboard_stats_use_the_configured_timezone_date(self):
        # The Dashboard must count "today" the way Progress does: in the configured timezone,
        # not by the host clock. Pretend the configured zone is already on a later date.
        import config, datetime
        later = datetime.date.today() + datetime.timedelta(days=3)
        _, before = self.get("/api/jobs/J-002")
        config.CFG.today = lambda: later
        try:
            self._send("PATCH", "/api/jobs/J-002", {"date_applied": later.isoformat()})
            _, st = self.get("/api/stats")
            _, pr = self.get("/api/progress")
            self.assertEqual(st["today"], later.isoformat())
            self.assertEqual(st["today"], pr["today"])
            self.assertEqual(st["applied_today"], pr["today_count"])
            self.assertGreaterEqual(st["applied_today"], 1)
            self.assertGreaterEqual(st["streak"], 1)
        finally:
            del config.CFG.today
            self._send("PATCH", "/api/jobs/J-002", {"date_applied": before.get("date_applied") or ""})


    def test_seed_can_be_turned_off(self):
        import config
        os.environ["NJC_SEED"] = "none"
        try:
            config.CFG.reload()
            self.assertIsNone(config.CFG.seed_path)
        finally:
            os.environ["NJC_SEED"] = str(REPO / "seed" / "example_seed.json")
            config.CFG.reload()


    def test_toml_fallback_keeps_commas_and_hashes_inside_quotes(self):
        # Python 3.9 and 3.10 have no tomllib, so config.toml goes through the fallback reader.
        import config
        got = config._parse_toml_fallback(
            'name = "Acme #1"   # trailing comment\n'
            'port = 8742 # comment\n'
            '[fit]\n'
            'locations = ["Remote US", "Portland, OR", \'Austin, TX\']  # comment, with a comma\n'
            'kinds = ["SE", "SA",]\n'
            'empty = []\n')
        self.assertEqual(got["name"], "Acme #1")
        self.assertEqual(got["port"], 8742)
        self.assertEqual(got["fit"]["locations"], ["Remote US", "Portland, OR", "Austin, TX"])
        self.assertEqual(got["fit"]["kinds"], ["SE", "SA"])
        self.assertEqual(got["fit"]["empty"], [])
        # The shipped example reads the same with or without tomllib.
        example = (REPO / "config.example.toml").read_text(encoding="utf-8")
        self.assertEqual(config._parse_toml_fallback(example)["fit"]["locations"], ["Remote US", "Portland, OR"])


    # ---- job-alert intake ----
    ALERT = """Your job alert for Solutions Engineer in United States
Forward Deployed Engineer
Initrode Labs
Austin, TX

This company is actively hiring
View job: https://www.linkedin.com/comm/jobs/view/9000000001/?trackingId=abc%3D%3D&refId=xyz&trk=eml-1

---------------------------------------------------------

Senior Accountant
Globex Foods
Chicago, IL
Fast growing
$80K/yr - $95K/yr
View job: https://www.linkedin.com/comm/jobs/view/9000000002/?trackingId=def

---------------------------------------------------------

Director of Solutions Engineering
Initrode Labs
Remote
1 connection
View job: https://www.linkedin.com/comm/jobs/view/9000000003/?trackingId=ghi

---------------------------------------------------------

See all jobs on LinkedIn: https://www.linkedin.com/comm/jobs/search-results/?keywords=x
Manage your job alerts: https://www.linkedin.com/comm/jobs/alerts
"""
    FACET = """Expand your search

Remote jobs
https://www.linkedin.com/comm/jobs/search-results/?keywords=Senior+Solutions+Architect

Senior Solutions Architect - Presales
Umbrella Storage
United States
View job: https://www.linkedin.com/comm/jobs/view/9000000004/?trackingId=jkl
"""

    def _ingest(self, body):
        return self._send("POST", "/api/alerts/ingest", body)[1]

    def test_alert_parser_reads_blocks_badges_and_headings(self):
        AL = __import__("alerts")
        got = AL.parse_linkedin(self.ALERT, "e1")
        self.assertEqual([g["job_id"] for g in got], ["9000000001", "9000000002", "9000000003"])
        self.assertEqual((got[0]["title"], got[0]["company"], got[0]["location"]), ("Forward Deployed Engineer", "Initrode Labs", "Austin, TX"))
        self.assertEqual(got[0]["alert"], "Solutions Engineer in United States")
        self.assertEqual(got[1]["comp"], "$80K/yr - $95K/yr")
        self.assertEqual(got[2]["location"], "Remote")          # "1 connection" badge is not a location
        f = AL.parse_linkedin(self.FACET, "e2")
        self.assertEqual([(g["title"], g["company"]) for g in f], [("Senior Solutions Architect - Presales", "Umbrella Storage")])

    def test_alert_ingest_triages_dedupes_and_is_idempotent(self):
        dry = self._ingest({"emails": [{"id": "t-e1", "text": self.ALERT}], "dry_run": True})
        self.assertTrue(dry["dry_run"]); self.assertEqual(len(dry["added"]), 3)
        _, before = self.get("/api/jobs")
        self.assertFalse(any(j.get("company") == "Initrode Labs" for j in before))   # dry run wrote nothing
        r = self._ingest({"emails": [{"id": "t-e1", "text": self.ALERT}]})
        lanes = {a["role"]: a["lane"] for a in r["added"]}
        self.assertEqual(lanes["Forward Deployed Engineer"], "main")
        self.assertEqual(lanes["Senior Accountant"], "stretch")
        self.assertEqual(lanes["Director of Solutions Engineering"], "stretch")
        _, jobs = self.get("/api/jobs")
        fde = next(j for j in jobs if j["role"] == "Forward Deployed Engineer" and j["company"] == "Initrode Labs")
        self.assertEqual((fde["status"], fde["source"], fde["apply_url"]), ("To apply", "LinkedIn job alert", "https://www.linkedin.com/jobs/view/9000000001/"))
        self.assertTrue(next(j for j in jobs if j["role"] == "Senior Accountant")["stretch"])
        # Same email again: skipped. Same roles in a new email: duplicates, nothing added.
        again = self._ingest({"emails": [{"id": "t-e1", "text": self.ALERT}]})
        self.assertEqual((again["emails_read"], again["emails_already_read"], again["added"]), (0, ["t-e1"], []))
        twice = self._ingest({"emails": [{"id": "t-e1b", "text": self.ALERT}]})
        self.assertEqual((twice["added"], len(twice["duplicates"])), ([], 3))
        self.assertIn("t-e1", self.get("/api/alerts/seen")[1]["email_ids"])

    def test_alert_items_dedupe_against_existing_roles_by_company_and_title(self):
        _, jobs = self.get("/api/jobs")
        have = jobs[0]
        r = self._ingest({"items": [{"title": have["role"], "company": have["company"], "location": "x", "job_id": "9000000099"},
                                    {"title": "Solutions Engineer", "company": "Hooli Test Co", "location": "Remote", "source": "dice", "url": "https://www.dice.com/job-detail/zzz"}],
                          "dry_run": True})
        self.assertEqual([d["tracked_as"] for d in r["duplicates"]], [have["ref"]])
        self.assertEqual([a["company"] for a in r["added"]], ["Hooli Test Co"])


    def test_alert_triage_lanes(self):
        AL = __import__("alerts")
        self.assertEqual(AL.triage("Senior Solutions Architect supporting Classified programs")[0], "stretch")
        self.assertEqual(AL.triage("Hiring for Clinical Domain Solutions & Presales")[0], "stretch")
        self.assertEqual(AL.triage("Associate Solutions Engineer")[0], "stretch")
        self.assertEqual(AL.triage("Forward Deployed Engineer")[0], "main")
        self.assertEqual(AL.triage("Sr. Solutions Architect, AI Infrastructure")[0], "main")


    def test_alert_same_title_different_postings_both_appear(self):
        AL = __import__("alerts")
        jobs = [{"ref": "J-900", "company": "Acme Cloud", "role": "Solutions Architect", "status": "Applied", "date_applied": "2026-11-20"},
                {"ref": "J-901", "company": "Curated Co", "role": "Field Engineer", "status": "To apply", "apply_url": "https://jobs.example.com/curated/1"},
                {"ref": "J-902", "company": "Roads Inc", "role": "Solutions Engineer", "status": "To apply", "linkedin_job_id": "111"}]
        items = [{"title": "Forward Deployed Engineer", "company": "Fonzi Test", "location": "New York", "job_id": "201"},
                 {"title": "Forward Deployed Engineer", "company": "Fonzi Test", "location": "Austin", "job_id": "202"},
                 {"title": "Solutions Architect", "company": "Acme Cloud", "location": "Remote", "job_id": "203"},     # already applied
                 {"title": "Field Engineer", "company": "Curated Co", "location": "Remote", "job_id": "204"},         # curated role, no LinkedIn id to compare
                 {"title": "Solutions Engineer", "company": "Roads Inc", "location": "Denver", "job_id": "112"}]      # another posting of an open alert role
        add, dups = AL.plan(items, jobs)
        self.assertEqual(sorted(a["linkedin_job_id"] for a in add), ["112", "201", "202"])
        self.assertEqual(sorted(d["tracked_as"] for d in dups), ["J-900", "J-901"])


    def test_builtin_digest_parser(self):
        AL = __import__("alerts")
        body = ("Job Preferences Technical Account Manager, Atlanta, Georgia, USA [](https://t.example/L0/https:%2F%2Fbuiltin.com%2Fprofile%2Fjob-preferences%3Fi=1/1/x)\n"
                "| Initech Technical Account Manager In Office Atlanta, GA [](https://t.example/L0/https:%2F%2Fbuiltin.com%2Fjob%2Ftechnical-account-manager%2F111%3Fi=1/1/x) |\n"
                "| Red Brick Co Staff Technical Account Manager Remote United States $116,960-$171,900 [](https://t.example/L0/https:%2F%2Fbuiltin.com%2Fjob%2Fstaff-technical-account-manager%2F222%3Fi=1/1/x) |\n"
                "| Get More Recommendations [](https://t.example/L0/https:%2F%2Fbuiltin.com%2Fjobs%3Fi=1/1/x) |\n")
        got = AL.parse_email(body, "b1")
        self.assertEqual([(g["company"], g["title"], g["location"], g["comp"]) for g in got],
                         [("Initech", "Technical Account Manager", "In Office Atlanta, GA", ""),
                          ("Red Brick Co", "Staff Technical Account Manager", "Remote United States", "$116,960-$171,900")])
        self.assertEqual(got[0]["url"], "https://builtin.com/job/technical-account-manager/111")
        add, _ = AL.plan(got, [])
        self.assertEqual([a["apply_url"] for a in add], [g["url"] for g in got])
        self.assertEqual({a["source"] for a in add}, {"Built In job alert"})


    def test_dice_alert_parser_and_two_postings_same_title(self):
        AL = __import__("alerts")
        body = """Forward Deployed Engineer at Globex Test

Hi, Fred.

New matches for your presales job alert:

 <https://www.dice.com/company/globex>

Forward Deployed Engineer
<https://www.dice.com/job-detail/8a16acd7-4429-44b7-87c2-5a3d335dcf31>

Globex Test

Arlington, Virginia, USA

Posted: 11-20-2026

Forward Deployed Engineer
<https://www.dice.com/job-detail/4cf1c085-95cf-4477-b3eb-fef3947e80bc>

Globex Test

Arlington, Virginia, USA

Posted: 10-27-2026

Hiring for Clinical Solutions Lead at Raritan, New Jersey -
Hybrid <https://www.dice.com/job-detail/3a86d685-f2ea-4a17-ac94-4f095841709e>

Staffing Co LLC.

Raritan, New Jersey, USA

Posted: 11-11-2026

Manage your daily job alert > <https://www.dice.com/dashboard/alerts>
"""
        got = AL.parse_email(body, "d1", "Results")
        self.assertEqual([(g["company"], g["title"], g["location"]) for g in got],
                         [("Globex Test", "Forward Deployed Engineer", "Arlington, Virginia, USA"),
                          ("Globex Test", "Forward Deployed Engineer", "Arlington, Virginia, USA"),
                          ("Staffing Co LLC.", "Hiring for Clinical Solutions Lead at Raritan, New Jersey - Hybrid", "Raritan, New Jersey, USA")])
        add, dups = AL.plan(got, [])
        self.assertEqual((len(add), len(dups)), (3, 0))                    # both same-title postings appear
        self.assertEqual(add[0]["ats"], "Dice")
        self.assertEqual(add[0]["source"], "Dice job alert")                 # not "LinkedIn job alert"
        self.assertEqual(AL.plan(got, [], "My feed")[0][0]["source"], "My feed")   # a caller's label still wins
        self.assertTrue(add[2]["stretch"])                                 # agency post goes to Stretch
        again, d2 = AL.plan(got, [dict(a, ref="J-%d" % i) for i, a in enumerate(add)])
        self.assertEqual((len(again), len(d2)), (0, 3))                    # same URLs are already tracked


if __name__ == "__main__":
    unittest.main(verbosity=2)
