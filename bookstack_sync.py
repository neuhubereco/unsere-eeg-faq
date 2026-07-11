#!/usr/bin/env python3
"""bookstack_sync.py — synct faq_data.json in das BookStack-Kapitel
"FAQ (WhatsApp Zusammenfassung)" auf docs.eegfaktura.at.

Idempotent: pro Abschnitt (portal/tool × faq/spezial) genau eine Seite,
identifiziert über den exakten Seitennamen im Kapitel; Update nur bei
Content-Änderung (SHA-256 in bookstack_state.json).

Default = Dry-Run (zeigt geplante Aktionen, schreibt nichts).
  --apply   tatsächlich anlegen/aktualisieren
  --dump    gerenderte HTML-Seiten nach /tmp/bookstack_pages/ schreiben

Konfiguration: /root/faq/bookstack.env (KEY=VALUE, nicht im Repo!)
  BOOKSTACK_TOKEN_ID / BOOKSTACK_TOKEN_SECRET  (Pflicht fürs Schreiben;
      API-Token: docs.eegfaktura.at → Mein Account → API-Tokens)
  BOOKSTACK_URL          default https://docs.eegfaktura.at
  BOOKSTACK_CHAPTER_ID   default 43
  BOOKSTACK_INCLUDE_TOOL default 1 (0 = nur Portal-Seiten)

Kein Auto-Retry (bewusst): jeder API-Call genau 1 Versuch, Fehler beenden
den Lauf laut. Ohne konfigurierten Token beendet sich --apply mit Exit 0
("nicht konfiguriert"), damit der systemd-Timer sauber durchläuft.
"""
import datetime
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE, "faq_data.json")
ENV_FILE = os.path.join(BASE, "bookstack.env")
STATE_FILE = os.path.join(BASE, "bookstack_state.json")

FAQ_SITE = "https://faq.unsere-eeg.at"
REPO_URL = "https://github.com/neuhubereco/unsere-eeg-faq"


def log(*a):
    print(datetime.datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def load_env():
    env = {}
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    for k in ("BOOKSTACK_URL", "BOOKSTACK_TOKEN_ID", "BOOKSTACK_TOKEN_SECRET",
              "BOOKSTACK_CHAPTER_ID", "BOOKSTACK_INCLUDE_TOOL"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    env.setdefault("BOOKSTACK_URL", "https://docs.eegfaktura.at")
    env.setdefault("BOOKSTACK_CHAPTER_ID", "43")
    env.setdefault("BOOKSTACK_INCLUDE_TOOL", "1")
    return env


def api(env, method, path, payload=None):
    """Genau EIN Versuch pro Call — kein Retry (Lockout-/Abuse-Schutz)."""
    url = env["BOOKSTACK_URL"].rstrip("/") + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Token %s:%s" % (env["BOOKSTACK_TOKEN_ID"],
                                          env["BOOKSTACK_TOKEN_SECRET"]),
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "unsere-eeg-faq bookstack-sync (florian@neuhuber.net)",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:400]
        raise SystemExit("API %s %s -> HTTP %s: %s" % (method, path, e.code, body))


def render_page(entries, section_label, stamp):
    """Ein Abschnitt (z.B. Portal/faq) -> eine BookStack-Seite (HTML)."""
    out = []
    out.append(
        '<p class="callout info">Automatisch erstellte Zusammenfassung der '
        'EEG-Faktura-WhatsApp-Gruppen (KI-kuratiert). Stand: %s · %d Einträge. '
        'Quelle &amp; Pflege: <a href="%s">faq.unsere-eeg.at</a> '
        '(<a href="%s">GitHub</a>). Diese Seite wird automatisch überschrieben '
        '&mdash; Korrekturen bitte dort einbringen.</p>'
        % (stamp, len(entries), FAQ_SITE, REPO_URL))
    cat = None
    for e in entries:  # faq_data.json ist kanonisch sortiert (Kategorie, count)
        if e.get("kategorie") != cat:
            cat = e.get("kategorie")
            out.append("<h2>%s</h2>" % cat)
        out.append("<h3>%s</h3>" % e["frage"])
        out.append(e["antwort"])
        if section_label == "Häufige Fragen" and e.get("count", 1) >= 3:
            out.append("<p><em>In den Gruppen %d× gefragt.</em></p>" % e["count"])
    return "\n".join(out)


def build_pages(data, include_tool, stamp):
    plan = [("portal", "faq", "EEG Faktura – Häufige Fragen (WhatsApp)", "Häufige Fragen"),
            ("portal", "spezial", "EEG Faktura – Spezialfälle (WhatsApp)", "Spezialfälle")]
    if include_tool:
        plan += [("tool", "faq", "EEG Faktura Tool – Häufige Fragen (WhatsApp)", "Häufige Fragen"),
                 ("tool", "spezial", "EEG Faktura Tool – Spezialfälle (WhatsApp)", "Spezialfälle")]
    pages = []
    for prod, sec, name, sec_label in plan:
        entries = data.get(prod, {}).get(sec, [])
        if not entries:
            continue
        pages.append({
            "key": "%s-%s" % (prod, sec),
            "name": name,
            "html": render_page(entries, sec_label, stamp),
        })
    return pages


def main():
    apply_mode = "--apply" in sys.argv
    dump = "--dump" in sys.argv

    env = load_env()
    has_creds = bool(env.get("BOOKSTACK_TOKEN_ID") and env.get("BOOKSTACK_TOKEN_SECRET"))
    if apply_mode and not has_creds:
        log("bookstack_sync: kein API-Token konfiguriert (%s) — übersprungen." % ENV_FILE)
        return 0

    data = json.load(open(DATA_FILE))
    stamp = datetime.date.fromtimestamp(os.path.getmtime(DATA_FILE)).strftime("%d.%m.%Y")
    pages = build_pages(data, env["BOOKSTACK_INCLUDE_TOOL"] != "0", stamp)

    if dump:
        os.makedirs("/tmp/bookstack_pages", exist_ok=True)
        for p in pages:
            fn = "/tmp/bookstack_pages/%s.html" % p["key"]
            open(fn, "w").write("<h1>%s</h1>\n%s" % (p["name"], p["html"]))
            log("dump:", fn)

    state = {}
    if os.path.exists(STATE_FILE):
        state = json.load(open(STATE_FILE))

    existing = {}
    if has_creds:
        chapter = api(env, "GET", "/api/chapters/%s" % env["BOOKSTACK_CHAPTER_ID"])
        for p in chapter.get("pages", []):
            existing[p["name"]] = p["id"]
        log("Kapitel %s: '%s', %d vorhandene Seiten"
            % (env["BOOKSTACK_CHAPTER_ID"], chapter.get("name"), len(existing)))
    else:
        log("Hinweis: kein Token — Bestand im Kapitel unbekannt (reiner Render-Check).")

    changed = 0
    for p in pages:
        h = hashlib.sha256(p["html"].encode()).hexdigest()
        page_id = existing.get(p["name"]) or state.get(p["key"], {}).get("page_id")
        if page_id and state.get(p["key"], {}).get("hash") == h and p["name"] in existing:
            log("SKIP  ", p["name"], "(unverändert)")
            continue
        action = "UPDATE" if page_id and p["name"] in existing else "CREATE"
        log("%s%s %s (%d Zeichen)" % ("" if apply_mode else "würde ", action, p["name"], len(p["html"])))
        if apply_mode:
            if action == "CREATE":
                res = api(env, "POST", "/api/pages", {
                    "chapter_id": int(env["BOOKSTACK_CHAPTER_ID"]),
                    "name": p["name"], "html": p["html"]})
                page_id = res["id"]
            else:
                api(env, "PUT", "/api/pages/%d" % page_id,
                    {"name": p["name"], "html": p["html"]})
            state[p["key"]] = {"page_id": page_id, "hash": h}
            changed += 1

    if apply_mode:
        json.dump(state, open(STATE_FILE, "w"), indent=1)
        log("fertig: %d Seite(n) geschrieben, %d gesamt." % (changed, len(pages)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
