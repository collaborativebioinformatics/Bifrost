# FLARE server (orchestrator) -- sits OUTSIDE every TRE. Never sees row-level data.
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir "nvflare>=2.5,<2.10" pyyaml pydantic numpy
COPY server/ /app/server/
COPY spec/ /app/spec/
COPY scripts/ /app/scripts/
COPY sites.yaml /app/sites.yaml
ENV PYTHONPATH=/app
CMD ["bash", "-c", "if [ -f /workspace/federated_apis/prod_00/flare-server/startup/start.sh ]; then bash scripts/start_server.sh; else echo 'no server kit in /workspace; idling'; sleep infinity; fi"]
