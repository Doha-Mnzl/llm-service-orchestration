# Image du service sales (Partie 3). Contexte de build : racine du dépôt.
#   docker build -f services/sales/Dockerfile -t lso-sales .
FROM python:3.11-slim
WORKDIR /app
COPY services/sales/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY services/sales /app/services/sales
ENV PYTHONUNBUFFERED=1
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://localhost:8001/health').status != 200)"
CMD ["uvicorn", "services.sales.main:app", "--host", "0.0.0.0", "--port", "8001"]
