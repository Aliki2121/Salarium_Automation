"""
salarium_scraper.py — version corrigée
======================================
Pilote Salarium (formulaire Angular Material) via Playwright.

Corrections par rapport à la version précédente :
  1. `async_playwright` était utilisé sans être importé  -> NameError garanti.
  2. `_enter_calculator` référençait `url`, absent de sa signature -> NameError
     silencieusement avalé, et un `page.goto` annulait le clic. Corrigé.
  3. Dépliage des champs : on utilise le "Bouton d'affichage" (le "Bouton
     d'effacement" n'existe QUE si le champ a déjà une valeur — c'était la cause
     des `Timeout 30000ms exceeded` sur sexe / age / annees_service).
  4. `locator.evaluate()` sur un élément absent attendait 30 s. Tous les accès
     passent maintenant par un `wait_for` explicite à timeout court.
  5. `locator.is_visible(timeout=...)` : le timeout est déprécié et IGNORÉ par
     Playwright, la méthode ne patiente pas. Remplacé par `_visible()`.
  6. Le fallback clavier de `_try_select` renvoyait toujours True : il pouvait
     sélectionner n'importe quelle option en la déclarant OK. Toute sélection
     est désormais RELUE et vérifiée.
  7. `run_simulations` ignorait les valeurs de retour : une combinaison mal
     remplie produisait quand même 41 lignes chiffrées, donc fausses. Un "gate"
     abandonne la combinaison et écrit une capture + le HTML dans DEBUG_DIR.
  8. Les critères sont ré-empreintés pendant la boucle des âges : si le
     formulaire se réinitialise, on arrête la combinaison au lieu de continuer.
"""

import asyncio
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Optional

from playwright.async_api import (
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

# ============================================================================
# Réglages
# ============================================================================

# Si True, une sélection qui ne peut pas être relue dans le DOM est considérée
# comme un échec (recommandé : on préfère une ligne vide à une ligne fausse).
# Passer à False seulement pour déboguer.
STRICT_VERIFY = True

DEBUG_DIR = os.environ.get("SALARIUM_DEBUG_DIR", "/tmp/salarium_debug")

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

# Champs dont la valeur ne doit PAS changer pendant la boucle des âges.
FINGERPRINT_TC = [
    "nogas", "regions", "managementLevels", "educationLevels", "genders",
    "permits", "companySizes", "hasThirteenSalary", "hasBonus",
    "hasHourContract", "weeklyHour",
]

# Secours si le libellé d'un radio n'est pas lisible dans le DOM.
RADIO_FALLBACK = {
    "sexe":         {"homme": "0", "femme": "1"},
    "treizieme":    {"non": "0", "oui": "1"},
    "paiements":    {"non": "0", "oui": "1"},
    "type_contrat": {"mensuel": "0", "horaire": "1"},
}

# ============================================================================
# Utilitaires
# ============================================================================


def _norm(s: Optional[str]) -> str:
    """minuscule, sans accent, ponctuation réduite à des espaces."""
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


async def _visible(loc: Locator, timeout: float = 1000) -> bool:
    """Remplace is_visible(timeout=...), qui n'attend pas."""
    try:
        await loc.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:
        return False


async def _exists(loc: Locator) -> bool:
    try:
        return await loc.count() > 0
    except Exception:
        return False


def _parse_money(text: str) -> Optional[float]:
    """Extrait un montant CHF. Le format avec séparateur (6'500) est prioritaire
    pour ne pas confondre un montant avec une année ou un pourcentage."""
    patterns = [
        r"\d{1,3}(?:[\s'\u2019\u00a0]\d{3})+",  # 6'500 / 6 500
        r"\b\d{4,6}\b",                          # 6500
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, text):
            cleaned = re.sub(r"[\s'\u2019\u00a0]", "", m.group(0))
            try:
                val = float(cleaned)
            except ValueError:
                continue
            if 1000 <= val <= 999999:
                return val
    return None


async def _reveal_field(container: Locator, timeout: float = 2500) -> bool:
    """Déplie un champ Angular replié.

    Ordre d'essai : bouton d'affichage (toujours présent), bouton d'effacement
    (présent seulement si le champ a déjà une valeur), puis le contrôle lui-même.
    Ne lève jamais et ne bloque jamais 30 s.
    """
    candidates = [
        "button[aria-label=\"Bouton d'affichage\"]",
        "button[aria-label*='affichage']",
        "button[aria-label*='effacement']",
    ]
    for sel in candidates:
        btn = container.locator(sel).first
        if not await _exists(btn):
            continue
        try:
            await btn.click(force=True, timeout=timeout)
            await asyncio.sleep(0.7)
            return True
        except Exception:
            continue

    for sel in [".field-control", ".field-label", "button"]:
        loc = container.locator(sel).first
        if not await _exists(loc):
            continue
        try:
            await loc.click(force=True, timeout=timeout)
            await asyncio.sleep(0.7)
            return True
        except Exception:
            continue
    return False


# ============================================================================
# Page d'accueil
# ============================================================================
async def _enter_calculator(page: Page, log: Optional[Callable[[str], None]] = None) -> bool:
    """Accepte les conditions puis entre dans le calculateur.

    `url` n'est plus utilisé ici : recharger la page annulerait le clic.
    """
    def _l(m):
        if log:
            log(m)

    try:
        boxes = page.locator("input[type='checkbox']")
        for i in range(await boxes.count()):
            box = boxes.nth(i)
            if await _visible(box, 300) and not await box.is_checked():
                await box.click(timeout=2000)
    except Exception:
        pass

    for txt in ["Calculer le salaire", "Calculer", "Lohn berechnen", "Calculate"]:
        try:
            btn = page.get_by_role("button", name=re.compile(txt, re.I)).first
            await btn.wait_for(state="visible", timeout=2000)
            await btn.click(timeout=5000)
            await asyncio.sleep(3.0)
            return True
        except Exception:
            continue

    try:
        btn = page.locator("button:has-text('alcul'), a:has-text('alcul')").first
        await btn.wait_for(state="visible", timeout=2000)
        await btn.click(timeout=5000)
        await asyncio.sleep(2.0)
        return True
    except Exception:
        _l("    [accueil] ⚠️ Aucun bouton d'entrée trouvé (formulaire déjà affiché ?)")
        return False


# ============================================================================
# Champs liste déroulante (Autocomplete & Accordéon)
# ============================================================================
async def _selection_ok(
    container: Optional[Locator],
    field_input: Optional[Locator],
    code: Optional[str],
    full_label: str,
) -> Optional[bool]:
    """Relit le champ pour vérifier ce qui a réellement été sélectionné.

    True  = la valeur attendue est bien présente
    False = une autre valeur est affichée
    None  = illisible (ni input ni conteneur exploitables)
    """
    texts = []
    if field_input is not None:
        try:
            texts.append(await field_input.input_value())
        except Exception:
            pass
    if container is not None:
        try:
            texts.append(await container.inner_text())
        except Exception:
            pass

    blob = _norm(" | ".join(t for t in texts if t))
    if not blob:
        return None

    key = _norm(full_label)
    if key:
        for n in (30, 20, 12):
            frag = key[:n].strip()
            if frag and frag in blob:
                return True
    if code and _norm(code) in blob:
        return True
    return False


async def _fill_dropdown(
    page: Page,
    field_key: str,
    value: str,
    placeholder_hint: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
) -> bool:
    def _l(msg):
        if log:
            log(msg)

    tc = TC.get(field_key)
    container = page.locator(f"app-options-list[translationcode='{tc}']").first if tc else None

    # Le point est obligatoire : sinon "20 - 49 employés" serait lu comme
    # code="20" + label="- 49 employés".
    code_match = re.match(r"^([\d\-]+)\.\s*(.*)$", value.strip())
    code = code_match.group(1) if code_match else None
    full_label = code_match.group(2) if code_match else value

    field_input = None

    if field_key == "profession":
        loc = page.locator("input[placeholder='Indiquez la profession']").first
        if await _visible(loc, 2000):
            field_input = loc

    if field_input is None and container is not None:
        # Déplier AVANT de chercher l'input : sur un champ replié il n'existe pas.
        if field_key not in ("branche", "region", "profession"):
            inner = container.locator("input, mat-option, input[type='radio']").first
            if not await _exists(inner):
                await _reveal_field(container)
                await asyncio.sleep(1.5)

        input_loc = container.locator("input").first
        if await _visible(input_loc, 1500):
            field_input = input_loc

    if field_input is None and placeholder_hint:
        loc = page.locator(f"input[placeholder*='{placeholder_hint}' i]").first
        if await _visible(loc, 800):
            field_input = loc
        else:
            for lbl in [placeholder_hint,
                        placeholder_hint.replace("é", "e").replace("è", "e")]:
                loc = page.locator(
                    f"mat-form-field:has(mat-label:has-text(\"{lbl}\")) input"
                ).first
                if await _visible(loc, 800):
                    field_input = loc
                    break

    async def _click_option() -> bool:
        """Clique l'option correspondante dans l'overlay. Ne vérifie rien."""
        try:
            await page.wait_for_selector("mat-option", state="visible", timeout=5000)
        except Exception:
            pass

        selectors = []
        if code:
            selectors += [
                f"xpath=//mat-option[starts-with(normalize-space(.), '{code}.')]",
                f"xpath=//div[contains(@class, 'option-label') and starts-with(normalize-space(.), '{code}.')]",
                f"xpath=//li[starts-with(normalize-space(.), '{code}.')]",
            ]
        short = full_label[:35].strip()
        if short:
            selectors += [
                f"xpath=//mat-option[contains(normalize-space(.), \"{short}\")]",
                f"xpath=//div[contains(@class, 'option-label') and contains(normalize-space(.), \"{short}\")]",
                f"xpath=//li[contains(normalize-space(.), \"{short}\")]",
            ]

        for sel in selectors:
            try:
                loc = page.locator(sel)
                n = await loc.count()
            except Exception:
                continue
            for i in range(n):
                opt = loc.nth(i)
                if not await _visible(opt, 800):
                    continue
                try:
                    text = (await opt.inner_text()).strip()
                except Exception:
                    continue
                # "1-2." ne doit pas être confondu avec "1."
                if code and "-" in code and re.match(r"^\d\.\s", text):
                    continue
                try:
                    await opt.click(timeout=3000)
                except Exception:
                    try:
                        await opt.click(force=True, timeout=3000)
                    except Exception:
                        continue
                await asyncio.sleep(0.6)
                return True
        return False

    async def _keyboard_pick() -> bool:
        """Dernier recours. NE déclare PAS le succès : la vérification le fera."""
        try:
            await page.keyboard.press("ArrowDown")
            await asyncio.sleep(0.3)
            await page.keyboard.press("Enter")
            await asyncio.sleep(0.6)
            return True
        except Exception:
            return False

    async def _verify(attempt: str) -> Optional[bool]:
        ok = await _selection_ok(container, field_input, code, full_label)
        if ok is True:
            _l(f"    [{field_key}] ✅ {value[:45]} ({attempt})")
        elif ok is False:
            _l(f"    [{field_key}] ❌ valeur affichée différente de « {value[:35]} » ({attempt})")
        else:
            _l(f"    [{field_key}] ⚠️ sélection illisible dans le DOM ({attempt})")
        return ok

    attempts = []

    if field_input is not None and field_key in ("branche", "region", "profession"):
        pure_words = [
            w for w in re.findall(r"[A-Za-zÀ-ÿ]{4,}", full_label)
            if w.lower() not in ("avec", "pour", "dans", "autres", "personnel",
                                 "supérieur", "inférieur")
        ]
        search_term = pure_words[0][:5] if pure_words else None

        if search_term:
            async def _by_text():
                await field_input.click(timeout=5000)
                await asyncio.sleep(0.5)
                await field_input.fill("")
                await asyncio.sleep(0.3)
                await field_input.type(search_term, delay=120)
                await asyncio.sleep(3.0)
                return await _click_option()
            attempts.append((f"recherche '{search_term}'", _by_text))

        if code and "-" not in code:
            async def _by_code():
                await field_input.click(timeout=5000)
                await field_input.fill("")
                await asyncio.sleep(0.3)
                await field_input.type(code, delay=100)
                await asyncio.sleep(3.0)
                return await _click_option()
            attempts.append((f"recherche du code '{code}'", _by_code))

        async def _open_list():
            await field_input.click(timeout=5000)
            await asyncio.sleep(1.5)
            return await _click_option()
        attempts.append(("liste ouverte", _open_list))

    elif field_input is not None:
        async def _click_then_select():
            await field_input.click(force=True, timeout=5000)
            await asyncio.sleep(0.5)
            return await _click_option()
        attempts.append(("panneau ouvert", _click_then_select))

    else:
        attempts.append(("panneau déjà ouvert", _click_option))

    # Le clavier reste en dernier recours, mais soumis à vérification.
    attempts.append(("fallback clavier", _keyboard_pick))

    inconclusive = False
    for label, fn in attempts:
        try:
            clicked = await fn()
        except Exception as e:
            _l(f"    [{field_key}] ⚠️ {label} : {type(e).__name__}")
            continue
        if not clicked:
            continue
        ok = await _verify(label)
        if ok is True:
            return True
        if ok is None:
            inconclusive = True

    if inconclusive and not STRICT_VERIFY:
        _l(f"    [{field_key}] ⚠️ accepté sans vérification (STRICT_VERIFY=False)")
        return True

    _l(f"    [{field_key}] ❌ échec de sélection de « {value[:45]} »")
    return False


# ============================================================================
# Champs numériques (Âge, Années service, Horaire)
# ============================================================================
async def _set_numeric(
    page: Page,
    field_key: str,
    value,
    log: Optional[Callable[[str], None]] = None,
    timeout: float = 6000,
) -> bool:
    def _l(msg):
        if log:
            log(msg)

    tc = TC.get(field_key)
    if not tc:
        return False

    val_str = str(int(value)) if float(value) == int(float(value)) else str(value)

    container = page.locator(f"app-input-number[translationcode='{tc}']").first
    inp = container.locator(
        'input[type="number"], input[formcontrolname="componentInput"]'
    ).first

    if not await _visible(inp, 600):
        await _reveal_field(container)
        try:
            await inp.wait_for(state="visible", timeout=timeout)
        except PlaywrightTimeoutError:
            _l(f"    [{field_key}] ❌ champ non déplié — input absent du DOM")
            return False

    try:
        await inp.click(timeout=timeout)
        await inp.fill("")
        await inp.fill(val_str)
        await inp.press("Enter")
        await asyncio.sleep(0.6)
    except PlaywrightTimeoutError:
        _l(f"    [{field_key}] ❌ input non actionnable")
        return False
    except Exception as e:
        _l(f"    [{field_key}] ❌ {type(e).__name__} : {e}")
        return False

    # Relecture : la valeur a-t-elle vraiment été prise en compte ?
    try:
        read = (await inp.input_value() or "").strip()
    except Exception:
        read = ""
    if re.sub(r"\D", "", read) != re.sub(r"\D", "", val_str):
        _l(f"    [{field_key}] ❌ lu « {read} », attendu « {val_str} »")
        return False
    return True


# ============================================================================
# Champs radio (Sexe, 13e, Contrat, Paiements)
# ============================================================================
async def _set_radio(
    page: Page,
    field_key: str,
    value: str,
    log: Optional[Callable[[str], None]] = None,
    timeout: float = 6000,
) -> bool:
    def _l(msg):
        if log:
            log(msg)

    tc = TC.get(field_key)
    if not tc:
        return False

    container = page.locator(f"app-radio-list[translationcode='{tc}']").first
    radios = container.locator("input[type='radio']")

    if not await _exists(radios):
        await _reveal_field(container)
        try:
            await radios.first.wait_for(state="attached", timeout=timeout)
        except PlaywrightTimeoutError:
            _l(f"    [{field_key}] ❌ champ non déplié — 0 radio dans le DOM")
            return False

    target = _norm(value)
    try:
        n = await radios.count()
    except Exception:
        n = 0

    # 1. Sélection par LIBELLÉ : reste juste même si l'OFS inverse les value=.
    for i in range(n):
        radio = radios.nth(i)
        try:
            label = await radio.evaluate(
                """el => {
                    const host = el.closest('mat-radio-button, label, .radio-item, li');
                    return (host ? host.innerText : '') || el.getAttribute('aria-label') || '';
                }"""
            )
        except Exception:
            label = ""
        label_n = _norm(label)
        if not label_n:
            continue
        if target in label_n or label_n.startswith(target[:6]):
            try:
                await radio.evaluate(
                    "el => { el.click();"
                    " el.dispatchEvent(new Event('change', {bubbles:true})); }"
                )
            except Exception:
                continue
            await asyncio.sleep(0.6)
            try:
                if await radio.is_checked():
                    _l(f"    [{field_key}] ✅ {value} (libellé « {label_n[:30]} »)")
                    return True
            except Exception:
                pass
            _l(f"    [{field_key}] ⚠️ clic sans effet sur « {label_n[:30]} »")

    # 2. Secours : mapping value=, mais l'état est relu.
    tv = next((v for k, v in RADIO_FALLBACK.get(field_key, {}).items() if k in target), None)
    if tv is not None:
        radio = container.locator(f"input[type='radio'][value='{tv}']").first
        if await _exists(radio):
            try:
                await radio.evaluate(
                    "el => { el.click();"
                    " el.dispatchEvent(new Event('change', {bubbles:true})); }"
                )
                await asyncio.sleep(0.6)
                if await radio.is_checked():
                    _l(f"    [{field_key}] ✅ {value} (secours value={tv}, libellé non lu)")
                    return True
            except Exception:
                pass

    _l(f"    [{field_key}] ❌ « {value} » non sélectionné ({n} radios trouvés)")
    return False


# ============================================================================
# Empreinte des critères (détection d'une réinitialisation du formulaire)
# ============================================================================
async def _criteria_fingerprint(page: Page) -> str:
    parts = []
    for tc in FINGERPRINT_TC:
        for tag in ("app-options-list", "app-radio-list", "app-input-number"):
            loc = page.locator(f"{tag}[translationcode='{tc}']").first
            if not await _exists(loc):
                continue
            try:
                parts.append(_norm(await loc.inner_text())[:60])
            except Exception:
                pass
            break
    try:
        prof = page.locator("input[placeholder='Indiquez la profession']").first
        if await _exists(prof):
            parts.append(_norm(await prof.input_value())[:60])
    except Exception:
        pass
    return " || ".join(parts)


async def _dump_debug(page: Page, name: str, log: Optional[Callable[[str], None]] = None):
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        png = os.path.join(DEBUG_DIR, f"{name}.png")
        html = os.path.join(DEBUG_DIR, f"{name}.html")
        await page.screenshot(path=png, full_page=True)
        with open(html, "w", encoding="utf-8") as fh:
            fh.write(await page.content())
        if log:
            log(f"    🔍 capture : {png}")
    except Exception:
        pass


# ============================================================================
# Extraction des 3 montants salariaux
# ============================================================================
async def _extract_salary_panel(page: Page) -> dict:
    result = {"q1": None, "mediane": None, "q3": None}

    try:
        titles = await page.locator("div.result-title-column").all_inner_texts()
        amounts = await page.locator("div.result-column").all_inner_texts()
    except Exception:
        return result

    label_map = {
        "25 gagnent moins": "q1",
        "mediane": "mediane",
        "25 gagnent plus": "q3",
    }

    for i, title in enumerate(titles):
        if i >= len(amounts):
            break
        t = _norm(title)
        for label, key in label_map.items():
            if label in t:
                v = _parse_money(amounts[i])
                if v is not None:
                    result[key] = v
                break

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
    identifiant: str = ""      # ex. "7-1" — utile pour tracer les échecs


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
        if progress_callback:
            progress_callback(idx, total, msg)

    sim_idx = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            viewport={"width": 1400, "height": 900}, locale="fr-CH",
        )
        page = await context.new_page()

        try:
            for combo_num, combo in enumerate(combinations, 1):
                tag = combo.identifiant or f"combo{combo_num}"
                _log(sim_idx, f"=== Combinaison {combo_num}/{len(combinations)} ({tag}) ===")
                log_cb = lambda m: _log(sim_idx, m)

                def _abandon(message: str):
                    """Remplit les 41 lignes de la combinaison avec l'erreur."""
                    nonlocal sim_idx
                    for age in ages:
                        results.append(_empty_row(
                            combo, age, age - combo.age_start, message[:200]
                        ))
                        sim_idx += 1

                # ------------------------------------------------ chargement
                try:
                    await page.goto(url, wait_until="networkidle", timeout=60_000)
                    await asyncio.sleep(3.0)
                    await _enter_calculator(page, log=log_cb)
                    await page.wait_for_selector(
                        "app-options-list[translationcode='nogas']", timeout=20_000
                    )
                    await asyncio.sleep(3.0)
                except Exception as e:
                    msg = f"Chargement échoué : {type(e).__name__}"
                    _log(sim_idx, f"❌ {msg}")
                    await _dump_debug(page, f"{tag}_chargement", log_cb)
                    _abandon(msg)
                    continue

                # ------------------------------------------------ critères
                checks: dict[str, bool] = {}
                try:
                    _log(sim_idx, f"  Branche : {combo.branche[:50]}…")
                    checks["branche"] = await _fill_dropdown(
                        page, "branche", combo.branche, "branche", log=log_cb)
                    await asyncio.sleep(2.5)

                    _log(sim_idx, f"  Région : {combo.region}")
                    checks["region"] = await _fill_dropdown(
                        page, "region", combo.region, "région", log=log_cb)
                    await asyncio.sleep(2.5)

                    _log(sim_idx, f"  Profession : {combo.profession[:50]}…")
                    checks["profession"] = await _fill_dropdown(
                        page, "profession", combo.profession,
                        "Indiquez la profession", log=log_cb)
                    await asyncio.sleep(4.0)

                    _log(sim_idx, "  Champs complémentaires…")
                    checks["position"] = await _fill_dropdown(
                        page, "position", combo.position, log=log_cb)
                    await asyncio.sleep(1.5)

                    checks["formation"] = await _fill_dropdown(
                        page, "formation", combo.formation, log=log_cb)
                    await asyncio.sleep(1.5)

                    checks["horaire"] = await _set_numeric(
                        page, "horaire", combo.horaire_hebdo, log=log_cb)
                    await asyncio.sleep(1.5)

                    checks["sexe"] = await _set_radio(
                        page, "sexe", combo.sexe, log=log_cb)
                    await asyncio.sleep(1.5)

                    checks["nationalite"] = await _fill_dropdown(
                        page, "nationalite", combo.nationalite, log=log_cb)
                    await asyncio.sleep(1.5)

                    checks["taille"] = await _fill_dropdown(
                        page, "taille", combo.taille, log=log_cb)
                    await asyncio.sleep(1.5)

                    checks["treizieme"] = await _set_radio(
                        page, "treizieme", combo.treizieme, log=log_cb)
                    await asyncio.sleep(1.5)

                    checks["paiements"] = await _set_radio(
                        page, "paiements", combo.paiements, log=log_cb)
                    await asyncio.sleep(1.5)

                    checks["type_contrat"] = await _set_radio(
                        page, "type_contrat", combo.type_contrat, log=log_cb)
                    await asyncio.sleep(2.0)
                except Exception as e:
                    msg = f"Config interrompue : {type(e).__name__} {e}"
                    _log(sim_idx, f"❌ {msg}")
                    await _dump_debug(page, f"{tag}_config", log_cb)
                    _abandon(msg)
                    continue

                # ------------------------------------------------ GATE
                failed = [k for k, ok in checks.items() if not ok]
                if failed:
                    msg = "Critères non appliqués : " + ", ".join(failed)
                    _log(sim_idx, f"❌ {msg} — combinaison abandonnée")
                    await _dump_debug(page, f"{tag}_criteres", log_cb)
                    _abandon(msg)
                    continue

                reference_fp = await _criteria_fingerprint(page)

                # ------------------------------------------------ boucle âges
                aborted_at = None
                for i, age in enumerate(ages):
                    service = max(0, age - combo.age_start)
                    sim_idx += 1
                    _log(sim_idx, f"  [{combo_num}/{len(combinations)}] Âge {age}, service {service}")

                    row = _empty_row(combo, age, service, "")
                    errs = []

                    try:
                        if not await _set_numeric(page, "age", age, log=log_cb):
                            errs.append("Âge non modifié")
                        await asyncio.sleep(0.5)
                        if not await _set_numeric(page, "annees_service", service, log=log_cb):
                            errs.append("Années service non modifiées")
                        await asyncio.sleep(0.5)

                        try:
                            await page.wait_for_load_state("networkidle", timeout=8000)
                        except PlaywrightTimeoutError:
                            pass
                        await asyncio.sleep(2.5)

                        amounts = await _extract_salary_panel(page)
                        if all(v is None for v in amounts.values()):
                            # une seconde chance : le panneau met parfois du temps
                            await asyncio.sleep(2.5)
                            amounts = await _extract_salary_panel(page)

                        row["q1"] = amounts["q1"]
                        row["mediane"] = amounts["mediane"]
                        row["q3"] = amounts["q3"]

                        if all(v is None for v in amounts.values()):
                            errs.append("Aucun montant détecté")
                        else:
                            trio = [amounts["q1"], amounts["mediane"], amounts["q3"]]
                            if any(v is None for v in trio):
                                errs.append("Montant partiel")
                            elif not (trio[0] <= trio[1] <= trio[2]):
                                errs.append("Ordre q1/médiane/q3 incohérent")

                        # les critères ont-ils tenu ? (contrôle toutes les 10 itérations)
                        if i % 10 == 0 and reference_fp:
                            if await _criteria_fingerprint(page) != reference_fp:
                                errs.append("Critères modifiés en cours de boucle")
                                aborted_at = age

                    except Exception as e:
                        errs.append(f"{type(e).__name__} : {e}"[:250])

                    row["erreur"] = " | ".join(errs)
                    results.append(row)

                    if aborted_at is not None:
                        _log(sim_idx, "❌ Formulaire réinitialisé — combinaison abandonnée")
                        await _dump_debug(page, f"{tag}_reset_age{age}", log_cb)
                        for rest in ages[i + 1:]:
                            results.append(_empty_row(
                                combo, rest, max(0, rest - combo.age_start),
                                "Critères modifiés en cours de boucle",
                            ))
                            sim_idx += 1
                        break

                    await asyncio.sleep(delay_seconds)

        finally:
            await context.close()
            await browser.close()

    _log(total, "Terminé.")
    return results


def _empty_row(combo: Combination, age: int, service: int, erreur: str) -> dict:
    return {
        "identifiant": combo.identifiant,
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
