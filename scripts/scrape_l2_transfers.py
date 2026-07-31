#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scrape des transferts Ligue 2 (Transfermarkt) -> régénère index.html (+ equipe.html)
Même fonctionnement que le pipeline Ligue 1 (scrape_l1_transfers.py).

- Arrivées à gauche, départs à droite, montants inclus.
- Retire les RETOURS DE PRÊT ("Fin du prêt ...").
- Retire les joueurs venant/allant d'une ÉQUIPE B / réserve / catégorie jeunes
  SANS montant. Les achats payants d'un jeune sont conservés.
- Détecte les nouvelles arrivées à chaque passage (diff de snapshot) pour
  alimenter les cases "Dernières arrivées" en haut de page.

Usage :
    python scripts/scrape_l2_transfers.py            # scrape en ligne + régénère
    python scripts/scrape_l2_transfers.py --offline  # réutilise le cache HTML
"""
import os, re, sys, json, time, html as _html
from datetime import date, datetime
import requests
from bs4 import BeautifulSoup

# console Windows -> UTF-8 (évite les plantages de print sur cp1252)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT   = r"C:\Users\Youss\Documents\mercato-l2-repo"          # repo dédié L2
CACHE  = os.path.join(SCRIPT_DIR, ".cache_l2")
DATA   = os.path.join(ROOT, "data")
TPL    = os.path.join(SCRIPT_DIR, "mercato_l2_template.html")
OUT    = os.path.join(ROOT, "index.html")
TPL_TEAM = os.path.join(SCRIPT_DIR, "equipe_l2_template.html")
OUT_TEAM = os.path.join(ROOT, "equipe.html")
LOGOS_DIR = os.path.join(ROOT, "logos")
STATE  = os.path.join(DATA, "transfers_l2.json")   # snapshot + historique des dates
SEASON = 2026
LATEST_DAYS = 7      # fenêtre "dernières arrivées"
LATEST_MAX  = 10     # nb de cases max
os.makedirs(CACHE, exist_ok=True)
os.makedirs(DATA,  exist_ok=True)
os.makedirs(LOGOS_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# code, nom, slug transfermarkt, id, couleur fond, couleur texte  (ordre d'affichage)
CLUBS = [
    ("ASSE",   "AS Saint-Étienne",       "as-saint-etienne",       618,   "#2fac66", "#fff"),
    ("FCN",    "FC Nantes",              "fc-nantes",              995,   "#fdff00", "#00843d"),
    ("REIMS",  "Stade de Reims",         "stade-reims",            1421,  "#e2001a", "#fff"),
    ("FCM",    "FC Metz",                "fc-metz",                347,   "#7a1f2b", "#fff"),
    ("MHSC",   "Montpellier Hérault SC", "montpellier-hsc",        969,   "#004494", "#ff6600"),
    ("CF63",   "Clermont Foot 63",       "clermont-foot-63",       3524,  "#0c3c74", "#fff"),
    ("RED",    "Red Star FC",            "red-star-fc",            1154,  "#007a3d", "#fff"),
    ("RAF",    "Rodez AF",               "rodez-af",               11273, "#ba0c2f", "#fff"),
    ("PAU",    "Pau FC",                 "pau-fc",                 3166,  "#002654", "#f8b400"),
    ("USLD",   "USL Dunkerque",          "usl-dunkerque",          9202,  "#001e50", "#e2001a"),
    ("GF38",   "Grenoble Foot 38",       "grenoble-foot-38",       1290,  "#e2001a", "#002654"),
    ("EAG",    "EA Guingamp",            "ea-guingamp",            855,   "#e2001a", "#000"),
    ("NANCY",  "AS Nancy-Lorraine",      "as-nancy-lorraine",      1159,  "#d0021b", "#fff"),
    ("ANNECY", "FC Annecy",              "fc-annecy",              30204, "#cc0000", "#fff"),
    ("LAVAL",  "Stade Lavallois",        "stade-laval",            1080,  "#002f6c", "#000"),
    ("BOUL",   "US Boulogne",            "us-boulogne",            7042,  "#002458", "#e2001a"),
    ("SOCH",   "FC Sochaux-Montbéliard", "fc-sochaux-montbeliard", 750,   "#ffd100", "#002f6c"),
    ("DFCO",   "Dijon FCO",              "dijon-fco",              2969,  "#ffffff", "#e2001a"),
]

POS = {
    "Gardien de but": "GdB", "Arrière droit": "ArD", "Arrière gauche": "ArG",
    "Défenseur central": "DC", "Milieu défensif": "MDC", "Milieu central": "MC",
    "Milieu offensif": "MO", "Milieu droit": "MD", "Milieu gauche": "MG",
    "Ailier droit": "AiD", "Ailier gauche": "AiG", "Avant-centre": "AC",
    "Second attaquant": "SA", "Attaquant": "AC", "Défenseur": "DC", "Milieu": "MC",
}
def pos_short(full):
    full = full.strip()
    if full in POS: return POS[full]
    # fallback : initiales des mots
    return "".join(w[0] for w in full.split()[:3]).upper()[:4] or "?"

# groupe de poste pour l'effectif (GK / DEF / MID / ATT)
GRP = {
    "Gardien de but": "GK",
    "Arrière droit": "DEF", "Arrière gauche": "DEF", "Défenseur central": "DEF", "Défenseur": "DEF",
    "Milieu défensif": "MID", "Milieu central": "MID", "Milieu offensif": "MID",
    "Milieu droit": "MID", "Milieu gauche": "MID", "Milieu": "MID",
    "Ailier droit": "ATT", "Ailier gauche": "ATT", "Avant-centre": "ATT",
    "Second attaquant": "ATT", "Attaquant": "ATT",
}
def pos_group(full):
    return GRP.get(full.strip(), "MID")

# --- détection équipe B / réserve / jeunes ---
RESERVE_RE = re.compile(
    r"(\bB\b|\bII\b|\bU-?1\d\b|\bU-?2\d\b|Futuro|R[ée]serve|Youth|Juniores|Primavera|Akademi)",
    re.I)
def is_reserve(club):
    return bool(RESERVE_RE.search(club or ""))

# --- parsing du montant ---
def parse_fee(raw):
    """retourne (type, val_millions, is_loan_return)
       type ∈ {paid, loan, free, none}"""
    t = (raw or "").strip()
    low = t.lower()
    if "fin du prêt" in low or "fin de prêt" in low or "fin du pret" in low:
        return ("none", 0.0, True)          # retour de prêt -> à retirer
    # nombre + unité
    val = 0.0
    m = re.search(r"([\d.,]+)\s*(mio|th|k)\b", low)
    if m:
        num = float(m.group(1).replace(".", "").replace(",", ".")) \
              if m.group(1).count(",") else float(m.group(1).replace(",", "."))
        unit = m.group(2)
        val = num if unit == "mio" else num / 1000.0
    if "prêt" in low or "pret" in low or "loan" in low:
        return ("loan", val, False)
    if m:
        return ("paid", val, False)
    if "libre" in low or "free" in low:
        return ("free", 0.0, False)
    return ("none", 0.0, False)

def fetch_url(fn, url, offline=False, label=""):
    if offline and os.path.exists(fn):
        return open(fn, "rb").read()
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            if r.status_code == 200 and len(r.content) > 5000:
                open(fn, "wb").write(r.content)
                return r.content
            print(f"  ! HTTP {r.status_code} pour {label or url} (essai {attempt+1})")
        except Exception as e:
            print(f"  ! {label or url} : {e} (essai {attempt+1})")
        time.sleep(2 + attempt * 2)
    if os.path.exists(fn):
        print(f"  -> cache utilisé pour {label or url}")
        return open(fn, "rb").read()
    return None

def fetch(slug, cid, offline=False):
    fn  = os.path.join(CACHE, f"{cid}.html")
    url = f"https://www.transfermarkt.fr/{slug}/transfers/verein/{cid}/saison_id/{SEASON}"
    return fetch_url(fn, url, offline, slug)

def vid(td):
    """id du club (verein) depuis le lien d'une cellule, ou None"""
    a = td.find("a", href=re.compile(r"/verein/\d+"))
    return int(re.search(r"/verein/(\d+)", a["href"]).group(1)) if a else None

# --- meta clubs par id + logos base64 ---
L1META = {cid: (code, name, bg, fg) for code, name, slug, cid, bg, fg in CLUBS}

def load_logo(code):
    for ext in ("png", "svg", "webp", "jpg", "jpeg"):
        p = os.path.join(ROOT, "logos", f"{code}.{ext}")
        if os.path.exists(p):
            mime = "image/svg+xml" if ext == "svg" else \
                   ("image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}")
            import base64
            b64 = base64.b64encode(open(p, "rb").read()).decode()
            return f"data:{mime};base64,{b64}"
    return ""
LOGOS = {code: load_logo(code) for code, *_ in CLUBS}

def fetch_latest(offline=False):
    """arrivées L1 les plus récentes (page 'derniers transferts', triée par date)"""
    fn  = os.path.join(CACHE, "latest.html")
    url = ("https://www.transfermarkt.fr/transfers/neuestetransfers/statistik"
           "?land_id=0&wettbewerb_id=FR2&minMarktwert=0&maxMarktwert=500000000")
    raw = fetch_url(fn, url, offline, "derniers transferts")
    if not raw:
        return []
    soup = BeautifulSoup(raw, "lxml", from_encoding="utf-8")
    t = soup.select_one("table.items")
    out = []
    for tr in (t.select("tbody > tr") if t else []):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 6:
            continue
        dst = vid(tds[4])
        if dst not in L1META:                       # on ne garde que les arrivées vers la L1
            continue
        namea = tds[0].select_one(".hauptlink a") or tds[0].find("a")
        if not namea:
            continue
        name = (namea.get("title") or namea.get_text(strip=True)).strip()
        inner = tds[0].find_all("tr")
        pos_full = inner[-1].get_text(" ", strip=True) if inner else ""
        age = re.sub(r"\D", "", tds[1].get_text(strip=True))[:2]
        frm_full = tds[3].get_text(" ", strip=True)     # inclut "U19"/"B" -> filtrage réserve
        frm = club_name_from_cell(tds[3])               # nom du club (le 1er <a> est le logo, vide)
        typ, val, loan_ret = parse_fee(tds[5].get_text(" ", strip=True))
        if loan_ret:
            continue
        if is_reserve(frm_full) and typ in ("free", "none", "loan"):
            continue                                # promu équipe B / jeune sans transfert
        code, cn, bg, fg = L1META[dst]
        out.append({"p": name, "age": int(age) if age else 0, "pos": pos_short(pos_full),
                    "club": frm, "type": typ, "val": round(val, 3),
                    "club_code": code, "club_name": cn, "bg": bg, "fg": fg})
    return out

def club_name_from_cell(td):
    for a in td.find_all("a"):
        txt = a.get_text(strip=True)
        if txt:
            return txt
    txt = td.get_text(" ", strip=True)
    # retire un éventuel nom de compétition en fin (span détaché)
    return txt or "Sans club"

def parse_side(rt):
    """parse une responsive-table -> liste de dict transferts bruts"""
    out = []
    tbody = rt.find("table").find("tbody")
    if not tbody:
        return out
    for tr in tbody.find_all("tr", recursive=False):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 6:
            continue
        namea = tds[1].select_one(".hauptlink a") or tds[1].find("a")
        if not namea:
            continue
        name = (namea.get("title") or namea.get_text(strip=True)).strip()
        # poste = dernière ligne de l'inline-table du joueur
        pos_full = ""
        inner_rows = tds[1].find_all("tr")
        if inner_rows:
            pos_full = inner_rows[-1].get_text(" ", strip=True)
        age  = re.sub(r"\D", "", tds[2].get_text(strip=True))[:2]
        club = club_name_from_cell(tds[4])
        # nettoie le nom de club (retire compétition collée si présente)
        fee_raw = tds[5].get_text(" ", strip=True)
        out.append({
            "p": name, "age": int(age) if age else 0,
            "pos": pos_short(pos_full), "club": club, "feeRaw": fee_raw,
        })
    return out

def clean(rows):
    """applique règles : retire retours de prêt + jeunes/réserve sans montant"""
    res = []
    for t in rows:
        typ, val, loan_ret = parse_fee(t["feeRaw"])
        if loan_ret:
            continue                                   # retour de prêt
        if is_reserve(t["club"]) and typ in ("free", "none", "loan"):
            continue                                   # promu équipe B / jeune sans transfert
        res.append({
            "p": t["p"], "age": t["age"], "pos": t["pos"], "club": t["club"],
            "type": typ, "val": round(val, 3),
        })
    return res

def scrape(offline=False):
    clubs_data = []
    for code, name, slug, cid, bg, fg in CLUBS:
        print(f"· {name} …")
        raw = fetch(slug, cid, offline)
        if not raw:
            print(f"  !! échec total {name}, club ignoré ce passage")
            clubs_data.append({"code": code, "name": name, "bg": bg, "fg": fg,
                               "arr": [], "dep": [], "failed": True})
            continue
        soup = BeautifulSoup(raw, "lxml", from_encoding="utf-8")
        rts = soup.select("div.responsive-table")
        arr = clean(parse_side(rts[0])) if len(rts) > 0 else []
        dep = clean(parse_side(rts[1])) if len(rts) > 1 else []
        clubs_data.append({"code": code, "name": name, "bg": bg, "fg": fg,
                           "arr": arr, "dep": dep})
        if not offline:
            time.sleep(1.2)
    return clubs_data

# ---------- effectifs (kader) ----------
def scrape_squad(slug, cid, offline=False):
    fn  = os.path.join(CACHE, f"kader_{cid}.html")
    url = f"https://www.transfermarkt.fr/{slug}/kader/verein/{cid}/saison_id/{SEASON}/plus/1"
    raw = fetch_url(fn, url, offline, slug + " kader")
    if not raw:
        return []
    soup = BeautifulSoup(raw, "lxml", from_encoding="utf-8")
    t = soup.select_one("table.items")
    players = []
    for tr in (t.select("tbody > tr.odd, tbody > tr.even") if t else []):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 3:
            continue
        nm = tds[1].select_one(".hauptlink a")
        if not nm:
            continue
        name = (nm.get("title") or nm.get_text(strip=True)).strip()
        inner = tds[1].select("tr")
        pos_full = inner[-1].get_text(" ", strip=True) if inner else ""
        num = tds[0].get_text(strip=True)
        num = "" if num in ("-", "") else num
        m = re.search(r"\((\d+)\)", tds[2].get_text())
        age = int(m.group(1)) if m else 0
        _, val, _ = parse_fee(tds[-1].get_text(" ", strip=True))
        players.append({"n": num, "p": name, "pos": pos_short(pos_full),
                        "grp": pos_group(pos_full), "age": age, "val": round(val, 2)})
    return players

def scrape_squads(offline=False, old=None):
    squads = {}
    old_sq = (old or {}).get("squads", {})
    for code, name, slug, cid, bg, fg in CLUBS:
        print(f"· effectif {name} …")
        pl = scrape_squad(slug, cid, offline)
        if not pl and code in old_sq:               # échec -> dernière version connue
            print(f"  ↻ {name} : effectif précédent réutilisé")
            pl = old_sq[code]
        squads[code] = pl
        if not offline:
            time.sleep(1.0)
    return squads

def generate_teams(squads):
    tpl = open(TPL_TEAM, encoding="utf-8").read()
    meta = {code: {"name": name, "bg": bg, "fg": fg}
            for code, name, slug, cid, bg, fg in CLUBS}
    order = [code for code, *_ in CLUBS]
    logocss, logocodes = logo_assets()
    out = (tpl
           .replace("/*__SQUADS__*/{}", json.dumps(squads, ensure_ascii=False))
           .replace("/*__CLUBMETA__*/{}", json.dumps(meta, ensure_ascii=False))
           .replace("/*__ORDER__*/[]", json.dumps(order))
           .replace("/*__LOGOCODES__*/[]", json.dumps(sorted(logocodes)))
           .replace("/*__LOGOCSS__*/", logocss))
    open(OUT_TEAM, "w", encoding="utf-8").write(out)
    n = sum(len(v) for v in squads.values())
    print(f"✓ {OUT_TEAM} régénéré — {n} joueurs sur {len(squads)} effectifs")

# ---------- diff / dates ----------
def key(club_code, direction, t):
    return f"{club_code}|{direction}|{t['p']}|{t['club']}"

def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            pass
    return {}

def signature(clubs_data):
    """empreinte des DONNÉES (hors horodatage) pour détecter un vrai changement"""
    import hashlib
    def norm(lst):
        return sorted([[t["p"], t["club"], t["type"], t["val"]] for t in lst])
    canon = [[c["code"], "FAILED" if c.get("failed") else norm(c["arr"]),
              [] if c.get("failed") else norm(c["dep"])] for c in clubs_data]
    return hashlib.md5(json.dumps(canon, ensure_ascii=False, sort_keys=True)
                       .encode("utf-8")).hexdigest()

def restore_failed(clubs_data, old):
    """réutilise la dernière version connue d'un club qui a échoué au scrape"""
    old_by = {c["code"]: c for c in old.get("clubs", [])}
    for i, c in enumerate(clubs_data):
        if c.get("failed") and c["code"] in old_by:
            oc = old_by[c["code"]]
            clubs_data[i] = {**c, "arr": oc.get("arr", []), "dep": oc.get("dep", []),
                             "failed": False, "restored": True}
            print(f"  ↻ {c['name']} : réutilisation de la dernière version connue")

def apply_history(clubs_data, old):
    """date chaque transfert (first_seen), écrit le snapshot, renvoie (seen, sig)"""
    today = date.today().isoformat()
    prev = old.get("seen", {})
    seen = {}
    for c in clubs_data:
        if c.get("failed"):
            continue
        for direction, lst in (("in", c["arr"]), ("out", c["dep"])):
            for t in lst:
                k = key(c["code"], direction, t)
                fs = prev.get(k, today)
                seen[k] = fs
                t["fs"] = fs
    sig = signature(clubs_data)
    json.dump({"updated": datetime.now().isoformat(timespec="seconds"),
               "sig": sig, "seen": seen, "clubs": clubs_data,
               "squads": SQUADS_CACHE},
              open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return seen, sig

def build_latest(recent_arr, seen):
    """dernières arrivées : ordre chronologique Transfermarkt, datées via l'historique"""
    today = date.today().isoformat()
    for t in recent_arr:
        k = key(t["club_code"], "in", t)
        t["fs"] = seen.get(k, today)
    return recent_arr[:LATEST_MAX]

# ---------- génération HTML ----------
def logo_assets():
    """CSS (un data-URI par club) + liste des codes ayant un logo — évite de
       répéter l'image base64 à chaque usage du crest."""
    css, codes = [], []
    for code, uri in LOGOS.items():
        if uri:
            codes.append(code)
            css.append(f".crest.l-{code}{{background-image:url(\"{uri}\");}}")
    return "\n".join(css), codes

def generate(clubs_data, latest):
    tpl = open(TPL, encoding="utf-8").read()
    updated = datetime.now().strftime("%d/%m/%Y à %Hh%M")
    tag = "du + récent au + ancien"
    logocss, logocodes = logo_assets()
    out = (tpl
           .replace("/*__CLUBS__*/[]", json.dumps(clubs_data, ensure_ascii=False))
           .replace("/*__LATEST__*/[]", json.dumps(latest, ensure_ascii=False))
           .replace("/*__LOGOCODES__*/[]", json.dumps(codes_sorted(logocodes)))
           .replace("/*__LOGOCSS__*/", logocss)
           .replace("__LATEST_LABEL__", tag)
           .replace("__UPDATED__", updated))
    open(OUT, "w", encoding="utf-8").write(out)
    print(f"\n✓ {OUT} régénéré — MAJ {updated}")

def codes_sorted(codes):
    return sorted(codes)

SQUADS_CACHE = {}

def main():
    global SQUADS_CACHE
    offline = "--offline" in sys.argv
    teams_only = "--teams-only" in sys.argv     # ne régénère que la page effectifs
    print(f"Scrape Ligue 2 {SEASON}/{SEASON+1} {'(cache)' if offline else '(en ligne)'}\n")
    old = load_state()
    old_sig = old.get("sig")

    SQUADS_CACHE = scrape_squads(offline, old)
    generate_teams(SQUADS_CACHE)
    if teams_only:
        # on garde les effectifs déjà en base + snapshot inchangé
        return

    clubs_data = scrape(offline)
    restore_failed(clubs_data, old)
    print("· Dernières arrivées …")
    recent_arr = fetch_latest(offline)
    seen, sig = apply_history(clubs_data, old)
    latest = build_latest(recent_arr, seen)
    na = sum(len(c["arr"]) for c in clubs_data)
    nd = sum(len(c["dep"]) for c in clubs_data)
    print(f"\nTotal : {na} arrivées, {nd} départs, {len(latest)} cases 'dernières arrivées'.")
    generate(clubs_data, latest)

    changed = (sig != old_sig)
    open(os.path.join(DATA, ".push"), "w", encoding="utf-8").write("1" if changed else "0")
    print("→ Données " + ("MODIFIÉES : publication." if changed else "inchangées : pas de publication."))

if __name__ == "__main__":
    main()
