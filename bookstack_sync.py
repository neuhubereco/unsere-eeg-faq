#!/usr/bin/env python3
"""bookstack_sync.py — synct faq_data.json in das BookStack-Kapitel
"FAQ (WhatsApp Zusammenfassung)" auf docs.eegfaktura.at.

Struktur: eine Seite pro Kategorie (Einrichtung, Mitglieder, …) mit den
häufigen Fragen oben und den Spezialfällen darunter; Mini-Kategorien
(<3 Einträge) wandern in "Sonstiges". Nur das Online-Portal
(BOOKSTACK_INCLUDE_TOOL=1 ergänzt eine Sammelseite fürs Desktop-Tool).

Idempotent: Seiten werden über den exakten Namen im Kapitel wiedergefunden;
Update nur bei Content-Änderung (SHA-256 in bookstack_state.json). Eigene
Seiten, die nicht mehr im Plan sind, werden gelöscht (BookStack-Papierkorb).

Auth: Session-Login mit Benutzer/Passwort über den OIDC-Flow
(docs → login.eegfaktura.at Keycloak), da die Rolle keinen API-Zugriff hat.
Geschrieben wird über die normalen Web-Endpoints (create-page/draft, PUT page).
GENAU EIN Login-Versuch pro Lauf — kein Retry (Keycloak-Lockout-Schutz).

Default = Dry-Run (zeigt geplante Aktionen, schreibt nichts).
  --apply   tatsächlich anlegen/aktualisieren
  --check   nur Login + vorhandene Seiten im Kapitel auflisten
  --dump    gerenderte HTML-Seiten nach /tmp/bookstack_pages/ schreiben

Konfiguration: /root/faq/bookstack.env (KEY=VALUE, chmod 600, NIE im Repo!)
  BOOKSTACK_USER / BOOKSTACK_PASSWORD  (Pflicht; eegfaktura.at-Konto)
  BOOKSTACK_URL          default https://docs.eegfaktura.at
  BOOKSTACK_BOOK         default faq
  BOOKSTACK_CHAPTER      default faq-whatsapp-zusammenfassung
  BOOKSTACK_INCLUDE_TOOL default 0 (1 = zusätzlich Desktop-Tool-Seite)

Ohne konfigurierte Zugangsdaten beendet sich --apply mit Exit 0
("nicht konfiguriert"), damit der systemd-Timer sauber durchläuft.
"""
import datetime
import hashlib
import html as htmlmod
import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE, "faq_data.json")
ENV_FILE = os.path.join(BASE, "bookstack.env")
STATE_FILE = os.path.join(BASE, "bookstack_state.json")

FAQ_SITE = "https://faq.unsere-eeg.at"
REPO_URL = "https://github.com/neuhubereco/unsere-eeg-faq"
UA = "unsere-eeg-faq bookstack-sync (florian@neuhuber.net)"


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
    for k in ("BOOKSTACK_URL", "BOOKSTACK_USER", "BOOKSTACK_PASSWORD",
              "BOOKSTACK_BOOK", "BOOKSTACK_CHAPTER", "BOOKSTACK_INCLUDE_TOOL"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    env.setdefault("BOOKSTACK_URL", "https://docs.eegfaktura.at")
    env.setdefault("BOOKSTACK_BOOK", "faq")
    env.setdefault("BOOKSTACK_CHAPTER", "faq-whatsapp-zusammenfassung")
    env.setdefault("BOOKSTACK_INCLUDE_TOOL", "0")
    return env


class Session:
    """Cookie-Session über docs.eegfaktura.at + login.eegfaktura.at."""

    def __init__(self, base):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = None

    def req(self, url, form=None, method=None):
        """Ein Request (Redirects werden gefolgt). -> (final_url, body)"""
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        r = urllib.request.Request(url, data=data, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,*/*",
        }, method=method)
        try:
            with self.op.open(r, timeout=30) as resp:
                return resp.geturl(), resp.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise SystemExit("HTTP %s bei %s %s: %s" % (
                e.code, method or ("POST" if form is not None else "GET"),
                url, body[:300]))

    def _grab_csrf(self, body):
        m = re.search(r'<meta name="token" content="([^"]+)"', body)
        if m:
            self.csrf = m.group(1)

    def login(self, user, password):
        """OIDC-Login, GENAU EIN Versuch. Wirft SystemExit bei Fehlschlag."""
        _, body = self.req(self.base + "/login")
        self._grab_csrf(body)
        if not self.csrf:
            raise SystemExit("Login-Seite ohne CSRF-Token — Layout geändert?")
        # Start OIDC-Flow -> landet (nach Redirects) auf der Keycloak-Maske
        url, body = self.req(self.base + "/oidc/login", form={"_token": self.csrf})
        if "login.eegfaktura.at" not in url:
            # bereits eingeloggt (bestehende Session)?
            if self._logged_in(body):
                log("Session bestand bereits — Login übersprungen.")
                self._grab_csrf(body)
                return
            raise SystemExit("OIDC-Redirect kam nicht bei Keycloak an: " + url)
        actions = [a for a in re.findall(r'<form[^>]+action="([^"]+)"', body)
                   if "login-actions" in a]
        if not actions:
            raise SystemExit("Keycloak-Loginformular nicht gefunden (%s)" % url)
        action = htmlmod.unescape(actions[0])
        url, body = self.req(action, form={
            "username": user, "password": password, "credentialId": ""})
        if not url.startswith(self.base) or not self._logged_in(body):
            hint = ""
            m = re.search(r'kc-error-message.*?<span[^>]*>([^<]+)', body, re.S)
            if m:
                hint = " — Keycloak: " + m.group(1).strip()
            raise SystemExit("Login fehlgeschlagen (kein 2. Versuch!)%s" % hint)
        self._grab_csrf(body)
        log("eingeloggt als", user)

    def _logged_in(self, body):
        return "/logout" in body or "Abmelden" in body


def chapter_pages(sess, env):
    """Vorhandene Seiten im Kapitel: {name: page_slug}"""
    url = "%s/books/%s/chapter/%s" % (sess.base, env["BOOKSTACK_BOOK"],
                                      env["BOOKSTACK_CHAPTER"])
    _, body = sess.req(url)
    sess._grab_csrf(body)
    # nur Hauptinhalt parsen — die Sidebar listet ALLE Buch-Seiten
    idx = body.find('id="main-content"')
    if idx > -1:
        body = body[idx:]
    pages = {}
    pat = (r'href="%s/books/%s/page/([^"]+)"[^>]*>\s*.*?'
           r'entity-list-item-name[^>]*>([^<]+)<'
           % (re.escape(sess.base), re.escape(env["BOOKSTACK_BOOK"])))
    for slug, name in re.findall(pat, body, re.S):
        pages[htmlmod.unescape(name).strip()] = slug
    return pages


def create_page(sess, env, name, page_html):
    """Neue Seite im Kapitel: create-page legt Draft an, POST publiziert."""
    url, body = sess.req("%s/books/%s/chapter/%s/create-page"
                         % (sess.base, env["BOOKSTACK_BOOK"], env["BOOKSTACK_CHAPTER"]))
    m = re.match(r'%s/books/%s/draft/(\d+)$'
                 % (re.escape(sess.base), re.escape(env["BOOKSTACK_BOOK"])), url)
    if not m:
        raise SystemExit("create-page: unerwartete Draft-URL " + url)
    sess._grab_csrf(body)
    final_url, _ = sess.req(
        "%s/books/%s/draft/%s" % (sess.base, env["BOOKSTACK_BOOK"], m.group(1)),
        form={"_token": sess.csrf, "name": name, "html": page_html})
    slug = final_url.rstrip("/").split("/page/")[-1]
    return slug


def update_page(sess, env, slug, name, page_html):
    sess.req("%s/books/%s/page/%s" % (sess.base, env["BOOKSTACK_BOOK"], slug),
             form={"_token": sess.csrf, "_method": "PUT", "name": name,
                   "html": page_html,
                   "summary": "Auto-Sync von faq.unsere-eeg.at"})


def page_exists(sess, env, slug):
    url = "%s/books/%s/page/%s" % (sess.base, env["BOOKSTACK_BOOK"], slug)
    r = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with sess.op.open(r, timeout=30):
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        return True


def delete_page(sess, env, slug):
    """Löschen = BookStack-Papierkorb. Liefert True nur bei verifiziertem
    Erfolg — ohne Delete-Recht antwortet BookStack mit Redirect+Flash
    statt Fehlercode (2026-07-11 live beobachtet)."""
    sess.req("%s/books/%s/page/%s" % (sess.base, env["BOOKSTACK_BOOK"], slug),
             form={"_token": sess.csrf, "_method": "DELETE"})
    return not page_exists(sess, env, slug)


def _callout(stamp, n_faq, n_spez):
    teile = []
    if n_faq:
        teile.append("%d häufige Fragen" % n_faq)
    if n_spez:
        teile.append("%d Spezialfälle" % n_spez)
    return ('<p class="callout info">Automatisch aus den EEG-Faktura-WhatsApp-'
            'Gruppen zusammengefasst (KI-kuratiert). Stand: %s · %s. '
            'Quelle &amp; Pflege: <a href="%s">faq.unsere-eeg.at</a> '
            '(<a href="%s">GitHub</a>). Diese Seite wird automatisch '
            'überschrieben &mdash; Korrekturen bitte dort einbringen.</p>'
            % (stamp, " und ".join(teile), FAQ_SITE, REPO_URL))


def _entries_html(entries, with_count):
    out = []
    for e in entries:
        out.append("<h3>%s</h3>" % e["frage"])
        out.append(e["antwort"])
        if with_count and e.get("count", 1) >= 3:
            out.append("<p><em>In den Gruppen %d× gefragt.</em></p>" % e["count"])
    return out


def render_cat_page(faq, spez, stamp):
    """Eine Kategorie-Seite: Häufige Fragen oben, Spezialfälle darunter."""
    out = [_callout(stamp, len(faq), len(spez))]
    if faq:
        out.append("<h2>Häufige Fragen</h2>")
        out += _entries_html(faq, with_count=True)
    if spez:
        out.append("<h2>Spezialfälle</h2>")
        out.append("<p><em>Seltene, aber lehrreiche Einzelfälle aus den "
                   "Gruppen &mdash; hilfreich, wenn die Standard-Antworten "
                   "nicht weiterhelfen.</em></p>")
        out += _entries_html(spez, with_count=False)
    return "\n".join(out)


def build_pages(data, include_tool, stamp):
    """Portal: eine Seite je Kategorie (Reihenfolge wie in faq_data.json,
    'Sonstiges' zuletzt); Mini-Kategorien (<3 Einträge) → 'Sonstiges'."""
    by_cat = {}
    order = []
    for sec in ("faq", "spezial"):
        for e in data.get("portal", {}).get(sec, []):
            cat = e.get("kategorie") or "Sonstiges"
            if cat not in by_cat:
                by_cat[cat] = {"faq": [], "spezial": []}
                order.append(cat)
            by_cat[cat][sec].append(e)
    # Mini-Kategorien einfalten
    for cat in [c for c in order if c != "Sonstiges"]:
        n = len(by_cat[cat]["faq"]) + len(by_cat[cat]["spezial"])
        if n < 3:
            dst = by_cat.setdefault("Sonstiges", {"faq": [], "spezial": []})
            if "Sonstiges" not in order:
                order.append("Sonstiges")
            dst["faq"] += by_cat[cat]["faq"]
            dst["spezial"] += by_cat[cat]["spezial"]
            order.remove(cat)
            del by_cat[cat]
    if "Sonstiges" in order:  # immer ans Ende
        order.remove("Sonstiges")
        order.append("Sonstiges")

    pages = []
    for cat in order:
        pages.append({"key": "cat:" + cat, "name": cat,
                      "html": render_cat_page(by_cat[cat]["faq"],
                                              by_cat[cat]["spezial"], stamp)})
    if include_tool:
        t = data.get("tool", {})
        pages.append({"key": "tool", "name": "EEG Faktura Tool (Desktop)",
                      "html": render_cat_page(t.get("faq", []),
                                              t.get("spezial", []), stamp)})
    return pages


def main():
    apply_mode = "--apply" in sys.argv
    check_mode = "--check" in sys.argv
    dump = "--dump" in sys.argv

    env = load_env()
    has_creds = bool(env.get("BOOKSTACK_USER") and env.get("BOOKSTACK_PASSWORD"))
    if (apply_mode or check_mode) and not has_creds:
        log("bookstack_sync: keine Zugangsdaten konfiguriert (%s) — übersprungen." % ENV_FILE)
        return 0

    data = json.load(open(DATA_FILE))
    stamp = datetime.date.fromtimestamp(os.path.getmtime(DATA_FILE)).strftime("%d.%m.%Y")
    pages = build_pages(data, env["BOOKSTACK_INCLUDE_TOOL"] != "0", stamp)

    if dump:
        os.makedirs("/tmp/bookstack_pages", exist_ok=True)
        for p in pages:
            fn = "/tmp/bookstack_pages/%s.html" % re.sub(r"[^\wäöüÄÖÜß-]+", "_", p["key"])
            open(fn, "w").write("<h1>%s</h1>\n%s" % (p["name"], p["html"]))
            log("dump:", fn)

    state = {}
    if os.path.exists(STATE_FILE):
        state = json.load(open(STATE_FILE))

    existing = {}
    sess = None
    if has_creds:
        sess = Session(env["BOOKSTACK_URL"])
        sess.login(env["BOOKSTACK_USER"], env["BOOKSTACK_PASSWORD"])
        existing = chapter_pages(sess, env)
        log("Kapitel '%s': %d vorhandene Seite(n): %s"
            % (env["BOOKSTACK_CHAPTER"], len(existing),
               ", ".join(existing) or "—"))
        if check_mode:
            return 0
    else:
        log("Hinweis: keine Zugangsdaten — Bestand unbekannt (reiner Render-Check).")

    changed = 0
    for p in pages:
        h = hashlib.sha256(p["html"].encode()).hexdigest()
        slug = existing.get(p["name"])
        if slug and state.get(p["key"], {}).get("hash") == h:
            log("SKIP  ", p["name"], "(unverändert)")
            continue
        action = "UPDATE" if slug else "CREATE"
        log("%s%s %s (%d Zeichen)" % ("" if apply_mode else "würde ",
                                      action, p["name"], len(p["html"])))
        if apply_mode:
            if action == "CREATE":
                slug = create_page(sess, env, p["name"], p["html"])
            else:
                update_page(sess, env, slug, p["name"], p["html"])
            state[p["key"]] = {"slug": slug, "hash": h}
            changed += 1
            log("   ->", "%s/books/%s/page/%s" % (sess.base, env["BOOKSTACK_BOOK"], slug))

    # Verwaiste EIGENE Seiten (in state, nicht mehr im Plan) entfernen.
    plan_keys = {p["key"] for p in pages}
    existing_slugs = set(existing.values())
    for key in [k for k in state if k not in plan_keys]:
        slug = state[key].get("slug")
        if not slug or slug not in existing_slugs:
            if apply_mode:
                del state[key]  # Seite gibt es nicht mehr
            continue
        if not apply_mode:
            log("würde DELETE %s (nicht mehr im Plan)" % slug)
            continue
        if delete_page(sess, env, slug):
            log("DELETE %s (nicht mehr im Plan)" % slug)
            del state[key]
        else:
            log("WARNUNG: Löschen von %s fehlgeschlagen (fehlendes Delete-"
                "Recht?) — bleibt im State, neuer Versuch beim nächsten Lauf."
                % slug)

    if apply_mode:
        json.dump(state, open(STATE_FILE, "w"), indent=1)
        log("fertig: %d Seite(n) geschrieben, %d gesamt." % (changed, len(pages)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
