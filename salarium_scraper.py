"""
salarium_scraper.py — meme interface, mais l'API JSON a la place du navigateur.

app.py n'a RIEN a changer : la signature de run_simulations() et la dataclass
Combination sont identiques a la version Playwright. Seul le moteur change.

    from salarium_scraper import run_simulations, Combination   # inchange

Les parametres `url` et `headless` sont acceptes puis ignores : il n'y a plus de
navigateur. `delay_seconds` sert maintenant de pause entre deux appels API.

Chaque ligne renvoyee contient les memes cles qu'avant, plus :
    quality       indicateur de fiabilite de l'estimation (renvoye par l'API)
    identifiant   si tu le renseignes dans Combination

L'ancien moteur reste disponible dans salarium_scraper_playwright.py.
"""

import asyncio
from dataclasses import dataclass
from typing import Callable, Optional

from salarium_api import Salarium, build_payload

MIN_DELAY = 0.30   # plancher : ne pas marteler un service public


@dataclass
class Combination:
    branche: str
    region: str
    profession: str
    position: str
    formation: str
    sexe: str
    nationalite: str
    taille: str
    treizieme: str
    paiements: str
    type_contrat: str
    horaire_hebdo: float
    age_start: int
    identifiant: str = ""


def _as_row(combo: Combination) -> dict:
    return {
        "branche": combo.branche,
        "region": combo.region,
        "profession": combo.profession,
        "position": combo.position,
        "formation": combo.formation,
        "sexe": combo.sexe,
        "nationalite": combo.nationalite,
        "taille": combo.taille,
        "treizieme": combo.treizieme,
        "paiements": combo.paiements,
        "type_contrat": combo.type_contrat,
        "horaire_hebdo": combo.horaire_hebdo,
    }


def _empty_row(combo: Combination, age: int, service: int, erreur: str) -> dict:
    return {
        "identifiant": combo.identifiant,
        **_as_row(combo),
        "age": age,
        "annees_service": max(0, service),
        "q1": None,
        "mediane": None,
        "q3": None,
        "quality": None,
        "erreur": erreur,
    }


async def run_simulations(
    url: str = "",
    combinations: list[Combination] = (),
    age_min: int = 25,
    age_max: int = 65,
    headless: bool = True,          # ignore : plus de navigateur
    delay_seconds: float = 1.0,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> list[dict]:

    combinations = list(combinations)
    ages = list(range(age_min, age_max + 1))
    total = len(combinations) * len(ages)
    results: list[dict] = []
    sim_idx = 0

    def _log(msg: str):
        if progress_callback:
            progress_callback(sim_idx, total, msg)

    api = Salarium(delay=max(float(delay_seconds), MIN_DELAY))

    for combo_num, combo in enumerate(combinations, 1):
        tag = combo.identifiant or f"combo{combo_num}"
        _log(f"=== Combinaison {combo_num}/{len(combinations)} ({tag}) ===")

        # 1. traduction des libelles en codes : une erreur ici est une erreur de
        #    saisie, pas un probleme reseau. On le dit clairement.
        try:
            base = build_payload(_as_row(combo), combo.age_start, 0)
        except ValueError as e:
            msg = f"Critere non reconnu : {e}"
            _log(f"❌ {msg}")
            for age in ages:
                results.append(_empty_row(combo, age, age - combo.age_start, msg[:200]))
                sim_idx += 1
            continue

        # 2. la combinaison branche/region/groupe est-elle couverte par l'ESS ?
        if not api.consistent(base["nogaId"], base["regionCode"],
                              base["occupationalGroupCode"]):
            msg = "Combinaison non couverte par l'ESS (consistent=false)"
            _log(f"⚠️ {msg}")
            for age in ages:
                results.append(_empty_row(combo, age, age - combo.age_start, msg))
                sim_idx += 1
            continue

        # 3. un appel par age
        for age in ages:
            service = max(0, age - combo.age_start)
            sim_idx += 1
            _log(f"[{combo_num}/{len(combinations)}] {tag} — age {age}, service {service}")

            row = _empty_row(combo, age, service, "")
            try:
                res = await asyncio.to_thread(
                    api.calculate, build_payload(_as_row(combo), age, service))
            except Exception as e:
                row["erreur"] = f"{type(e).__name__}: {e}"[:250]
                results.append(row)
                continue

            row["q1"] = res["q1"]
            row["mediane"] = res["mediane"]
            row["q3"] = res["q3"]
            row["quality"] = res["quality"]

            errs = []
            if res["erreur"]:
                errs.append(res["erreur"])
            trio = (res["q1"], res["mediane"], res["q3"])
            if all(v is not None for v in trio) and not (trio[0] <= trio[1] <= trio[2]):
                errs.append("Ordre q1/mediane/q3 incoherent")
            row["erreur"] = " | ".join(errs)
            results.append(row)

    _log("Termine.")
    return results
