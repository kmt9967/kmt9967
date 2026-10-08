#!/usr/bin/env python3
"""Regenerate the dynamic sections of README.md (Python standard library only).

Sections live between marker comments:
    <!-- FEATURED:START --> ... <!-- FEATURED:END -->
    <!-- ACHIEVEMENTS:START --> ... <!-- ACHIEVEMENTS:END -->
    <!-- ARTICLES:START --> ... <!-- ARTICLES:END -->
    <!-- REPOS:START --> ... <!-- REPOS:END -->

Each section is built independently. If building one fails (network error,
bad response, empty result that should not be empty), that section keeps its
previous content, so the README is never blanked. The file is only rewritten
when something actually changed.

Usage:
    python scripts/update_readme.py            # update README.md in place
    python scripts/update_readme.py --dry-run  # print the result, write nothing
    python scripts/update_readme.py --offline  # no network: data-file sections only
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
UA = "kmt9967-profile-readme/1.0 (+https://github.com/kmt9967/kmt9967)"
TIMEOUT = 20


def log(msg):
    print(msg, file=sys.stderr)


def load(name):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


def fetch(url, accept="*/*", token=None):
    headers = {"User-Agent": UA, "Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        if resp.status != 200:
            raise RuntimeError(f"{url} -> HTTP {resp.status}")
        return resp.read().decode("utf-8", errors="replace")


def fmt_date(value):
    """'2026-09-27' or ISO timestamp -> '27 Sep 2026'."""
    d = dt.date.fromisoformat(value[:10])
    return f"{d.day} {d.strftime('%b %Y')}"


def md_escape(text):
    return text.replace("|", "\\|").replace("\n", " ").strip()


def banned_hit(line, banned):
    """True if any word or dotted domain fragment of the line hashes to a banned value."""
    for token in re.findall(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*", line.lower()):
        parts = token.split(".")
        for i in range(len(parts)):
            for j in range(i + 1, len(parts) + 1):
                if hashlib.sha256(".".join(parts[i:j]).encode()).hexdigest() in banned:
                    return True
    return False


def clean(lines, banned):
    """Drop any generated line that contains a banned term."""
    out = []
    for line in lines:
        if banned_hit(line, banned):
            log(f"  dropped a line containing a banned term: {line[:60]}…")
            continue
        out.append(line)
    return out


def verified(items):
    ok = []
    for item in items:
        if item.get("status") == "verified" and re.fullmatch(r"\d{4}-\d{2}-\d{2}", item.get("verified_on", "")):
            ok.append(item)
    return ok


def link_people(text, people):
    for name, url in people.items():
        text = text.replace(name, f"[{name}]({url})")
    return text


# ---------------------------------------------------------------- sections

def build_featured(cfg, token, offline):
    item = next((f for f in cfg["featured"] if f.get("active")), None)
    if not item:
        raise RuntimeError("no active featured project")
    lines = [f"### {item['title']}", ""]
    if item.get("badge"):
        lines += [f"**{item['badge']}**", ""]
    lines += [item["summary"], ""]
    meta = []
    if item.get("stack"):
        meta.append("**Stack:** " + ", ".join(item["stack"]))
    if item.get("repo") and not offline:
        repo = json.loads(fetch(f"https://api.github.com/repos/{item['repo']}", "application/vnd.github+json", token))
        if repo.get("private") or repo.get("archived"):
            raise RuntimeError("featured repo is private or archived")
        meta.append(f"**Last updated:** {fmt_date(repo['pushed_at'])}")
    if meta:
        lines += [" · ".join(meta), ""]
    links = list(item.get("links", []))
    if item.get("repo"):
        links.insert(0, {"label": "Repository", "url": f"https://github.com/{item['repo']}"})
    lines.append(" · ".join(f"[{l['label']}]({l['url']})" for l in links))
    return lines


def build_achievements(cfg, data):
    people = cfg.get("people_links", {})
    lines = []
    for h in verified(data.get("highlights", [])):
        line = f"- **{h['title']}** for [{h['project']}]({h['url']})"
        if h.get("detail"):
            line += f": {h['detail']}"
        lines.append(line)
    if not lines:
        raise RuntimeError("no verified highlights")

    stats = verified(data.get("platform_stats", []))
    if stats:
        lines += ["", "| Platform | Snapshot | Last verified |", "|---|---|---|"]
        for s in stats:
            lines.append(f"| [{s['platform']}]({s['url']}) | {md_escape(' · '.join(s['facts']))} | {s['verified_on']} |")
        lines += ["", "<sub>Platform numbers are dated snapshots, not live counters.</sub>"]

    hacks = sorted(verified(data.get("hackathons", [])), key=lambda h: h["date"], reverse=True)
    if hacks:
        lines += ["", "**Hackathon entries (2026)**", "",
                  "| Project | Event | Result | Team | Links |", "|---|---|---|---|---|"]
        for h in hacks:
            result = f"**{h['result']}**" if h["result"].lower().startswith("finalist") else h["result"]
            links = [f"[Repo]({h['repo']})"] if h.get("repo") else []
            if h.get("case_study"):
                links.append(f"[Case study]({h['case_study']})")
            if h.get("submission"):
                links.append(f"[Submission]({h['submission']})")
            if h.get("certificate"):
                links.append(f"[Certificate]({h['certificate']})")
            event = f"{md_escape(h['event'])} · {h['platform']} · {fmt_date(h['date'])}"
            team = link_people(md_escape(h.get("team", "")), people)
            lines.append(f"| **{md_escape(h['project'])}** | {event} | {result} | {team} | {' · '.join(links)} |")

    certs = sorted(verified(data.get("certificates", [])), key=lambda c: c.get("date", ""), reverse=True)
    if certs:
        lines += ["", "**Certificates**", ""]
        for c in certs:
            title = f"[{md_escape(c['title'])}]({c['url']})" if c.get("url") else md_escape(c["title"])
            bits = [b for b in (c.get("issuer"), c.get("event"), fmt_date(c["date"]) if c.get("date") else None) if b]
            lines.append(f"- {title} · {' · '.join(md_escape(b) for b in bits)} <sub>(last verified {c['verified_on']})</sub>")
    return lines


def meta_content(page, key):
    for pat in (rf'<meta[^>]+(?:property|name)="{re.escape(key)}"[^>]+content="([^"]*)"',
                rf'<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="{re.escape(key)}"'):
        m = re.search(pat, page, re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    return None


def build_articles(cfg):
    acfg = cfg["articles"]
    xml = fetch(acfg["sitemap"], "application/xml")
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    root = ET.fromstring(xml)
    site = cfg["site"].rstrip("/")
    candidates = []
    for url in root.findall("s:url", ns):
        loc = (url.findtext("s:loc", default="", namespaces=ns) or "").strip()
        path = loc[len(site):] if loc.startswith(site) else ""
        if any(path.startswith(p) and len(path) > len(p) for p in acfg["path_prefixes"]):
            candidates.append((url.findtext("s:lastmod", default="", namespaces=ns) or "", loc))
    if not candidates:
        return [acfg["empty_text"]]
    # Read the newest pages (by lastmod) to get their real titles and publish dates.
    candidates.sort(reverse=True)
    posts = []
    for lastmod, loc in candidates[: acfg["limit"] * 3]:
        page = fetch(loc, "text/html")
        title = meta_content(page, "og:title")
        if not title:
            m = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
            title = html.unescape(m.group(1)).strip() if m else None
        if not title:
            raise RuntimeError(f"no title on {loc}")
        title = re.sub(r"\s+[|·—-]\s+Talal Khawaja$", "", title)
        published = meta_content(page, "article:published_time") or lastmod
        desc = meta_content(page, "description") or ""
        posts.append((published or "", title, loc, desc))
    posts.sort(reverse=True)
    lines = []
    for published, title, loc, desc in posts[: acfg["limit"]]:
        line = f"- [{md_escape(title)}]({loc})"
        if published:
            line += f" · {fmt_date(published)}"
        if desc:
            line += f"<br><sub>{md_escape(html.escape(desc, quote=False))}</sub>"
        lines.append(line)
    return lines


def build_repos(cfg, token, featured_repo):
    rcfg = cfg["repos"]
    raw = fetch(f"https://api.github.com/users/{cfg['user']}/repos?type=owner&sort=pushed&per_page=100",
                "application/vnd.github+json", token)
    repos = json.loads(raw)
    if not isinstance(repos, list) or not repos:
        raise RuntimeError("GitHub API returned no repositories")
    exclude = {name.lower() for name in rcfg["exclude"]}
    if featured_repo:
        exclude.add(featured_repo.split("/")[-1].lower())
    picked = []
    for r in repos:
        if r.get("private") or r.get("fork") or r.get("archived") or r.get("disabled"):
            continue
        if r["name"].lower() in exclude:
            continue
        if rcfg.get("require_description") and not (r.get("description") or "").strip():
            continue
        picked.append(r)
    picked.sort(key=lambda r: r["pushed_at"], reverse=True)
    picked = picked[: rcfg["limit"]]
    if not picked:
        raise RuntimeError("no repositories left after filtering")
    lines = ["| Repository | What it is | Language | Updated |", "|---|---|---|---|"]
    for r in picked:
        desc = md_escape(r["description"])
        if len(desc) > 140:
            desc = desc[:137].rsplit(" ", 1)[0] + "…"
        homepage = (r.get("homepage") or "").strip()
        home = ""
        if homepage.startswith("https://"):
            label = "case study" if homepage.startswith(cfg["site"]) else "demo"
            home = f" · [{label}]({homepage})"
        lines.append(f"| [{r['name']}]({r['html_url']}){home} | {desc} | {r.get('language') or '—'} | {fmt_date(r['pushed_at'])} |")
    return lines


# ---------------------------------------------------------------- plumbing

def replace_block(text, name, lines):
    start, end = f"<!-- {name}:START -->", f"<!-- {name}:END -->"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if not pattern.search(text):
        raise RuntimeError(f"markers for {name} not found in README.md")
    body = "\n".join(lines).strip()
    return pattern.sub(lambda _: f"{start}\n{body}\n{end}", text, count=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    cfg = load("profile.json")
    achievements = load("achievements.json")
    token = os.environ.get("GITHUB_TOKEN") or None
    banned = set(cfg.get("banned_term_sha256", []))
    featured = next((f for f in cfg["featured"] if f.get("active")), {})

    original = README.read_text(encoding="utf-8")
    text = original
    builders = [
        ("FEATURED", lambda: build_featured(cfg, token, args.offline), False),
        ("ACHIEVEMENTS", lambda: build_achievements(cfg, achievements), False),
        ("ARTICLES", lambda: build_articles(cfg), True),
        ("REPOS", lambda: build_repos(cfg, token, featured.get("repo")), True),
    ]
    failures = 0
    for name, build, needs_network in builders:
        if args.offline and needs_network:
            log(f"{name}: skipped (offline), previous content kept")
            continue
        try:
            lines = clean(build(), banned)
            if not lines:
                raise RuntimeError("section would be empty")
            text = replace_block(text, name, lines)
            log(f"{name}: ok ({len(lines)} lines)")
        except Exception as exc:  # keep the previous content for this section
            failures += 1
            log(f"{name}: FAILED ({exc.__class__.__name__}: {exc}); previous content kept")

    if args.dry_run:
        print(text)
    elif text != original:
        README.write_text(text, encoding="utf-8", newline="\n")
        log("README.md updated")
    else:
        log("README.md unchanged")
    if failures:
        log(f"{failures} section(s) kept their previous content")
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::warning::{failures} README section(s) could not be refreshed; previous content kept")


if __name__ == "__main__":
    main()
