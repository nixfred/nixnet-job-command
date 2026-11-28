#!/usr/bin/env python3
"""Profile-driven custom answers for NixNet Job Command.

generate(job) returns a list of {id, q, a} items: why here, why company,
why role, relevant experience, a proud project, plus open-ended form
questions found on the job.

All first-person facts come from profile.json (see profile.example.json).
Nothing in this module is a real person's resume.
"""
from __future__ import annotations

import re
import sys

try:
    from config import CFG
except ImportError:
    CFG = None

EMDASH = "\u2014"
BAD_MARK = re.compile(r"[\[\]{}]|<placeholder>|TODO|TBD|FIXME", re.I)

SKIP_FIELD = re.compile(
    r"^(first name|last name|preferred( first)? name|name|email|phone|address|city|state|zip|country|linkedin|github|website|portfolio|resume|cv|"
    r"pronouns?|gender|race|ethnic|hispanic|veteran|disability|salary|compensation|sponsor|authori[sz]ed|start date|notice|"
    r"clearance|location|how did you hear|referr|legal name|postal|privacy|certify|acknowled|arbitration|over the age|18 years)$",
    re.I,
)

CORE_IDS = ("why_here", "why_company", "why_role", "experience", "proud_project")


def profile():
    if CFG is not None:
        return CFG.profile or {}
    return {}


def company_name(job):
    return (job.get("company") or "this company").split(" (")[0].strip()


def role_name(job):
    return (job.get("role") or "this role").strip()


def words(s):
    return [w for w in re.split(r"\s+", (s or "").strip()) if w]


def word_count(s):
    return len(words(s))


def clean(s):
    s = (s or "").replace(EMDASH, ", ").replace("\u2013", "-")
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r" ,", ",", s)
    s = re.sub(r"\s+\.", ".", s)
    return s


def sentences(s):
    s = clean(s)
    if not s:
        return []
    parts = re.split(r"(?<=[.!?])\s+", s)
    return [p.strip() for p in parts if p.strip()]


def join_sents(parts):
    out = []
    for p in parts:
        p = clean(p)
        if not p:
            continue
        if p[-1] not in ".!?":
            p += "."
        out.append(p)
    return " ".join(out)


def fit_words(text, lo=80, hi=150, extras=None):
    extras = list(extras or [])
    text = clean(text)
    sents = sentences(text)
    text = join_sents(sents)
    n = word_count(text)
    i = 0
    while n < lo and i < len(extras):
        add = clean(extras[i]); i += 1
        if not add:
            continue
        if add[-1] not in ".!?":
            add += "."
        if add[:-1].lower() in text.lower():
            continue
        cand = clean(text + " " + add)
        if word_count(cand) <= hi:
            text = cand
            n = word_count(text)
    sents = sentences(text)
    while word_count(text) > hi and len(sents) > 2:
        sents = sents[:-1]
        text = join_sents(sents)
    if word_count(text) > hi:
        w = words(text)[:hi]
        text = " ".join(w)
        if text[-1] not in ".!?":
            text += "."
    return clean(text)


def _map(key, default=None):
    p = profile()
    val = p.get(key, default)
    return val if val is not None else (default or {})


def hook_for(job):
    hooks = _map("company_hooks", {})
    co = job.get("company") or ""
    if co in hooks:
        return hooks[co]
    short = company_name(job)
    for k, v in hooks.items():
        if k.split(" (")[0] == short:
            return v
    return ""


def product_line(job):
    uses = _map("product_use", {})
    co = job.get("company") or ""
    if co in uses:
        return uses[co]
    return uses.get(company_name(job), "")


def proof(name):
    proofs = _map("proofs", {})
    return proofs.get(name) or ""


def pick_proof_ids(job):
    kind = (job.get("kind") or "").lower()
    cat = (job.get("category") or "").lower()
    by_kind = _map("proofs_by_kind", {})
    by_cat = _map("proofs_by_category", {})
    ids = by_kind.get(kind) or by_cat.get(cat) or _map("default_proofs", [])
    if isinstance(ids, str):
        ids = [ids]
    if not ids:
        ids = list(_map("proofs", {}).keys())[:3]
    return [i for i in ids if proof(i)]


def proofs_text(job, limit=2):
    out = []
    for pid in pick_proof_ids(job)[:limit]:
        t = proof(pid)
        if t:
            out.append(t)
    return out


def skills():
    s = profile().get("skills") or []
    if isinstance(s, str):
        return [x.strip() for x in s.split(",") if x.strip()]
    return list(s)


def jd_themes(job, limit=3):
    """Profile skills the posting text names. A role with no posting text has no themes: fit_notes are
    the tracker's own notes, not the posting's words, and must not be quoted as what the posting says."""
    jd = job.get("jd") or ""
    if not jd:
        return []
    text = re.sub(r"(?s)<[^>]+>", " ", jd)
    found = []
    low = text.lower()
    for sk in skills():
        if sk.lower() in low and sk not in found:
            found.append(sk)
        if len(found) >= 6:
            break
    return found[:limit] if found else []


def role_hook(job):
    hooks = _map("role_hooks", {})
    kind = (job.get("kind") or "").lower()
    return hooks.get(kind) or hooks.get("default") or ""


def location_line():
    p = profile()
    loc = p.get("location") or ""
    extra = p.get("location_note") or ""
    if loc and extra:
        return "I am based in %s. %s" % (loc, extra)
    if loc:
        return "I am based in %s." % loc
    return extra


def why_looking(_job=None):
    return clean(profile().get("why_looking") or "I am looking for the next role where I can help customers put this kind of platform to work.")


def theme_clause(job):
    themes = jd_themes(job)
    if not themes:
        return ""
    if len(themes) == 1:
        return "The posting leans on %s, which matches the work I already do." % themes[0]
    return "The posting leans on %s and %s, which matches the work I already do." % (", ".join(themes[:-1]), themes[-1])


def why_company(job):
    co = company_name(job)
    hook = hook_for(job)
    parts = []
    if hook:
        parts.append(hook)
    else:
        parts.append(
            "I want to work at %s because the product sits in a space I already care about, and the role is a chance to help customers get real value from it."
            % co
        )
    parts.append(role_hook(job))
    parts.extend(proofs_text(job, 1))
    parts.append(location_line())
    return fit_words(join_sents(parts), extras=proofs_text(job, 3) + [why_looking()])


def why_role(job):
    role = role_name(job)
    co = company_name(job)
    parts = [
        "The %s role at %s is the kind of customer-facing work I want to do next." % (role, co),
        role_hook(job),
        theme_clause(job),
    ]
    parts.extend(proofs_text(job, 2))
    parts.append(location_line())
    return fit_words(join_sents(parts), extras=proofs_text(job, 3))


def why_here(job):
    co = company_name(job)
    role = role_name(job)
    hook = hook_for(job)
    parts = []
    if hook:
        parts.append(hook)
    else:
        parts.append("I want to work at %s, in the %s role, because it combines a product I respect with customer-facing work I already know how to do." % (co, role))
    parts.append(role_hook(job))
    parts.append(theme_clause(job))
    parts.extend(proofs_text(job, 1))
    parts.append(location_line())
    return fit_words(join_sents(parts), extras=proofs_text(job, 3) + [why_looking()])


def relevant_experience(job):
    parts = [
        "I bring hands-on, customer-facing experience that maps to the %s role at %s." % (role_name(job), company_name(job)),
        theme_clause(job),
    ]
    parts.extend(proofs_text(job, 2))
    parts.append(role_hook(job))
    return fit_words(join_sents(parts), extras=proofs_text(job, 3))


def proud_project(job):
    p = profile()
    proud = p.get("proud_project") or ""
    parts = [proud] if proud else proofs_text(job, 1)
    if not parts:
        parts = ["I am proud of work that takes a hard platform problem and turns it into something a customer can actually run."]
    parts.append(theme_clause(job))
    parts.extend(proofs_text(job, 2))
    return fit_words(join_sents(parts), extras=proofs_text(job, 3))


def cover_letter(job):
    p = profile()
    greet = p.get("cover_greeting") or "Hello,"
    close = p.get("cover_close") or "Thank you for your time. I would welcome a conversation."
    body = join_sents([
        "I am applying for the %s role at %s." % (role_name(job), company_name(job)),
        hook_for(job) or why_here(job),
        role_hook(job),
        theme_clause(job),
    ] + proofs_text(job, 1) + [location_line(), close])
    letter = "%s\n\n%s" % (greet, fit_words(body, lo=90, hi=170, extras=proofs_text(job, 3)))
    return clean(letter)


def additional(job):
    extra = profile().get("additional") or ""
    parts = [extra] if extra else [why_looking(), location_line()]
    parts.extend(proofs_text(job, 1))
    return fit_words(join_sents(parts), extras=proofs_text(job, 3))


def ai_use(job):
    text = profile().get("ai_use") or proof("ai_use") or proofs_text(job, 1)
    if isinstance(text, list):
        parts = text
    else:
        parts = [text]
    parts.append(theme_clause(job))
    return fit_words(join_sents(parts), extras=proofs_text(job, 3))


def shipped(job):
    text = profile().get("shipped") or proof("shipped") or proofs_text(job, 2)
    parts = text if isinstance(text, list) else [text]
    return fit_words(join_sents(parts), extras=proofs_text(job, 3))


def llm_agents(job):
    text = profile().get("llm_agents") or proof("agents") or proofs_text(job, 2)
    parts = text if isinstance(text, list) else [text]
    return fit_words(join_sents(parts), extras=proofs_text(job, 3))


def open_source(job):
    text = profile().get("open_source") or proof("open_source") or proofs_text(job, 2)
    parts = text if isinstance(text, list) else [text]
    return fit_words(join_sents(parts), extras=proofs_text(job, 3))


def teaching(job):
    text = profile().get("teaching") or proof("teaching") or proud_project(job)
    parts = text if isinstance(text, list) else [text]
    return fit_words(join_sents(parts), extras=proofs_text(job, 3))


def client_ai(job):
    text = profile().get("client_facing") or proof("customer") or proofs_text(job, 2)
    parts = text if isinstance(text, list) else [text]
    parts.append(theme_clause(job))
    return fit_words(join_sents(parts), extras=proofs_text(job, 3))


def remote(_job=None):
    text = profile().get("remote") or location_line() or "I have worked remotely with customers and teammates across time zones, and I keep written notes so the work stays clear."
    parts = text if isinstance(text, list) else [text]
    return fit_words(join_sents(parts), extras=proofs_text(_job, 2) + [why_looking()])


def used_product(job):
    line = product_line(job)
    parts = []
    if line:
        parts.append(line)
    else:
        parts.append(
            "I have not claimed hands-on daily use of every %s product in this posting, and I will not invent that experience. I learn products quickly and I already work in the same problem space."
            % company_name(job)
        )
    parts.extend(proofs_text(job, 1))
    return fit_words(join_sents(parts), extras=proofs_text(job, 3))


EXTRA_RULES = [
    (r"currently employed|why are you looking|why .*looking", why_looking),
    (r"cover letter", cover_letter),
    (r"additional information|anything else|other information", additional),
    (r"using ai today|how do you use ai|use of ai", ai_use),
    (r"written and shipped production software|production software", shipped),
    (r"llm-based agents|agentic systems|experience with ai or agentic", llm_agents),
    (r"open source", open_source),
    (r"teaching developers|best thing you.ve made|proud of", teaching),
    (r"client.facing|customer.facing", client_ai),
    (r"async|remote environment|remote work", remote),
    (r"used .* product|hands-on experience with|have you used", used_product),
    (r"tell us about (a |your )?project|project you are proud", proud_project),
    (r"relevant experience|why are you a good fit|why you.d be a good", relevant_experience),
]


def _norm_q(q):
    return re.sub(r"[^a-z0-9]+", " ", (q or "").lower()).strip()


def is_skip_field(label):
    lab = (label or "").strip()
    if not lab:
        return True
    if SKIP_FIELD.match(lab):
        return True
    if len(lab) < 8 and not re.search(r"why|tell|describ|experienc|project|letter", lab, re.I):
        return True
    return False


def extra_composer(label):
    lab = (label or "").strip()
    if is_skip_field(lab):
        return None
    if re.search(r"why .*(interested|want|would you like|work (at|with|here))|^why this (company|role)|^why .+\?$", lab, re.I):
        if re.search(r"looking|employed", lab, re.I):
            return why_looking
        return None
    for rx, fn in EXTRA_RULES:
        if re.search(rx, lab, re.I):
            return fn
    if re.search(r"tell us|describe|explain|write|essay|statement|what is your|how (have|do|would|will)|share (an? |your )", lab, re.I):
        return relevant_experience
    return None


def collect_extra_questions(job):
    seen = set()
    out = []

    def add(label):
        lab = (label or "").strip()
        key = _norm_q(lab)
        if not lab or key in seen:
            return
        fn = extra_composer(lab)
        if not fn:
            return
        seen.add(key)
        out.append((lab, fn))

    for q in job.get("questions") or []:
        if isinstance(q, dict):
            add(q.get("label") or q.get("q") or "")
    for a in job.get("answers") or []:
        if isinstance(a, dict):
            add(a.get("q") or a.get("question") or "")
    return out


def generate(job):
    """Return a list of {id,q,a} custom answers for this job."""
    co, role = company_name(job), role_name(job)
    items = [
        {"id": "why_here", "q": "Why do you want to work here / in this role?", "a": why_here(job)},
        {"id": "why_company", "q": "Why do you want to work at %s?" % co, "a": why_company(job)},
        {"id": "why_role", "q": "Why do you want the %s role?" % role, "a": why_role(job)},
        {"id": "experience", "q": "What relevant experience do you bring to this role?", "a": relevant_experience(job)},
        {"id": "proud_project", "q": "Tell us about a project you are proud of.", "a": proud_project(job)},
    ]
    used_fn = {why_here, why_company, why_role, relevant_experience, proud_project}
    for i, (label, fn) in enumerate(collect_extra_questions(job)):
        if fn in used_fn and fn is not why_looking and fn is not cover_letter and fn is not additional and fn is not used_product:
            continue
        items.append({"id": "extra_%d" % (i + 1), "q": label, "a": fn(job)})
        used_fn.add(fn)
    for it in items:
        it["a"] = clean(it["a"])
        if EMDASH in it["a"] or BAD_MARK.search(it["a"]):
            it["a"] = clean(it["a"].replace(EMDASH, ", "))
            it["a"] = BAD_MARK.sub("", it["a"])
            it["a"] = clean(it["a"])
        # Surface a review flag when the profile left a fact thin.
        if word_count(it["a"]) < 40:
            it["needs_review"] = True
            it["note"] = "Short answer: add more proofs or a company hook to your profile."
    return items


def validate(items):
    problems = []
    for it in items:
        n = word_count(it.get("a") or "")
        if n < 80 or n > 150:
            problems.append((it.get("id"), "words", n, it.get("q")))
        if EMDASH in (it.get("a") or ""):
            problems.append((it.get("id"), "emdash", n, it.get("q")))
        if BAD_MARK.search(it.get("a") or ""):
            problems.append((it.get("id"), "placeholder", n, it.get("q")))
    return problems


if __name__ == "__main__":
    import json
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    if not path:
        sys.stderr.write("usage: custom_answers.py jobs.json [out.json]\n")
        sys.exit(2)
    payload = json.load(open(path))
    jobs = payload["jobs"] if isinstance(payload, dict) and "jobs" in payload else payload
    out = {j["ref"]: generate(j) for j in jobs if j.get("ref")}
    dest = sys.argv[2] if len(sys.argv) > 2 else "-"
    if dest == "-":
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        json.dump(out, open(dest, "w"), indent=2)
        print("wrote", dest)
