#!/usr/bin/env python3
import json, html, datetime, collections, sys
IN = sys.argv[1] if len(sys.argv)>1 else "faq_final.json"
OUT = sys.argv[2] if len(sys.argv)>2 else "faq_index.html"
d = json.load(open(IN))
stamp = sys.argv[3] if len(sys.argv)>3 else datetime.datetime.now().strftime("%d.%m.%Y")
CATORDER = ["Einrichtung","Mitglieder","Zählpunkte/Netzbetreiber","Abrechnung","Tarife","Steuer/USt","Fehler/Störung","Bedienung","Installation/Updates","Sonstiges"]
def esc(s): return html.escape((s or "").strip())
import re as _re
_ALLOWED=_re.compile(r"&lt;(/?)(p|ol|ul|li|strong|code|br)&gt;")
def rich(s):
    return _ALLOWED.sub(r"<\1\2>", html.escape((s or "").strip()))

def render_bucket(entries, spezial=False):
    by=collections.OrderedDict()
    for e in sorted(entries, key=lambda x:(CATORDER.index(x["kategorie"]) if x["kategorie"] in CATORDER else 99, -x.get("count",1))):
        by.setdefault(e["kategorie"],[]).append(e)
    out=[]
    for cat,lst in by.items():
        out.append(f'<h4 class="cat">{esc(cat)} <span class="n">{len(lst)}</span></h4>')
        for e in lst:
            cnt=e.get("count",1)
            badge=f'<span class="badge" title="so oft in den Chats aufgetaucht">{cnt}×</span>' if (cnt and cnt>1 and not spezial) else ''
            sp='<span class="sp">Spezialfall</span>' if spezial else ''
            out.append('<details class="q"><summary>'+sp+esc(e["frage"])+badge+'</summary><div class="a">'+rich(e["antwort"])+'</div></details>')
    return "\n".join(out)

def product_section(pid, title, sub, data):
    return f'''<section class="prod" id="{pid}" {'style="display:none"' if pid=="tool" else ''}>
<div class="ph"><h2>{title}</h2><p>{sub}</p></div>
<h3 class="bk">Häufige Fragen <span class="n">{len(data["faq"])}</span></h3>
{render_bucket(data["faq"])}
<h3 class="bk sp2">Spezialfälle <span class="n">{len(data["spezial"])}</span></h3>
<p class="hint">Seltene, aber lehrreiche Einzelfälle aus der Community.</p>
{render_bucket(data["spezial"], spezial=True)}
</section>'''

portal = product_section("portal","EEG Faktura – Online-Portal","Die Web-Anwendung (Login, Zählpunkte, Netzbetreiber-Prozesse, Abrechnung).", d["portal"])
tool   = product_section("tool","EEG Faktura Tool – Desktop","Das separate Desktop-Programm (.exe von stromregion.at, lokaler Import/Export, SEPA-Datenträger).", d["tool"])
tot = len(d["portal"]["faq"])+len(d["portal"]["spezial"])+len(d["tool"]["faq"])+len(d["tool"]["spezial"])

HTML=f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EEG Faktura – Häufige Fragen</title>
<script defer data-domain="faq.unsere-eeg.at" src="https://stats.solavia.at/js/script.js"></script>
<style>
:root{{--bg:#f5f7f6;--card:#fff;--ink:#18241f;--mut:#5b6a63;--acc:#2e7d5b;--line:#e4eae7;--sp:#8a5a1f;--spbg:#fdf3e5}}
*{{box-sizing:border-box}}body{{margin:0;font:16px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}}
header{{background:#2e7d5b;color:#fff;padding:34px 20px}}
.wrap{{max-width:840px;margin:0 auto;padding:0 20px}}
header h1{{margin:0 0 5px;font-size:1.6rem}}header p{{margin:0;opacity:.92;font-size:.95rem}}
.tabs{{display:flex;gap:8px;margin:18px 0 4px;flex-wrap:wrap}}
.tab{{flex:1;min-width:200px;padding:12px 16px;border:1px solid var(--line);background:var(--card);border-radius:12px;cursor:pointer;font-size:1rem;font-weight:600;color:var(--ink);text-align:left}}
.tab small{{display:block;font-weight:400;color:var(--mut);font-size:.8rem;margin-top:2px}}
.tab.on{{border-color:var(--acc);box-shadow:inset 0 0 0 1px var(--acc)}}
#s{{width:100%;padding:13px 16px;font-size:1rem;border:1px solid var(--line);border-radius:10px;margin:12px 0 4px}}
.ph{{margin:16px 0 6px}}.ph h2{{color:var(--acc);font-size:1.2rem;margin:0}}.ph p{{color:var(--mut);margin:3px 0 0;font-size:.9rem}}
.bk{{font-size:1.05rem;margin:22px 0 4px}}.bk.sp2{{color:var(--sp)}}
.cat{{font-size:.95rem;color:var(--acc);margin:16px 0 6px;border-bottom:2px solid var(--line);padding-bottom:4px}}
.cat .n,.bk .n{{color:var(--mut);font-weight:400;font-size:.8rem}}
.hint{{color:var(--mut);font-size:.85rem;margin:2px 0 8px}}
details.q{{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:7px 0}}
summary{{cursor:pointer;padding:12px 14px;font-weight:600;list-style:none;position:relative}}
summary::-webkit-details-marker{{display:none}}
summary:before{{content:"+";color:var(--acc);font-weight:700;margin-right:9px}}details[open] summary:before{{content:"–"}}
.a{{padding:0 14px 13px 30px;color:var(--mut)}}
.a p{{margin:0 0 8px}}.a p:last-child{{margin-bottom:0}}
.a ol,.a ul{{margin:4px 0 8px;padding-left:22px}}.a li{{margin:3px 0}}
.a code{{background:#eef3f0;border:1px solid var(--line);border-radius:5px;padding:1px 6px;font-size:.86em;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:#1f5c43}}
.a strong{{color:var(--ink)}}
.badge{{background:var(--acc);color:#fff;font-size:.72rem;padding:1px 7px;border-radius:10px;margin-left:8px;font-weight:600}}
.sp{{background:var(--spbg);color:var(--sp);font-size:.7rem;padding:1px 7px;border-radius:6px;margin-right:8px;font-weight:600}}
footer{{color:var(--mut);font-size:.82rem;text-align:center;padding:28px 20px 46px}}
footer a{{color:var(--acc);text-decoration:none;font-weight:600;display:inline-block;margin-top:10px}}
footer a:hover{{text-decoration:underline}}
.hide{{display:none}}
</style></head><body>
<header><div class="wrap"><h1>EEG Faktura – Häufige Fragen</h1>
<p>Automatisch aus den Community-Chats generiert &amp; per KI zusammengefasst · {tot} Einträge</p></div></header>
<main class="wrap">
<div class="tabs">
<button class="tab on" data-p="portal">EEG Faktura (Online-Portal)<small>{len(d['portal']['faq'])} häufige · {len(d['portal']['spezial'])} Spezialfälle</small></button>
<button class="tab" data-p="tool">EEG Faktura Tool (Desktop)<small>{len(d['tool']['faq'])} häufige · {len(d['tool']['spezial'])} Spezialfälle</small></button>
</div>
<input id="s" placeholder="🔎 Frage suchen … (z.B. Zählpunkt, Tarif, SEPA, abgelehnt, Update)">
{portal}
{tool}
</main>
<footer>{tot} Einträge · Stand {stamp} · Automatisch per KI aus den Community-Chats zusammengefasst und mit <strong>lokaler KI (on-premise)</strong> laufend aktuell gehalten. Ohne Gewähr.<br>
Kein offizieller Support. Bei individuellen Fällen: Netzbetreiber bzw. EEG-Faktura-Betreiber kontaktieren.<br>
<a href="https://unsere-eeg.at/">🌱 unsere-eeg.at — Energiegemeinschaft gründen &amp; betreiben</a></footer>
<script>
var tabs=document.querySelectorAll('.tab'),s=document.getElementById('s');
tabs.forEach(function(t){{t.onclick=function(){{tabs.forEach(x=>x.classList.remove('on'));t.classList.add('on');
var p=t.dataset.p;document.querySelectorAll('.prod').forEach(function(sec){{sec.style.display=sec.id===p?'':'none';}});s.value='';filter();}};}});
function filter(){{var q=s.value.toLowerCase().trim();
document.querySelectorAll('.prod').forEach(function(sec){{if(sec.style.display==='none')return;
sec.querySelectorAll('details.q').forEach(function(d){{d.classList.toggle('hide',q&&d.textContent.toLowerCase().indexOf(q)<0);}});
sec.querySelectorAll('.cat,.bk,.hint').forEach(function(h){{var n=h.nextElementSibling,v=false;while(n&&(n.tagName==='DETAILS'||n.classList.contains('hint'))){{if(n.tagName==='DETAILS'&&!n.classList.contains('hide'))v=true;n=n.nextElementSibling;}}if(h.classList.contains('cat'))h.classList.toggle('hide',q&&!v);}});}});}}
s.addEventListener('input',filter);
</script></body></html>"""
open(OUT,"w").write(HTML)
print("gebaut:",OUT,"|",tot,"Einträge")
