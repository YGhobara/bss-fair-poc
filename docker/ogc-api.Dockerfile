FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY services/ogc_api/requirements.txt /app/services/ogc_api/requirements.txt

RUN pip install --no-cache-dir \
    -r /app/services/ogc_api/requirements.txt

COPY services /app/services
COPY client /app/client
COPY metadata /app/metadata

EXPOSE 8000

CMD ["uvicorn", "services.ogc_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
