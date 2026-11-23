# Image du service catalog (Partie 3). Contexte de build : racine du dépôt.
#   docker build -f services/catalog/Dockerfile -t lso-catalog .
FROM python:3.11-slim
WORKDIR /app
COPY services/catalog/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY services/catalog /app/services/catalog
ENV PYTHONUNBUFFERED=1
EXPOSE 8003
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://localhost:8003/health').status != 200)"
CMD ["uvicorn", "services.catalog.main:app", "--host", "0.0.0.0", "--port", "8003"]
