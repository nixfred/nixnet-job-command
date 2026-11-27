#!/usr/bin/env python3
"""Runtime config for NixNet Job Command.

Stdlib only. Values come from, in order (later wins):
  1. built-in defaults
  2. config.toml or config.json (NJC_CONFIG / JCC_CONFIG, or a file in the repo root)
  3. profile.json (NJC_PROFILE / JCC_PROFILE, or profile_path from config)
  4. environment variables

Never logs secret values. Optional integration keys stay in the environment
or in a chmod-600 file under the data directory, never in the repo.
"""
from __future__ import annotations

import json
import os
import sys
import datetime

VERSION = "0.3.7"
APP_NAME_DEFAULT = "NixNet Job Command"
BRAND_DEFAULT = "nixfred"

# This file lives in <repo>/app/config.py
APP_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(APP_DIR)


def _repo_dir():
    return os.environ.get("NJC_ROOT") or os.environ.get("JCC_ROOT") or REPO_DIR


def _which_config():
    for key in ("NJC_CONFIG", "JCC_CONFIG"):
        p = os.environ.get(key)
        if p:
            return os.path.expanduser(p)
    root = _repo_dir()
    for name in ("config.toml", "config.json"):
        p = os.path.join(root, name)
        if os.path.isfile(p):
            return p
    return None


def _strip_comment(line):
    """Drop a trailing # comment, but keep a # that sits inside a quoted string."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i]
    return line


def _split_array(inner):
    """Split array items on commas, but keep a comma that sits inside a quoted string."""
    items, cur, quote = [], "", None
    for ch in inner:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == ",":
            items.append(cur)
            cur = ""
            continue
        cur += ch
    items.append(cur)
    return [i.strip() for i in items if i.strip()]


def _parse_toml(text):
    """Minimal TOML reader for our config subset. Uses tomllib when available."""
    try:
        import tomllib
        return tomllib.loads(text)
    except ModuleNotFoundError:
        return _parse_toml_fallback(text)


def _parse_toml_fallback(text):
    """Python 3.9 and 3.10 have no tomllib. Tables, dotted keys, strings, ints, bools, string arrays."""
    data = {}
    section = data
    for raw in text.splitlines():
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            path = line[1:-1].strip()
            cur = data
            for part in path.split("."):
                cur = cur.setdefault(part, {})
            section = cur
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if v in ("true", "false"):
            val = v == "true"
        elif v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            items = []
            for item in _split_array(inner):
                items.append(item.strip('"').strip("'"))
            val = items
        elif (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            val = v[1:-1]
        else:
            try:
                val = int(v)
            except ValueError:
                try:
                    val = float(v)
                except ValueError:
                    val = v
        section[k] = val
    return data


def _load_file(path):
    if not path or not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    if path.endswith(".json"):
        return json.loads(text or "{}")
    return _parse_toml(text)


def _load_profile(path):
    if not path or not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.loads(f.read() or "{}")


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [v]


class Config:
    def __init__(self):
        self.reload()

    def reload(self):
        root = _repo_dir()
        cfg_path = _which_config()
        file_cfg = _load_file(cfg_path) if cfg_path else {}

        def g(*keys, default=None):
            cur = file_cfg
            for k in keys:
                if not isinstance(cur, dict) or k not in cur:
                    return default
                cur = cur[k]
            return cur

        profile_path = (
            os.environ.get("NJC_PROFILE")
            or os.environ.get("JCC_PROFILE")
            or g("profile_path")
            or os.path.join(root, "profile.json")
        )
        profile_path = os.path.expanduser(profile_path)
        # Fall back to the example profile so a fresh clone still boots.
        if not os.path.isfile(profile_path):
            example = os.path.join(root, "profile.example.json")
            if os.path.isfile(example):
                profile_path = example
        self.profile_path = profile_path
        self.profile = _load_profile(profile_path)

        self.repo_dir = root
        self.app_dir = APP_DIR
        self.config_path = cfg_path
        self.app_name = (
            os.environ.get("NJC_APP_NAME")
            or g("app_name")
            or self.profile.get("app_name")
            or APP_NAME_DEFAULT
        )
        self.brand = g("brand") or self.profile.get("brand") or BRAND_DEFAULT
        self.display_name = (
            os.environ.get("NJC_DISPLAY_NAME")
            or g("display_name")
            or self.profile.get("display_name")
            or "there"
        )
        self.first_name = (
            g("first_name") or self.profile.get("first_name") or self.display_name
        )
        # "manual" keeps the stored queue_rank order. "newest_best" ranks by fit plus freshness on every read.
        qo = (os.environ.get("NJC_QUEUE_ORDER") or g("queue_order") or "manual")
        self.queue_order = str(qo).strip().lower() if str(qo).strip().lower() in ("manual", "newest_best") else "manual"
        self.logo = g("logo") or self.profile.get("logo") or "/logo.svg"
        self.favicon = g("favicon") or self.profile.get("favicon") or "/favicon.svg"
        # Optional folder whose files shadow app/static (logo, favicon, ...).
        ov = g("static_overlay") or self.profile.get("static_overlay") or ""
        self.static_overlay = os.path.expanduser(ov) if ov else ""
        self.default_resume = g("default_resume") or self.profile.get("default_resume") or "resume.pdf"
        self.resume_note = (
            g("resume_note") or self.profile.get("resume_note")
            or "Keep the file your profile names, and attach it on the official form."
        )
        # Appended to closed-at times in the UI, for example "ET".
        self.timezone_label = g("timezone_label") or self.profile.get("timezone_label") or ""
        self.legal_name = (
            g("legal_name")
            or self.profile.get("legal_name")
            or self.display_name
        )
        self.identity_vault = (
            g("identity", "vault")
            if isinstance(g("identity"), dict)
            else None
        ) or self.profile.get("identity_vault") or "Identity"
        self.assistant_name = (
            g("assistant_name")
            or self.profile.get("assistant_name")
            or "your assistant"
        )
        self.timezone = (
            os.environ.get("NJC_TIMEZONE")
            or os.environ.get("JCC_TIMEZONE")
            or g("timezone")
            or self.profile.get("timezone")
            or "local"
        )
        data_default = os.path.join(root, "data")
        self.data_dir = os.path.expanduser(
            os.environ.get("NJC_DATA")
            or os.environ.get("JCC_DATA")
            or g("data_dir")
            or data_default
        )
        # Database file name inside data_dir. A new install uses njc.db; an
        # install migrated from the private Job Command Center keeps jcc.db.
        self.db_file = os.path.basename(
            os.environ.get("NJC_DB_FILE")
            or os.environ.get("JCC_DB_FILE")
            or g("db_file")
            or "njc.db"
        )
        seed_default = os.path.join(root, "seed", "example_seed.json")
        seed_raw = (
            os.environ.get("NJC_SEED")
            or os.environ.get("JCC_SEED")
            or g("seed_path")
            or seed_default
        )
        # "none" (or "off") turns seeding off, so a real database never gets
        # the example jobs mixed in.
        if str(seed_raw).strip().lower() in ("none", "off", "false", "disabled"):
            self.seed_path = None
        else:
            self.seed_path = os.path.expanduser(seed_raw)
        self.port = int(
            os.environ.get("NJC_PORT")
            or os.environ.get("JCC_PORT")
            or g("port")
            or 8742
        )
        bind_raw = (
            os.environ.get("NJC_BIND")
            or os.environ.get("JCC_BIND")
            or g("bind")
            or "127.0.0.1"
        )
        if isinstance(bind_raw, list):
            self.bind = [str(b).strip() for b in bind_raw if str(b).strip()]
        else:
            self.bind = [b.strip() for b in str(bind_raw).split(",") if b.strip()]
        if not self.bind:
            self.bind = ["127.0.0.1"]
        # Never silently expand to all interfaces.
        if "0.0.0.0" in self.bind and not (
            os.environ.get("NJC_ALLOW_PUBLIC_BIND") or os.environ.get("JCC_ALLOW_PUBLIC_BIND")
        ):
            sys.stderr.write(
                "refusing to bind 0.0.0.0 unless NJC_ALLOW_PUBLIC_BIND=1 is set; using 127.0.0.1\n"
            )
            self.bind = ["127.0.0.1"]

        start = (
            os.environ.get("NJC_PROGRESS_START")
            or os.environ.get("JCC_PROGRESS_START")
            or g("progress_start")
            or self.profile.get("progress_start")
        )
        self.progress_start = _parse_date(start)  # may be None: first boot uses today
        self.daily_goal = int(
            os.environ.get("NJC_DAILY_GOAL")
            or os.environ.get("JCC_DAILY_GOAL")
            or g("daily_goal")
            or self.profile.get("daily_goal")
            or 5
        )
        fit = g("fit") or self.profile.get("fit") or {}
        if not isinstance(fit, dict):
            fit = {}
        self.fit = {
            "min_base_pay": fit.get("min_base_pay"),
            "locations": _as_list(fit.get("locations")),
            "kinds": _as_list(fit.get("kinds")),
            "notes": fit.get("notes") or "",
        }
        self.min_python = (3, 9)

    def require_python(self):
        if sys.version_info < self.min_python:
            need = ".".join(str(x) for x in self.min_python)
            have = ".".join(str(x) for x in sys.version_info[:3])
            raise SystemExit("NixNet Job Command needs Python %s or newer (found %s)." % (need, have))

    def zoneinfo(self):
        name = self.timezone
        if not name or name == "local":
            return None
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            return None

    def now(self):
        tz = self.zoneinfo()
        if tz is not None:
            return datetime.datetime.now(tz)
        return datetime.datetime.now().astimezone()

    def today(self):
        return self.now().date()

    def display(self):
        return {
            "app_name": self.app_name,
            "brand": self.brand,
            "display_name": self.display_name,
            "legal_name": self.legal_name,
            "identity_vault": self.identity_vault,
            "assistant_name": self.assistant_name,
            "first_name": self.first_name,
            "logo": self.logo,
            "favicon": self.favicon,
            "default_resume": self.default_resume,
            "resume_note": self.resume_note,
            "timezone_label": self.timezone_label,
            "timezone": self.timezone,
            "version": VERSION,
        }


def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


CFG = Config()
