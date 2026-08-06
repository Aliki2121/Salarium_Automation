"""
check_deploy.py — test de sante de l'API Salarium, a lancer en local ET sur le
serveur apres deploiement.

    py check_deploy.py                      # en local
    railway run python check_deploy.py      # depuis l'environnement Railway

Six verifications, dans l'ordre :
  1. l'API repond
  2. les listes de codes correspondent aux mappings
  3. trois combinaisons de reference renvoient EXACTEMENT les montants deja
     valides contre le fichier V6 (test de non-regression)
  4. consistent() repond
  5. latence moyenne mesuree
  6. verdict global, code de sortie 0 ou 1

Le point 3 est le vrai test : si l'OFS renumerote un code, ces montants
changent et tu le sais avant de lancer une extraction.
"""

import sys
import time

from salarium_api import Salarium, REGIONS, MANAGEMENT, EDUCATION, SIZES, PERMITS, GENDERS

# Cas de reference — montants confirmes identiques au fichier V6 (141/141).
GOLDEN = [
    ("directeurs, universitaire, anc. 0",
     {"nogaId": 65, "regionCode": 1, "occupationalGroupCode": 10,
      "managementLevelCode": 1, "educationLevelCode": 1, "companySizeCode": 2,
      "permitCode": 1, "genderCode": 0, "hasThirteenSalaryCode": 1,
      "hasBonusCode": 0, "hasHourContractCode": 0, "workHourValue": 40.0,
      "ageCode": 25, "workYearCode": 0},
     (10893, 12646, 14833)),
    ("reception/CFC sans cadre, anc. 10",
     {"nogaId": 65, "regionCode": 1, "occupationalGroupCode": 42,
      "managementLevelCode": 3, "educationLevelCode": 6, "companySizeCode": 2,
      "permitCode": 1, "genderCode": 0, "hasThirteenSalaryCode": 1,
      "hasBonusCode": 0, "hasHourContractCode": 0, "workHourValue": 40.0,
      "ageCode": 30, "workYearCode": 10},
     (5439, 6315, 7407)),
    ("admin. entreprises, cadre inf., anc. 10",
     {"nogaId": 65, "regionCode": 1, "occupationalGroupCode": 24,
      "managementLevelCode": 2, "educationLevelCode": 3, "companySizeCode": 2,
      "permitCode": 1, "genderCode": 0, "hasThirteenSalaryCode": 1,
      "hasBonusCode": 0, "hasHourContractCode": 0, "workHourValue": 40.0,
      "ageCode": 35, "workYearCode": 10},
     (8251, 9579, 11235)),
]


def main() -> int:
    api = Salarium()
    echecs = []

    print("1. joignabilite de l'API")
    t0 = time.time()
    try:
        codes = api.codes("regions")
        print(f"   OK  regions = {codes}  ({time.time() - t0:.2f} s)")
    except Exception as e:
        print(f"   ECHEC  {type(e).__name__}: {e}")
        print("\n   Depuis un serveur, une erreur ici signifie soit un blocage "
              "sortant, soit un filtrage de l'IP du datacenter.")
        return 1

    print("\n2. codes de l'API vs mappings")
    for nom, table in [("regions", REGIONS), ("management-levels", MANAGEMENT),
                       ("education-levels", EDUCATION), ("company-sizes", SIZES),
                       ("permits", PERMITS), ("genders", GENDERS)]:
        live = set(api.codes(nom))
        mine = {c for _, c in table}
        manque = mine - live
        print(f"   {'OK ' if not manque else 'NON'} {nom:20s} API={sorted(live)}"
              + (f"  INVALIDES={sorted(manque)}" if manque else ""))
        if manque:
            echecs.append(f"codes {nom}")

    print("\n3. non-regression sur 3 cas de reference")
    latences = []
    for libelle, payload, attendu in GOLDEN:
        t0 = time.time()
        res = api.calculate(payload)
        latences.append(time.time() - t0)
        obtenu = (res["q1"], res["mediane"], res["q3"])
        if res["erreur"]:
            print(f"   ECHEC {libelle} : {res['erreur']}")
            echecs.append(libelle)
        elif obtenu == attendu:
            print(f"   OK    {libelle} -> {obtenu}  quality={res['quality']}")
        else:
            print(f"   ECART {libelle} : attendu {attendu}, obtenu {obtenu}")
            echecs.append(libelle)

    print("\n4. consistent()")
    for noga, region, groupe, quoi in [(65, 1, 10, "assurance/GE/directeurs")]:
        val = api.consistent(noga, region, groupe)
        print(f"   {'OK ' if val else 'NON'} {quoi} -> {val}")
        if not val:
            echecs.append("consistent")

    if latences:
        moy = sum(latences) / len(latences)
        print(f"\n5. latence moyenne par appel : {moy:.2f} s")
        print(f"   estimation pour 4 468 appels : {4468 * moy / 60:.0f} min")

    print("\n6. verdict")
    if echecs:
        print(f"   ECHEC — {len(echecs)} probleme(s) : {', '.join(echecs)}")
        print("   Ne lance PAS d'extraction avant d'avoir corrige.")
        return 1
    print("   TOUT EST VERT — l'API repond et les mappings sont inchanges.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
