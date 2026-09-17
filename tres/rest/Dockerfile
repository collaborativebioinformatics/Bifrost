FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir fastapi uvicorn pandas
WORKDIR /app
COPY tres/ /app/tres/
ENV TRE_ID=local DATA_PATH=/data/local.csv
EXPOSE 8000
CMD ["uvicorn", "tres.rest.app:app", "--host", "0.0.0.0", "--port", "8000"]
