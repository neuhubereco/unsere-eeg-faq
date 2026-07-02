#!/usr/bin/env python3
# /root/faq/faq_update.py — woechentliche FAQ-Aktualisierung aus den EEG-WhatsApp-Gruppen.
# Alles ON-PREMISE: Extraktion qwen2.5:7b + Dedup via nomic-embed (ollama-maria :11460).
# Neue Fragen -> neuer Eintrag; bekannte Fragen -> count+1. Danach Rebuild + Deploy auf CT173.
import json, subprocess, urllib.request, datetime, sys, os, re, math, hashlib

BASE="/root/faq"; DB="/opt/wa-maria/data/florian.db"
OLLAMA="http://127.0.0.1:11460"
GEN="qwen2.5:7b"; EMBM="nomic-embed-text"
DRY="--dry-run" in sys.argv
CHUNK=60; SIM_THRESH=0.86; MIN_RAM_GB=10
GROUPS={  # name: (chat_id, produkt-prior)
 "community":("120363207226559363@g.us","portal"),
 "bedienung":("120363371299050578@g.us","portal"),
 "tarife":("120363371627947784@g.us","portal"),
 "neuefunktionen":("120363389871946581@g.us","portal"),
 "tool":("120363414838128548@g.us","tool"),
}
CATS=["Einrichtung","Mitglieder","Zählpunkte/Netzbetreiber","Abrechnung","Tarife","Steuer/USt","Fehler/Störung","Bedienung","Installation/Updates","Sonstiges"]

def log(*a): print(datetime.datetime.now().strftime("%H:%M:%S"),*a,flush=True)

def git(*args):
    r=subprocess.run(["git","-C",BASE]+list(args),capture_output=True,text=True,timeout=120)
    return r.returncode, (r.stdout+r.stderr).strip()

def ram_ok():
    kb=int([l for l in open("/proc/meminfo") if l.startswith("MemAvailable")][0].split()[1])
    return kb > MIN_RAM_GB*1024*1024

def fetch(cid, since):
    q="SELECT ts,from_me,text FROM messages WHERE chat_id='%s' AND ts>%d AND text<>'' ORDER BY ts"%(cid,since)
    p=subprocess.run(["pct","exec","171","--","sqlite3","-readonly","-json",DB,q],capture_output=True,text=True,timeout=120)
    try: return json.loads(p.stdout or "[]")
    except: return []

def api(path,payload,timeout=300):
    req=urllib.request.Request(OLLAMA+path,data=json.dumps(payload).encode(),headers={"content-type":"application/json"})
    return json.loads(urllib.request.urlopen(req,timeout=timeout).read())

def llm(prompt):
    r=api("/api/generate",{"model":GEN,"prompt":prompt,"stream":False,"keep_alive":"3m","format":"json","options":{"temperature":0,"num_ctx":4096}})
    return (r.get("response") or "").strip()

def embed(text):
    return api("/api/embeddings",{"model":EMBM,"prompt":text[:400]},timeout=60).get("embedding")

def cos(a,b):
    d=sum(x*y for x,y in zip(a,b)); na=math.sqrt(sum(x*x for x in a)); nb=math.sqrt(sum(x*x for x in b))
    return d/(na*nb) if na and nb else 0.0

def eid(produkt,frage):
    return hashlib.sha1((produkt+"|"+frage.lower().strip()).encode()).hexdigest()[:16]

PROMPT=("Du bekommst Nachrichten aus einer WhatsApp-Support-Gruppe rund um 'EEG Faktura' (Software fuer Energiegemeinschaften in Oesterreich).\n"
 "ZWEI Produkte: produkt='portal' = Online-Webanwendung (Login, Zaehlpunkte, Netzbetreiber-Prozesse, Abrechnung im Portal); "
 "produkt='tool' = separates Desktop-Programm (.exe von stromregion.at, Versionen 1.8.x/2.x, lokaler Excel/SEPA-Export).\n"
 "Extrahiere NUR beantwortete, fuer andere nuetzliche Fragen/Probleme mit der gegebenen Loesung. KEIN Small-Talk, nichts Unbeantwortetes, keine rein privaten Einmal-Faelle.\n"
 "typ='haeufig' (allgemeine wiederkehrende Frage) oder typ='spezialfall' (seltener, aber lehrreicher Einzelfall).\n"
 "kategorie aus: "+", ".join(CATS)+".\n"
 "Die 'antwort' formatiere als knappes HTML mit NUR diesen Tags: <p>,<ol>,<ul>,<li>,<strong>,<code> "
 "(Menuepfade wie Prozesse -> X als <code>Prozesse → X</code>; Schritt-Anleitungen als <ol>). Max ~3 Saetze Inhalt.\n"
 'Antworte NUR als JSON: {"faqs":[{"frage":"...","antwort":"<p>...</p>","produkt":"portal","typ":"haeufig","kategorie":"..."}]}. Leer wenn nichts.\n\n'
 "Nachrichten (A=Betreiber, U=Nutzer):\n")

def main():
    if not ram_ok(): log("ABBRUCH: <%d GB RAM frei"%MIN_RAM_GB); return
    # Community-PRs / Remote-Aenderungen zuerst reinziehen
    rc,out=git("pull","--rebase","origin","main"); log("git pull:",out[:120] or "ok")
    state=json.load(open(BASE+"/state.json"))
    data=json.load(open(BASE+"/faq_data.json"))
    try: embs=json.load(open(BASE+"/embeddings.json"))
    except: embs={}
    # Kandidaten sammeln
    cands=[]
    for g,(cid,prior) in GROUPS.items():
        since=state.get(g,0); rows=fetch(cid,since)
        if rows: state[g]=max(r["ts"] for r in rows)
        log(f"[{g}] {len(rows)} neue Nachrichten")
        i=0
        while i<len(rows):
            chunk=rows[i:i+CHUNK]; i+=CHUNK
            block="\n".join(("A" if r["from_me"]==1 else "U")+": "+(r["text"] or "").replace("\n"," ")[:250] for r in chunk)
            try:
                obj=json.loads(llm(PROMPT+block))
                for f in (obj.get("faqs") or []):
                    if isinstance(f,dict) and f.get("frage") and f.get("antwort"):
                        f["produkt"]=f.get("produkt") if f.get("produkt") in ("portal","tool") else prior
                        cands.append(f)
            except Exception as e: log(f"  chunk-err: {e}")
    log(f"Kandidaten: {len(cands)}")
    added=merged=0
    if cands:
        # bestehende Embeddings lazy nachziehen
        for prod in ("portal","tool"):
            for bucket in ("faq","spezial"):
                for e in data[prod][bucket]:
                    e.setdefault("id",eid(prod,e["frage"]))
                    if e["id"] not in embs:
                        v=embed(e["frage"])
                        if v: embs[e["id"]]=v
        for c in cands:
            prod=c["produkt"]
            v=embed(c["frage"])
            if not v: continue
            best=0.0; bestE=None
            for bucket in ("faq","spezial"):
                for e in data[prod][bucket]:
                    s=cos(v,embs.get(e["id"],[]))
                    if s>best: best,bestE=s,e
            if best>=SIM_THRESH and bestE is not None:
                bestE["count"]=bestE.get("count",1)+1; merged+=1
            else:
                bucket="spezial" if c.get("typ")=="spezialfall" else "faq"
                ne={"frage":c["frage"][:300],"antwort":c["antwort"][:1500],
                    "kategorie":c.get("kategorie") if c.get("kategorie") in CATS else "Sonstiges",
                    "count":1,"added":datetime.date.today().isoformat()}
                ne["id"]=eid(prod,ne["frage"])
                data[prod][bucket].append(ne); embs[ne["id"]]=v; added+=1
    log(f"neu: {added}, hochgezaehlt: {merged}")
    if DRY: log("(DRY-RUN — nichts gespeichert/deployt)"); return
    json.dump(state,open(BASE+"/state.json","w"))
    # kanonisch: sortiert (Kategorie, count absteigend, Frage) + pretty — PR-freundliche, stabile Diffs
    def _key(e): return (CATS.index(e["kategorie"]) if e["kategorie"] in CATS else 99, -e.get("count",1), e["frage"].lower())
    for _p in ("portal","tool"):
        for _b in ("faq","spezial"):
            data[_p][_b]=sorted(data[_p][_b],key=_key)
    open(BASE+"/faq_data.json","w").write(json.dumps(data,ensure_ascii=False,indent=2)+"\n")
    json.dump(embs,open(BASE+"/embeddings.json","w"))
    # Aenderungen ins Repo zurueck (Community sieht/reviewt sie dort)
    if added or merged:
        git("add","faq_data.json")
        rc,_=git("commit","-m","auto-update: +%d neu, %d hochgezählt (%s)"%(added,merged,datetime.date.today().isoformat()))
        if rc==0:
            rc,out=git("push","origin","main"); log("git push:","ok" if rc==0 else out[:120])
    # Rebuild + Deploy (Stand-Datum = heute)
    stamp=datetime.date.today().strftime("%d.%m.%Y")
    r=subprocess.run(["python3",BASE+"/faq_build2.py",BASE+"/faq_data.json","/tmp/faq_index.html",stamp],capture_output=True,text=True)
    log(r.stdout.strip() or r.stderr.strip())
    subprocess.run(["pct","push","173","/tmp/faq_index.html","/var/www/faq/index.html"],check=True,timeout=60)
    os.remove("/tmp/faq_index.html")
    log("DEPLOYED auf CT173")

if __name__=="__main__": main()
