# Image du service inventory (Partie 3). Contexte de build : racine du dépôt.
#   docker build -f services/inventory/Dockerfile -t lso-inventory .
FROM python:3.11-slim
WORKDIR /app
COPY services/inventory/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY services/inventory /app/services/inventory
ENV PYTHONUNBUFFERED=1
EXPOSE 8002
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://localhost:8002/health').status != 200)"
CMD ["uvicorn", "services.inventory.main:app", "--host", "0.0.0.0", "--port", "8002"]
