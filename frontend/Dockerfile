# Image de l'interface Streamlit (Partie 3). Contexte de build : racine du dépôt.
#   docker build -f frontend/Dockerfile -t lso-frontend .
FROM python:3.11-slim
WORKDIR /app/frontend
COPY frontend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY frontend /app/frontend
ENV PYTHONUNBUFFERED=1
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port", "8501", "--server.address", "0.0.0.0", "--server.headless", "true"]
