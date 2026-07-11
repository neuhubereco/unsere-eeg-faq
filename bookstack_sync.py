#!/usr/bin/env python3
"""bookstack_sync.py — synct faq_data.json in das BookStack-Kapitel
"FAQ (WhatsApp Zusammenfassung)" auf docs.eegfaktura.at.

Idempotent: pro Abschnitt (portal/tool × faq/spezial) genau eine Seite,
identifiziert über den exakten Seitennamen im Kapitel; Update nur bei
Content-Änderung (SHA-256 in bookstack_state.json).

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
  BOOKSTACK_INCLUDE_TOOL default 1 (0 = nur Portal-Seiten)

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
    env.setdefault("BOOKSTACK_INCLUDE_TOOL", "1")
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


def render_page(entries, section_label, stamp):
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
        pages.append({"key": "%s-%s" % (prod, sec), "name": name,
                      "html": render_page(entries, sec_label, stamp)})
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
            fn = "/tmp/bookstack_pages/%s.html" % p["key"]
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

    if apply_mode:
        json.dump(state, open(STATE_FILE, "w"), indent=1)
        log("fertig: %d Seite(n) geschrieben, %d gesamt." % (changed, len(pages)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
