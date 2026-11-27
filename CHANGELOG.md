# Changelog

## 0.3.7 — 2026-11-26

- Queue order: new `queue_order` setting. `"manual"` (the default) keeps the stored order. `"newest_best"` ranks every role by fit score plus a freshness bonus on every read, with HOT roles always first, so a new strong role rises and a stale one sinks without hand re-ranking. Unscored roles use a tier stand-in; "posted" dates, LinkedIn relative ages ("2 weeks ago") and the day we found the role are all understood
- The job page shows the live queue number

## 0.3.6 — 2026-11-25

Three community fixes from @charles-hood, merged as PRs #1 to #3. Thank you.

- Config: on Python 3.9 and 3.10 the built-in config reader no longer splits quoted values on commas or cuts them at a `#` (for example `"Portland, OR"` stays one item)
- Alerts: roles read from Dice and Built In emails are labelled "Dice job alert" and "Built In job alert" instead of "LinkedIn job alert"; a `source` sent in the ingest request still wins
- README: the quick start `cd` matches the cloned folder name

## 0.3.5 — 2026-11-25

- The Apply queue and Dashboard no longer list companies you skipped (paused). They were one banner card each and pushed the first role off the screen. Apply-limit banners (for example 3/3 used) still show. A skipped company's roles still leave the queue, the header count still says how many are paused, and Resume company stays on that company's own job page

## 0.3.4 — 2026-11-25

- The space-and-punctuation-ignoring search pass now runs only when the search text itself has a space or punctuation, so a short word such as "neo" no longer matches across word boundaries

## 0.3.3 — 2026-11-25

- Jobs: the status, fit, source and tier dropdowns are no longer remembered between
  visits, so a filter left over from earlier (for example "To apply") can never
  hide a role you applied to. Only the search text and sort order are kept
- Search ignores spaces and punctuation as a second pass, so "neo 4j" and
  "neo-4j" find "Neo4j"

## 0.3.2 — 2026-11-25

- Job alerts now read Dice and Built In emails as well as LinkedIn. Send the email
  text to `POST /api/alerts/ingest`; the app picks the reader from the body
- Two postings with the same title and different links (Dice ids, Built In ids)
  both appear. A title match still means "already tracked" only for roles you
  applied to or closed, roles with the same link, or curated roles that have no
  alert id to compare

## 0.3.1 — 2026-11-25

- Job alerts: a LinkedIn job id is now authoritative. Two postings with the same
  title (one per city) are two roles and both appear; before, the
  company-plus-title rule folded them into one. A title match still counts as
  "already tracked" when the tracked role is one you applied to or closed, has
  the same apply URL, or came from a source with no LinkedIn id to compare

## 0.3.0 — 2026-11-25

Job alerts flow into the app, and a search that dead-ended after you applied is fixed.

- Job-alert intake: `POST /api/alerts/ingest` takes alert email text (LinkedIn
  digests) or already-parsed roles, dedupes against every tracked role in any
  status (LinkedIn job id, apply URL, or company plus near-identical title),
  triages by title into the apply queue or Stretch with a reason (clearance and
  staffing-agency titles go to Stretch), and records
  which emails it has read (`GET /api/alerts/seen`). `dry_run` previews. New
  `alert_seen` table; the old app can still open the database
- Search fix, Apply queue and Stretch: the queue lists only roles still to apply, and the
  search text is remembered between visits, so searching a company you had just
  applied to (for example "acme") showed "No matches" even though the role
  was tracked. Both queues now show an "Also tracked, not in the queue" card
  with every match that already left it (applied, closed, paused or in the other
  queue), each with its status and a link, plus an "Open all in Jobs" button
- Jobs: the status, fit, source and tier filters are remembered too. When they
  hide roles that match your search, a note now says how many and offers
  "Clear filters"
- The queue's no-match page now says which queue it searched

## 0.2.0 — 2026-11-25

Brings the repo up to the private Job Command Center 1.6.1 behavior so one
code base can run a real, already-populated database.

- Hot leads: `hot`, `due_at`, `due_label` fields, a HOT badge, sort-first in the
  queue, searchable, and a due callout on the job and queue cards
- Config: `db_file` (keep an existing database name), `seed_path = "none"`,
  `first_name`, `logo`, `favicon`, `static_overlay`, `default_resume`,
  `resume_note`, `timezone_label`; `index.html` is templated from config
- Answers table works with both `needs_review` and the older `fred_decides`
  column, and the API returns both names, so an existing database needs no
  migration and the old app can still open it for a rollback
- Progress page: the first stat tile now reads "Since <your start date>" instead of a fixed date
- Screenshots regenerated from the fictional example seed
- Tests no longer read a local `config.toml`; new tests for HOT, the legacy
  column and seed-off

## 0.1.0 — 2026-11-25

First public-shaped release of NixNet Job Command, generalized from the
private Job Command Center (v1.6.0) so anyone can run it locally.

- Apply queue, Stretch lane, custom answers, receipt links, company limits
- Progress graph with a daily goal, streak, best day, and a share card
- Who-to-contact cards, duplicate detection, search, and a local answer library
- Profile, proofs, fit rules and daily goal are config- and data-driven
- Example seed (Acme Cloud and friends) so the app runs on first boot
- `make setup`, `make run`, `make test`, plus systemd and launchd examples

