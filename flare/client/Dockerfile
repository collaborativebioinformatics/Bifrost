# FLARE client image: the only process inside a TRE that dials out (to flare-server only).
# Contains the adapters + safe output filter; talks to the TRE's native API over the
# internal network. M1: idles. M3: scripts/start_client.sh starts the provisioned client.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml /app/
RUN pip install --no-cache-dir "nvflare>=2.5,<2.10" pyyaml pydantic httpx numpy pandas
COPY adapters/ /app/adapters/
COPY harmonisation/ /app/harmonisation/
COPY spec/ /app/spec/
COPY scripts/ /app/scripts/
COPY sites.yaml /app/sites.yaml
ENV PYTHONPATH=/app AUDIT_DIR=/audit
CMD ["bash", "-c", "if [ -f /client_kit/startup/start.sh ]; then bash scripts/start_client.sh; else echo 'no client kit mounted; idling'; sleep infinity; fi"]
