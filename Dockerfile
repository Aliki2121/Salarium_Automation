FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD streamlit run app.py --server.port=${PORT:-8501} --server.address=0.0.0.0
