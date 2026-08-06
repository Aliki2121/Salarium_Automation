"""
salarium_api.py — client direct de l'API Salarium. Remplace le scraper.

Endpoints confirmes par capture reseau :
    GET  /api/Data/code/{regions|management-levels|education-levels|
                         company-sizes|permits|genders|nogas|
                         occupational-groups|has-bonuses|
                         has-thirteen-salaries|has-hour-contracts}
    GET  /api/Data/professions/fr        -> index de recherche (metier -> groupe)
    GET  /api/Data/qualities             -> qualite par (nogaId, regionCode)
    GET  /api/Calculation/consistent/{noga}/{region}/{group}   -> true|false
    GET  /api/Calculation/default/{noga}/{region}/{group}      -> criteres par defaut
    POST /api/Calculation/calculate-salary                     -> Q1 / mediane / Q3

Reponse du calcul :
    {"statisticalResults":[{"code":25,...},{"code":50,...},{"code":75,...}],
     "quality":3}
    code 25 = Q1, 50 = mediane, 75 = Q3.

Deux modes :
    py salarium_api.py --check
        Verifie que les listes de codes de l'API correspondent aux mappings
        ci-dessous.

    py salarium_api.py --validate <fichier.xlsx> [--n 3]
        Rejoue des lignes deja presentes dans l'onglet RESULTATS et compare les
        montants renvoyes par l'API a ceux du fichier. C'est CE test qui prouve
        que les mappings libelle -> code sont justes : tant qu'il n'est pas vert,
        ne relance pas d'extraction.

    py salarium_api.py --run <combinaisons.csv> --out resultats.csv
        Extraction complete. Le CSV d'entree doit contenir les colonnes :
        identifiant, nogaId, region, profession, position, formation, sexe,
        nationalite, taille, treizieme, paiements, type_contrat,
        horaire_hebdo, age_start
"""

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
from typing import Optional

import requests

BASE = "https://www.salarium.bfs.admin.ch"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": f"{BASE}/",
    "Origin": BASE,
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"),
}

DELAY = 0.35          # pause entre deux appels — ne pas descendre plus bas
RETRIES = 2           # reessais sur erreur reseau ou 5xx
AGE_MAX = 65
TIMEOUT = 30


def _norm(s) -> str:
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# ---------------------------------------------------------------------------
# Mappings libelle -> code.
# Les codes valides sont ceux renvoyes par /api/Data/code/* :
#   regions [1..7] · management-levels [1,2,3] · education-levels [1..8]
#   company-sizes [1,2,3] · permits [1..5] · genders [0,1] · yes/no [0,1]
# Les codes formation viennent directement de l'onglet COMBINAISONS_SALARIUM
# (« 6 – Formation professionnelle achevee, CFC/AFP »), et le POST capture
# montrait bien educationLevelCode 6 puis 3 : coherent.
# TOUT CECI RESTE A VALIDER PAR --validate.
# ---------------------------------------------------------------------------

REGIONS = [
    (["lemanique"], 1),
    (["mittelland"], 2),
    (["nord ouest"], 3),
    (["zurich"], 4),
    (["orientale"], 5),
    (["centrale"], 6),
    (["tessin", "ticino"], 7),
]

MANAGEMENT = [
    (["1 2", "1 + 2", "cadre superieur"], 1),
    (["3 4", "3 + 4", "cadre inferieur", "responsable de l execution"], 2),
    (["sans fonction de cadre"], 3),
]

EDUCATION = [   # ordre important : le plus specifique d'abord
    (["universitaire", "uni epf", "epf"], 1),
    (["specialisee", "hes", "hep", "pedagogique"], 2),
    (["professionnelle superieure", "brevet", "diplome federal"], 3),
    (["ecole normale"], 4),
    (["maturite"], 5),
    (["apprentissage complet", "cfc", "afp", "professionnelle achevee"], 6),
    (["acquise en entreprise"], 7),
    (["sans formation"], 8),
]

SIZES = [
    (["moins de 20"], 1),
    (["20 49", "20 a 49", "20 49 employes"], 2),
    (["50"], 3),
]

PERMITS = [
    (["suisse"], 1),
    (["courte duree", "cat l"], 2),
    (["permis de sejour", "cat b"], 3),
    (["etablissement", "cat c"], 4),
    (["frontalier", "cat g"], 5),
]

GENDERS = [(["homme", "male"], 0), (["femme", "female"], 1)]
YESNO = [(["oui", "yes", "1"], 1), (["non", "no", "0"], 0)]
HOUR_CONTRACT = [(["horaire"], 1), (["mensuel"], 0)]

# Codes de groupes de professions reellement acceptes (/api/Data/code/
# occupational-groups). Un code hors de cette liste doit lever une erreur
# plutot que partir dans un appel qui renverra n'importe quoi.
VALID_GROUPS = {10, 20, 21, 22, 23, 24, 25, 26, 30, 31, 32, 33, 34, 35, 41, 42,
                43, 44, 50, 51, 52, 53, 54, 61, 62, 70, 71, 72, 73, 74, 75, 80,
                81, 82, 83, 90, 91, 92, 93, 94, 96}

# Les directeurs sont agreges sous 10 dans l'API. Selon la feuille, ils sont
# libelles « 11-14. Directeurs, cadres de direction… » (RESULTATS) ou
# « 11 – Directeurs generaux… » (COMBINAISONS_SALARIUM) : aucun des deux
# prefixes n'est un code valide.
GROUP_ALIASES = {11: 10, 12: 10, 13: 10, 14: 10}


def _lookup(table, label, field) -> int:
    n = _norm(label)
    for keys, code in table:
        for k in keys:
            if _norm(k) in n:
                return code
    raise ValueError(f"{field} : libelle non reconnu « {label} »")


def _leading_int(label) -> Optional[int]:
    """Premier nombre du libelle : « 6 - Formation… » -> 6, « 3+4- Cadre » -> 3."""
    m = re.match(r"^\s*(\d+)", str(label or "").strip())
    return int(m.group(1)) if m else None


def group_code(label) -> int:
    code = _leading_int(label)
    if code is None:
        raise ValueError(f"groupe de professions : prefixe absent « {label} »")
    code = GROUP_ALIASES.get(code, code)
    if code not in VALID_GROUPS:
        raise ValueError(
            f"groupe de professions : code {code} absent de la liste de l'API "
            f"« {label} »")
    return code


def education_code(label) -> int:
    """Le prefixe numerique des libelles de COMBINAISONS_SALARIUM
    (« 6 - Formation professionnelle achevee, CFC/AFP ») EST le code de l'API.
    On le prend en priorite : le libellé « 5 - Maturite gymnasiale,
    professionnelle ou specialisee » contient « specialisee » et serait sinon
    confondu avec la haute ecole specialisee (code 2)."""
    code = _leading_int(label)
    if code is not None and 1 <= code <= 8:
        return code
    return _lookup(EDUCATION, label, "formation")


def management_code(label) -> int:
    """« 1+2 » / « 1-2 » -> 1, « 3+4 » / « 3-4 » -> 2, « 5 » -> 3.
    L'API n'accepte que [1,2,3] : les codes ne sont pas les chiffres du libelle."""
    code = _leading_int(label)
    if code in (1, 2):
        return 1
    if code in (3, 4):
        return 2
    if code == 5:
        return 3
    return _lookup(MANAGEMENT, label, "position")


def noga_id(label) -> int:
    code = _leading_int(label)
    if code is None:
        raise ValueError(f"branche : prefixe absent « {label} »")
    return code


def build_payload(row: dict, age: int, work_year: int) -> dict:
    return {
        "nogaId": noga_id(row["branche"]),
        "regionCode": _lookup(REGIONS, row["region"], "region"),
        "occupationalGroupCode": group_code(row["profession"]),
        "managementLevelCode": management_code(row["position"]),
        "educationLevelCode": education_code(row["formation"]),
        "companySizeCode": _lookup(SIZES, row["taille"], "taille"),
        "permitCode": _lookup(PERMITS, row["nationalite"], "nationalite"),
        "genderCode": _lookup(GENDERS, row["sexe"], "sexe"),
        "hasThirteenSalaryCode": _lookup(YESNO, row["treizieme"], "13e"),
        "hasBonusCode": _lookup(YESNO, row["paiements"], "paiements"),
        "hasHourContractCode": _lookup(HOUR_CONTRACT, row["type_contrat"], "contrat"),
        "workHourValue": float(row["horaire_hebdo"]),
        "ageCode": int(age),
        "workYearCode": int(work_year),
    }


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class Salarium:
    def __init__(self, delay: float = DELAY):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.delay = delay
        self._consistent: dict = {}

    def _get(self, path: str):
        last = None
        for essai in range(RETRIES + 1):
            time.sleep(self.delay * (1 + essai))
            try:
                r = self.s.get(f"{BASE}{path}", timeout=TIMEOUT)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last = e
        raise last

    def codes(self, name: str):
        return self._get(f"/api/Data/code/{name}")

    def professions(self, lang: str = "fr"):
        return self._get(f"/api/Data/professions/{lang}")

    def consistent(self, noga: int, region: int, group: int) -> bool:
        key = (noga, region, group)
        if key not in self._consistent:
            try:
                self._consistent[key] = bool(
                    self._get(f"/api/Calculation/consistent/{noga}/{region}/{group}"))
            except Exception:
                self._consistent[key] = False
        return self._consistent[key]

    def calculate(self, payload: dict) -> dict:
        """-> {'q1':…, 'mediane':…, 'q3':…, 'quality':…, 'erreur':…}"""
        out = {"q1": None, "mediane": None, "q3": None, "quality": None, "erreur": ""}

        # Reessais avec pause croissante : un 502/503 passager ou une coupure
        # reseau ne doit pas laisser un trou dans les donnees. Une reponse 4xx
        # n'est pas reessayee : le payload est en cause, pas le reseau.
        r = None
        for essai in range(RETRIES + 1):
            time.sleep(self.delay * (1 + essai))
            try:
                r = self.s.post(f"{BASE}/api/Calculation/calculate-salary",
                                data=json.dumps(payload), timeout=TIMEOUT)
            except Exception as e:
                out["erreur"] = f"{type(e).__name__}: {e}"[:200]
                r = None
                continue
            if r.status_code == 200:
                out["erreur"] = ""
                break
            out["erreur"] = f"HTTP {r.status_code}: {r.text[:150]}"
            if 400 <= r.status_code < 500:
                return out
        if r is None or r.status_code != 200:
            return out
        try:
            data = r.json()
        except Exception:
            out["erreur"] = "reponse non JSON"
            return out

        mapping = {25: "q1", 50: "mediane", 75: "q3"}
        for item in data.get("statisticalResults") or []:
            key = mapping.get(item.get("code"))
            if key:
                out[key] = item.get("value")
        out["quality"] = data.get("quality")
        if all(out[k] is None for k in ("q1", "mediane", "q3")):
            out["erreur"] = "aucun montant renvoye"
        return out


# ---------------------------------------------------------------------------
# --check : les listes de codes de l'API collent-elles aux mappings ?
# ---------------------------------------------------------------------------
def cmd_check():
    api = Salarium()
    expected = {
        "regions": {c for _, c in REGIONS},
        "management-levels": {c for _, c in MANAGEMENT},
        "education-levels": {c for _, c in EDUCATION},
        "company-sizes": {c for _, c in SIZES},
        "permits": {c for _, c in PERMITS},
        "genders": {c for _, c in GENDERS},
    }
    ok = True
    for name, mine in expected.items():
        live = set(api.codes(name))
        manquants = mine - live
        status = "OK " if not manquants else "NON"
        if manquants:
            ok = False
        print(f"[{status}] {name:20s} API={sorted(live)}  mappings={sorted(mine)}"
              + (f"  INVALIDES={sorted(manquants)}" if manquants else ""))
    groups = set(api.codes("occupational-groups"))
    print(f"[   ] occupational-groups  API={sorted(groups)}")
    print("\nRappel : cette verification ne prouve QUE la validite des codes,"
          " pas la correspondance libelle -> code. Pour ca : --validate.")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# --validate : rejoue des lignes du fichier Excel et compare
# ---------------------------------------------------------------------------
def cmd_validate(xlsx: str, n_per_group: int = 3):
    import pandas as pd

    df = pd.read_excel(xlsx, sheet_name="RESULTATS", header=1).dropna(how="all")
    df = df[df["mediane"].notna()]
    code_col = df.columns[0]
    api = Salarium()

    total = ok = ecarts = erreurs = 0
    details = []

    for grp, sub in df.groupby(code_col):
        sub = sub.sort_values("annees_service")
        pick = sub.iloc[:: max(1, len(sub) // n_per_group)].head(n_per_group)
        for _, row in pick.iterrows():
            total += 1
            try:
                payload = build_payload(row, int(row["age"]), int(row["annees_service"]))
            except ValueError as e:
                erreurs += 1
                print(f"[MAP ] {grp} anc={row['annees_service']} : {e}")
                continue
            res = api.calculate(payload)
            if res["erreur"]:
                erreurs += 1
                print(f"[ERR ] {grp} anc={row['annees_service']} : {res['erreur']}")
                continue
            same = all(
                res[k] is not None and abs(float(res[k]) - float(row[k])) < 1.0
                for k in ("q1", "mediane", "q3")
            )
            if same:
                ok += 1
                print(f"[OK  ] {grp} anc={row['annees_service']:>2} "
                      f"med={res['mediane']} quality={res['quality']}")
            else:
                ecarts += 1
                msg = (f"[DIFF] {grp} anc={row['annees_service']:>2} "
                       f"fichier=({row['q1']},{row['mediane']},{row['q3']}) "
                       f"api=({res['q1']},{res['mediane']},{res['q3']})")
                print(msg)
                details.append((grp, dict(payload), msg))

    print(f"\n{ok}/{total} identiques · {ecarts} ecarts · {erreurs} erreurs")
    if ecarts:
        print("\nUn ecart systematique sur un critere = un mapping faux. "
              "Regarde quel champ varie entre les lignes DIFF et les lignes OK :")
        for grp, payload, _ in details[:5]:
            print(f"  {grp} -> {payload}")
    return 0 if ecarts == 0 and erreurs == 0 else 1


# ---------------------------------------------------------------------------
# --run : extraction complete
# ---------------------------------------------------------------------------
def cmd_run(combos_csv: str, out_csv: str, age_max: int = AGE_MAX):
    api = Salarium()
    with open(combos_csv, encoding="utf-8-sig", newline="") as fh:
        combos = list(csv.DictReader(fh))
    print(f"{len(combos)} combinaison(s)")

    fields = ["identifiant", "branche", "region", "profession", "position",
              "formation", "sexe", "nationalite", "taille", "treizieme",
              "paiements", "type_contrat", "horaire_hebdo", "age",
              "annees_service", "q1", "mediane", "q3", "quality", "erreur"]

    with open(out_csv, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()

        for i, combo in enumerate(combos, 1):
            ident = combo.get("identifiant") or f"combo{i}"
            age_start = int(float(combo["age_start"]))
            try:
                base = build_payload(combo, age_start, 0)
            except ValueError as e:
                print(f"[{ident}] mapping impossible : {e}")
                continue

            if not api.consistent(base["nogaId"], base["regionCode"],
                                  base["occupationalGroupCode"]):
                print(f"[{ident}] combinaison NON couverte par l'ESS "
                      f"(consistent=false) — ignoree")
                for age in range(age_start, age_max + 1):
                    writer.writerow({**{k: combo.get(k, "") for k in fields},
                                     "identifiant": ident, "age": age,
                                     "annees_service": age - age_start,
                                     "q1": "", "mediane": "", "q3": "",
                                     "quality": "", "erreur": "consistent=false"})
                continue

            for age in range(age_start, age_max + 1):
                service = age - age_start
                res = api.calculate(build_payload(combo, age, service))
                writer.writerow({
                    **{k: combo.get(k, "") for k in fields},
                    "identifiant": ident, "age": age, "annees_service": service,
                    "q1": res["q1"], "mediane": res["mediane"], "q3": res["q3"],
                    "quality": res["quality"], "erreur": res["erreur"],
                })
                fh.flush()
            print(f"[{ident}] {age_max - age_start + 1} ages · {i}/{len(combos)}")

    print(f"\nEcrit : {out_csv}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--validate", metavar="XLSX")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--run", metavar="COMBOS_CSV")
    ap.add_argument("--out", default="resultats_api.csv")
    a = ap.parse_args()

    if a.check:
        return cmd_check()
    if a.validate:
        return cmd_validate(a.validate, a.n)
    if a.run:
        return cmd_run(a.run, a.out)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
