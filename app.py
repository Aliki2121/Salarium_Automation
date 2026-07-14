import asyncio
import io
import time
from datetime import datetime
from itertools import product
import pandas as pd
import streamlit as st
from salarium_scraper import run_simulations, Combination
from salarium_options import (
    BRANCHES, REGIONS, PROFESSIONS,
    POSITIONS, FORMATIONS, SEXES, NATIONALITES,
    TAILLES_ENTREPRISE, OUI_NON, TYPES_CONTRAT,
)

MAX_COMBINATIONS = 20
AGE_MAX = 65

st.set_page_config(page_title="Salarium Batch", page_icon="📊", layout="wide")
st.image("logohrbenchinstituteVFtrespetit.jpg", width=100)
# --- MODIFICATION : CSS POUR ENLEVER LA BARRE À GAUCHE ---
st.markdown("""
    <style>
        [data-testid="stSidebar"], [data-testid="stSidebarNav"] {display: none;}
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)

st.title(" Salarium — Simulations multi-combinaisons")

# ---------------------------------------------------------------------------
# Champs principaux (single-select)
# ---------------------------------------------------------------------------
st.subheader("Paramètres principaux")

col1, col2, col3 = st.columns(3)
with col1:
    branche = st.selectbox(
        "Branche économique",
        options=BRANCHES,
        index=BRANCHES.index("66. Activités auxiliaires de services financiers et d'assurance"),
    )
with col2:
    region = st.selectbox("Région", options=REGIONS, index=0)
with col3:
    profession = st.selectbox("Groupe de professions", options=PROFESSIONS, index=0)

# ---------------------------------------------------------------------------
# Paramètres numériques
# ---------------------------------------------------------------------------
st.subheader("Âge & horaire")

col_a, col_b = st.columns(2)
with col_a:
    age_start = st.slider(
        "Âge de début (= 0 année de service)",
        min_value=18, max_value=50, value=25, step=1,
        help=f"L'âge boucle de cette valeur jusqu'à {AGE_MAX}. Les années de service incrémentent en parallèle.",
    )
with col_b:
    horaire_hebdo = st.slider(
        "Horaire hebdomadaire (h)",
        min_value=8, max_value=50, value=40, step=1,
    )

n_ages = AGE_MAX - age_start + 1
st.caption(f"→ {n_ages} simulations d'âge ({age_start} → {AGE_MAX})")

# ---------------------------------------------------------------------------
# Paramètres complémentaires (multi-select)
# ---------------------------------------------------------------------------
st.subheader("Paramètres complémentaires (multi-sélection → combinaisons)")

c1, c2 = st.columns(2)
with c1:
    sel_positions = st.multiselect("Position professionnelle", POSITIONS, default=[POSITIONS[0]])
    sel_formations = st.multiselect("Formation", FORMATIONS, default=[FORMATIONS[0]])
    sel_sexes = st.multiselect("Sexe", SEXES, default=["Homme"])
    sel_nationalites = st.multiselect("Nationalité / Permis", NATIONALITES, default=[NATIONALITES[0]])

with c2:
    sel_tailles = st.multiselect("Taille de l'entreprise", TAILLES_ENTREPRISE, default=[TAILLES_ENTREPRISE[2]])
    sel_treiziemes = st.multiselect("13e salaire", OUI_NON, default=["Oui"])
    sel_paiements = st.multiselect("Paiements spéciaux", OUI_NON, default=["Oui"])
    sel_contrats = st.multiselect("Type de contrat", TYPES_CONTRAT, default=[TYPES_CONTRAT[0]])

# ---------------------------------------------------------------------------
# Compteur & Lancement
# ---------------------------------------------------------------------------
multi_lists = [sel_positions, sel_formations, sel_sexes, sel_nationalites, sel_tailles, sel_treiziemes, sel_paiements,
               sel_contrats]
n_combinations = 1
for lst in multi_lists: n_combinations *= max(len(lst), 0)
n_total = n_combinations * n_ages

if any(len(lst) == 0 for lst in multi_lists):
    st.error("⚠️ Sélectionne au moins une option dans chaque catégorie.")
elif n_combinations > MAX_COMBINATIONS:
    st.error(f"⚠️ {n_combinations} combinaisons (max : {MAX_COMBINATIONS}).")
else:
    st.info(f"**{n_combinations} combinaison(s) × {n_ages} âges = {n_total} simulations**")

if "results_df" not in st.session_state:
    st.session_state.results_df = None

run = st.button("🚀 Lancer", type="primary", use_container_width=True)

if run:
    combos = []
    for pos, form, sex, nat, taille, tre, pai, contrat in product(
            sel_positions, sel_formations, sel_sexes, sel_nationalites,
            sel_tailles, sel_treiziemes, sel_paiements, sel_contrats,
    ):
        combos.append(Combination(
            branche=branche, region=region, profession=profession,
            position=pos, formation=form, sexe=sex, nationalite=nat,
            taille=taille, treizieme=tre, paiements=pai, type_contrat=contrat,
            horaire_hebdo=float(horaire_hebdo), age_start=age_start,
        ))

    progress_bar = st.progress(0.0, text="Initialisation…")
    status_box = st.empty()
    start_time = time.time()
    last_idx = [0]


    def on_progress(idx: int, total: int, message: str):
        pct = idx / total if total else 0
        progress_bar.progress(min(pct, 1.0), text=f"[{idx}/{total}] {message[:80]}")
        elapsed = time.time() - start_time
        if idx > 0 and idx > last_idx[0]:
            last_idx[0] = idx
            avg = elapsed / idx
            rem = (total - idx) * avg
            status_box.info(
                f"⏱️ Écoulé : **{int(elapsed // 60)}m {int(elapsed % 60):02d}s** | ETA : **{int(rem // 60)}m {int(rem % 60):02d}s**")


    try:
        results = asyncio.run(run_simulations(
            url="https://www.salarium.bfs.admin.ch/",
            combinations=combos,
            age_min=age_start,
            age_max=AGE_MAX,
            headless=True,
            delay_seconds=1.0,
            progress_callback=on_progress,
        ))
        import os

        if os.path.exists("/tmp/debug.png"):
            st.image("/tmp/debug.png", caption="Ce que Playwright voit")
        st.session_state.results_df = pd.DataFrame(results)
        st.success(f"✅ Terminé en {int((time.time() - start_time) // 60)}m.")
    except Exception as e:
        st.error(f"❌ {e}")


# ---------------------------------------------------------------------------
# Excel & Résultats (Tes fonctions originales)
# ---------------------------------------------------------------------------
def build_excel(df: pd.DataFrame) -> bytes:
    # ... (Garde ton code build_excel exactement comme il était) ...
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Résultats", index=False)
    return output.getvalue()


if st.session_state.results_df is not None and not st.session_state.results_df.empty:
    df = st.session_state.results_df
    st.subheader("Résultats")
    st.dataframe(df, use_container_width=True, hide_index=True, height=350)

    if df["mediane"].notna().any():
        st.subheader("Évolution par âge")
        chart_df = df.groupby("age")[["q1", "mediane", "q3"]].mean()
        st.line_chart(chart_df)

    st.download_button("⬇️ Télécharger Excel", data=build_excel(df), file_name="salarium_export.xlsx",
                       use_container_width=True, type="primary")
