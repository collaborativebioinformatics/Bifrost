# FLARE server (orchestrator) -- sits OUTSIDE every TRE. Never sees row-level data.
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir "nvflare>=2.5,<2.10" pyyaml pydantic numpy
COPY adapters/ /app/adapters/
COPY harmonisation/ /app/harmonisation/
COPY spec/ /app/spec/
COPY server/ /app/server/
COPY scripts/ /app/scripts/
COPY flare/app/ /app/flare/app/
COPY flare/__init__.py /app/flare/__init__.py
COPY sites.yaml projects.yaml /app/
ENV PYTHONPATH=/app SERVER_OUT=/server_out
CMD ["bash", "-c", "if [ -f /workspace/federated_apis/prod_00/flare-server/startup/start.sh ]; then bash scripts/start_server.sh; else echo 'no server kit in /workspace; idling'; sleep infinity; fi"]
