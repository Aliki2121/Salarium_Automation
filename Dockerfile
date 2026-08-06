# Plus besoin de l'image Playwright (~1,7 Go) : le moteur interroge l'API JSON,
# il n'y a plus de navigateur a embarquer.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# requirements en premier : la couche pip est mise en cache tant que le fichier
# ne change pas.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Utilisateur non privilegie
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8501

# Forme shell pour que ${PORT} soit substitue par Railway.
# --server.headless=true : pas de tentative d'ouvrir un navigateur au demarrage.
CMD streamlit run app.py \
      --server.port=${PORT:-8501} \
      --server.address=0.0.0.0 \
      --server.headless=true
