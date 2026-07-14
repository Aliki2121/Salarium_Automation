"""
salarium_scraper.py
===================
Pilote Salarium via Playwright en se basant sur la structure exacte du DOM Angular.
"""

import asyncio
import re
from dataclasses import dataclass
from typing import Callable, Optional

from playwright.async_api import (
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

# Translation codes extraits du DOM
TC = {
    "branche":           "nogas",
    "region":            "regions",
    "profession":        None,  # Géré via le placeholder explicite
    "position":          "managementLevels",
    "formation":         "educationLevels",
    "age":               "age",
    "annees_service":    "workYear",
    "horaire":           "weeklyHour",
    "sexe":              "genders",
    "nationalite":       "permits",
    "taille":            "companySizes",
    "treizieme":         "hasThirteenSalary",
    "paiements":         "hasBonus",
    "type_contrat":      "hasHourContract",
}

MONEY_RE = re.compile(r"(\d{1,3}(?:[\s'']\d{3})+|\d{3,6})(?:[.,]\d{1,2})?")

def _parse_money(text: str) -> Optional[float]:
    # Cherche les montants avec séparateur (6'500) ou sans (2024)
    patterns = [
        r"\d{1,3}(?:[\s'\u2019\u00a0]\d{3})+",  # avec séparateur
        r"\d{4,6}",                                # 4-6 chiffres sans séparateur
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            cleaned = m.group(0).replace("'", "").replace("\u2019", "").replace("\u00a0", "").replace(" ", "")
            try:
                val = float(cleaned)
                if 1000 <= val <= 999999:
                    return val
            except ValueError:
                pass
    return None

# ============================================================================
# Page d'accueil
# ============================================================================
async def _enter_calculator(page: Page) -> bool:
    try:
        boxes = page.locator("input[type='checkbox']")
        for i in range(await boxes.count()):
            box = boxes.nth(i)
            if await box.is_visible(timeout=300) and not await box.is_checked():
                await box.click()
    except Exception:
        pass

    for txt in ["Calculer le salaire", "Calculer", "Lohn berechnen", "Calculate"]:
        try:
            btn = page.get_by_role("button", name=re.compile(txt, re.I)).first
            await btn.wait_for(state="visible", timeout=2000)
            await btn.click()
            await page.goto(url, wait_until="networkidle", timeout=60_000)
            await asyncio.sleep(4.0)
            await page.screenshot(path="/tmp/debug.png", full_page=True)
            return True
        except Exception:
            continue

    try:
        btn = page.locator("button:has-text('alcul'), a:has-text('alcul')").first
        await btn.wait_for(state="visible", timeout=2000)
        await btn.click()
        await asyncio.sleep(2.0)
        return True
    except Exception:
        return False

# ============================================================================
# Champs liste déroulante (Autocomplete & Accordéon)
# ATTENTION : CODE INTACT COMME DEMANDÉ
# ============================================================================
async def _fill_dropdown(
    page: Page,
    field_key: str,
    value: str,
    placeholder_hint: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
) -> bool:
    def _l(msg):
        if log: log(msg)

    tc = TC.get(field_key)
    container = page.locator(f"app-options-list[translationcode='{tc}']").first if tc else None

    code_match = re.match(r"^([\d\-]+)\.\s*(.*)$", value.strip())
    code = code_match.group(1) if code_match else None
    full_label = code_match.group(2) if code_match else value

    field_input = None

    if field_key == "profession":
        try:
            loc = page.locator("input[placeholder='Indiquez la profession']").first
            if await loc.is_visible(timeout=1500):
                field_input = loc
        except Exception:
            pass

    if not field_input and container:
        try:
            input_loc = container.locator("input").first
            if await input_loc.is_visible(timeout=1000):
                field_input = input_loc
        except Exception:
            pass

        if field_key not in ["branche", "region", "profession"]:
            try:
                btn_affichage = container.locator("button[aria-label=\"Bouton d'affichage\"]").first
                if await btn_affichage.is_visible(timeout=300):
                    await btn_affichage.click(force=True)
                    await asyncio.sleep(2.5)
            except Exception:
                pass

    if not field_input and placeholder_hint:
        try:
            loc = page.locator(f"input[placeholder*='{placeholder_hint}' i]").first
            if await loc.is_visible(timeout=500):
                field_input = loc
        except Exception:
            pass

        if not field_input:
            for lbl in [placeholder_hint, placeholder_hint.replace("é", "e").replace("è", "e")]:
                try:
                    loc = page.locator(f"mat-form-field:has(mat-label:has-text(\"{lbl}\")) input").first
                    if await loc.is_visible(timeout=500):
                        field_input = loc
                        break
                except Exception:
                    continue

    async def _try_select():
        # Attendre que les mat-option Angular apparaissent dans l'overlay global
        try:
            await page.wait_for_selector("mat-option", state="visible", timeout=5000)
        except Exception:
            pass

        selectors = []
        if code:
            selectors.append(f"xpath=//mat-option[starts-with(normalize-space(.), '{code}.')]")
            selectors.append(f"xpath=//div[contains(@class, 'option-label') and starts-with(normalize-space(.), '{code}.')]")
            selectors.append(f"xpath=//li[starts-with(normalize-space(.), '{code}.')]")
        short = full_label[:35].strip()
        if short:
            selectors.append(f"xpath=//mat-option[contains(normalize-space(.), '{short}')]")
            selectors.append(f"xpath=//div[contains(@class, 'option-label') and contains(normalize-space(.), '{short}')]")
            selectors.append(f"xpath=//li[contains(normalize-space(.), '{short}')]")

        for sel in selectors:
            try:
                loc = page.locator(sel)
                for i in range(await loc.count()):
                    opt = loc.nth(i)
                    if await opt.is_visible(timeout=1000):
                        text = (await opt.inner_text()).strip()
                        if code and "-" in code and re.match(r"^\d\.\s", text):
                            continue
                        # Clic normal (pas force=True) pour déclencher les event handlers Angular
                        try:
                            await opt.click(timeout=3000)
                        except Exception:
                            await opt.click(force=True)
                        await asyncio.sleep(0.5)
                        return True
            except Exception:
                continue

        # Fallback clavier : plus fiable avec Angular Material autocomplete
        try:
            await page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.3)
            await page.keyboard.press("Enter")
            await asyncio.sleep(0.5)
            return True
        except Exception:
            pass

        return False

    if field_input:
        if field_key in ["branche", "region", "profession"]:
            try:
                # Clic normal pour focus Angular (pas force=True)
                await field_input.click()
                await asyncio.sleep(0.5)
                await field_input.fill("")
                await asyncio.sleep(0.3)

                pure_words = [w for w in re.findall(r"[A-Za-zÀ-ÿ]{4,}", full_label) if w.lower() not in ("avec", "pour", "dans", "autres", "personnel", "supérieur", "inférieur")]

                search_term = None
                if pure_words:
                    search_term = pure_words[0][:5]

                if search_term:
                    await field_input.type(search_term, delay=120)
                    await asyncio.sleep(3.0)  # laisser Angular charger les options

                    if await _try_select():
                        _l(f"    [{field_key}] ✅ Sélectionné après recherche de '{search_term}'")
                        return True
            except Exception as e:
                _l(f"    [{field_key}] ⚠️ Erreur lors de la saisie: {e}")

            if code and "-" not in code:
                try:
                    await field_input.click()
                    await field_input.fill("")
                    await asyncio.sleep(0.3)
                    await field_input.type(code, delay=100)
                    await asyncio.sleep(3.0)
                    if await _try_select():
                        _l(f"    [{field_key}] ✅ Sélectionné après recherche du code '{code}'")
                        return True
                except Exception:
                    pass

            try:
                await field_input.click()
                await asyncio.sleep(1.5)
                if await _try_select():
                    _l(f"    [{field_key}] ✅ Sélectionné via liste ouverte (sans texte)")
                    return True
            except Exception:
                pass
        else:
            try:
                await field_input.click(force=True)
                await asyncio.sleep(0.5)
                if await _try_select():
                    _l(f"    [{field_key}] ✅ Sélectionné (sans recherche)")
                    return True
            except Exception:
                pass
    else:
        if await _try_select():
            _l(f"    [{field_key}] ✅ Sélectionné dans le panneau")
            return True

    _l(f"    [{field_key}] ❌ Impossible de trouver ou sélectionner l'option")
    return False

# ============================================================================
# CORRIGÉ : Champs numériques (Âge, Années service, Horaire)
# Basé sur l'approche originale — clic JS sur span.selected-by-user pour révéler
# ============================================================================
async def _set_numeric(page: Page, field_key: str, value) -> bool:
    tc = TC.get(field_key)
    if not tc:
        return False

    val_str = str(int(value)) if (isinstance(value, int) or value == int(value)) else str(value)

    # 1. On cible le conteneur du champ spécifique
    container = page.locator(f"app-input-number[translationcode='{tc}']").first

    # 2. Chercher l'input directement (s'il est déjà là)
    input_loc = container.locator('input[type="number"], input[formcontrolname="componentInput"]').first

    if not await input_loc.is_visible(timeout=500):
        # 3. TA MÉTHODE : Cliquer sur le bouton d'effacement (ou son touch-target) pour révéler l'input
        try:
            # On cherche le bouton d'effacement
            clear_btn = container.locator('button[aria-label="Bouton d\'effacement"]').first
            if await clear_btn.is_visible(timeout=1000):
                # On clique dessus (via JS pour éviter les soucis de coordonnées Playwright)
                await clear_btn.evaluate("el => el.click()")
                await asyncio.sleep(0.5)  # Laisser le temps à l'animation Angular de s'afficher
        except Exception:
            pass

    # 4. Maintenant on remplit le champ qui doit être visible (et vide !)
    try:
        if await input_loc.is_visible(timeout=2000):
            await input_loc.click()
            await input_loc.fill(val_str)
            await input_loc.press("Enter")
            await asyncio.sleep(0.5)
            return True
    except Exception as e:
        print(f"Erreur remplissage {field_key} : {e}")

    return False

# ============================================================================
# CORRIGÉ : Champs radio (Sexe, 13e, Contrat, Paiements)
# Basé sur l'approche originale — clic JS sur span.selected-by-user pour révéler
# ============================================================================
async def _set_radio(page: Page, field_key: str, value: str, log: Optional[Callable[[str], None]] = None) -> bool:
    def _l(msg):
        if log: log(msg)

    tc = TC.get(field_key)
    if not tc: return False

    container = page.locator(f"app-radio-list[translationcode='{tc}']").first
    control = container.locator(".field-control")

    # 1. Ouvrir le champ via le bouton d'effacement dans le contrôle
    if not await control.locator("input[type='radio']").first.is_visible(timeout=500):
        try:
            btn = control.locator("button[aria-label*='effacement'] .mat-mdc-button-touch-target").first
            await btn.evaluate("el => el.click()")
            await asyncio.sleep(1.0)
        except Exception:
            pass

    # 2. Traduction des valeurs (INVERSÉES selon tes observations)
    v_low = value.lower()
    target_val = "0"

    if field_key == "sexe":
        target_val = "0" if "homme" in v_low else "1"

    elif field_key == "type_contrat":
        # DOM: value="1" = salaire horaire, value="0" = salaire mensuel
        target_val = "1" if "horaire" in v_low else "0"

    elif field_key in ["treizieme", "paiements"]:
        target_val = "1" if "oui" in v_low else "0"

    # 3. Clic forcé sur l'input natif par sa valeur
    try:
        radio = control.locator(f"input[type='radio'][value='{target_val}']")

        await radio.evaluate("""el => {
            el.click();
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""")

        _l(f"    [{field_key}] ✅ Sélectionné {value} (value technique: {target_val})")
        await asyncio.sleep(0.5)
        return True
    except Exception as e:
        _l(f"    [{field_key}] ❌ Erreur sur {field_key} : {e}")
        return False

# ============================================================================
# Extraction des 3 montants salariaux
# ============================================================================
async def _extract_salary_panel(page: Page) -> dict:
    result = {"q1": None, "mediane": None, "q3": None}

    try:
        # Récupère les labels et montants directement par classe CSS
        titles = await page.locator("div.result-title-column").all_inner_texts()
        amounts = await page.locator("div.result-column").all_inner_texts()

        label_map = {
            "25% gagnent moins": "q1",
            "Médiane": "mediane",
            "Mediane": "mediane",
            "25% gagnent plus": "q3",
        }

        for i, title in enumerate(titles):
            for label, key in label_map.items():
                if label in title and i < len(amounts):
                    v = _parse_money(amounts[i])
                    if v and 1000 <= v <= 999999:
                        result[key] = v
    except Exception as e:
        print(f"Erreur extraction : {e}")

    return result

# ============================================================================
# Configuration d'une combinaison
# ============================================================================
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

# ============================================================================
# Boucle principale
# ============================================================================
async def run_simulations(
    url: str,
    combinations: list[Combination],
    age_min: int,
    age_max: int,
    headless: bool = True,
    delay_seconds: float = 1.0,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> list[dict]:

    results: list[dict] = []
    ages = list(range(age_min, age_max + 1))
    n_ages = len(ages)
    total = len(combinations) * n_ages

    def _log(idx: int, msg: str):
        if progress_callback: progress_callback(idx, total, msg)

    sim_idx = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 900}, locale="fr-CH",
        )
        page = await context.new_page()

        try:
            for combo_num, combo in enumerate(combinations, 1):
                _log(sim_idx, f"=== Combinaison {combo_num}/{len(combinations)} ===")

                try:
                    await page.goto(url, wait_until="networkidle", timeout=60_000)
                    await asyncio.sleep(4.0)  # augmenté de 2.0 → 4.0
                    await _enter_calculator(page)
                    try:
                        await page.wait_for_selector("app-options-list[translationcode='nogas'] input", timeout=10_000)
                    except Exception:
                        pass
                    await asyncio.sleep(4.0)
                except Exception as e:
                    _log(sim_idx, f"❌ Échec chargement : {e}")
                    for age in ages:
                        results.append(_empty_row(combo, age, age - combo.age_start, f"Chargement échoué : {e}"[:200]))
                        sim_idx += 1
                    continue

                try:
                    log_cb = lambda m: _log(sim_idx, m)

                    _log(sim_idx, f"  Branche : {combo.branche[:50]}…")
                    await _fill_dropdown(page, "branche", combo.branche, "branche", log=log_cb)
                    await asyncio.sleep(2.5)  # augmenté de 1.5 → 2.5

                    _log(sim_idx, f"  Région : {combo.region}")
                    await _fill_dropdown(page, "region", combo.region, "région", log=log_cb)
                    await asyncio.sleep(2.5)  # augmenté de 1.5 → 2.5

                    _log(sim_idx, f"  Profession : {combo.profession[:50]}…")
                    await _fill_dropdown(page, "profession", combo.profession, "Indiquez la profession", log=log_cb)
                    await asyncio.sleep(4.0)  # augmenté de 2.5 → 4.0

                    _log(sim_idx, "  Configuration des champs complémentaires…")

                    await _fill_dropdown(page, "position", combo.position, log=log_cb)
                    await asyncio.sleep(1.5)  # augmenté de 0.8 → 1.5

                    await _fill_dropdown(page, "formation", combo.formation, log=log_cb)
                    await asyncio.sleep(1.5)  # augmenté de 0.8 → 1.5

                    await _set_numeric(page, "horaire", combo.horaire_hebdo)
                    await asyncio.sleep(1.5)  # augmenté de 0.8 → 1.5

                    await _set_radio(page, "sexe", combo.sexe, log=log_cb)
                    await asyncio.sleep(1.5)  # augmenté de 0.8 → 1.5

                    await _fill_dropdown(page, "nationalite", combo.nationalite, log=log_cb)
                    await asyncio.sleep(1.5)  # augmenté de 0.8 → 1.5

                    await _fill_dropdown(page, "taille", combo.taille, log=log_cb)
                    await asyncio.sleep(1.5)  # augmenté de 0.8 → 1.5

                    await _set_radio(page, "treizieme", combo.treizieme, log=log_cb)
                    await asyncio.sleep(1.5)  # augmenté de 0.8 → 1.5

                    await _set_radio(page, "paiements", combo.paiements, log=log_cb)
                    await asyncio.sleep(1.5)  # augmenté de 0.8 → 1.5

                    await _set_radio(page, "type_contrat", combo.type_contrat, log=log_cb)
                    await asyncio.sleep(2.0)  # augmenté de 1.0 → 2.0

                except Exception as e:
                    _log(sim_idx, f"❌ Échec config : {e}")
                    for age in ages:
                        results.append(_empty_row(combo, age, age - combo.age_start, f"Config échouée : {e}"[:200]))
                        sim_idx += 1
                    continue

                for i, age in enumerate(ages):
                    service = max(0, age - combo.age_start)
                    sim_idx += 1
                    _log(sim_idx, f"  [{combo_num}/{len(combinations)}] Âge {age}, service {service}")

                    row = _empty_row(combo, age, service, "")

                    try:
                        ok_age = await _set_numeric(page, "age", age)
                        await asyncio.sleep(0.5)
                        ok_svc = await _set_numeric(page, "annees_service", service)
                        await asyncio.sleep(0.5)

                        if not ok_age: row["erreur"] = "Âge non modifié"
                        if not ok_svc: row["erreur"] = (row["erreur"] + " | " if row["erreur"] else "") + "Années service non modifiées"

                        try:
                            await page.wait_for_load_state("networkidle", timeout=8000)
                        except PlaywrightTimeoutError:
                            pass
                        await asyncio.sleep(3.0)

                        amounts = await _extract_salary_panel(page)
                        row["q1"] = amounts["q1"]
                        row["mediane"] = amounts["mediane"]
                        row["q3"] = amounts["q3"]

                        if all(v is None for v in (row["q1"], row["mediane"], row["q3"])):
                            row["erreur"] = (row["erreur"] + " | " if row["erreur"] else "") + "Aucun montant détecté"

                    except Exception as e:
                        row["erreur"] = str(e)[:300]

                    results.append(row)
                    await asyncio.sleep(delay_seconds)

        finally:
            await context.close()
            await browser.close()

    _log(total, "Terminé.")
    return results

def _empty_row(combo: Combination, age: int, service: int, erreur: str) -> dict:
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
        "age": age,
        "annees_service": max(0, service),
        "q1": None,
        "mediane": None,
        "q3": None,
        "erreur": erreur,
    }
