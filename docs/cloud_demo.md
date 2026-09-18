# Cloud demo runbook (simplified)

One orchestrator in the cloud, sites wherever they are. No Docker, no NextCloud: three scripts.

```
AWS EC2 (public DNS)            laptop                         Gefion (Slurm job, optional)
  FLARE server + admin   <---   site hunt  (REST mock TRE)       site gefion (DataSHIELD-style)
  scripts/run_job.py     <---   site brev  (SQL mock TRE)   --->  dials out to AWS:8002
```

Every client dials **out** to `AWS:8002` over mTLS. Nothing dials into a TRE.
All hosts generate the same synthetic cohort (seed 7), so AWS can verify results against ground truth.

## 1. Server (AWS, once)

```sh
TOKEN=$(curl -sX PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
DNS=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-hostname); echo "$DNS"
sudo apt-get install -y git curl && cd ~ && git clone https://github.com/collaborativebioinformatics/Bifrost.git && cd Bifrost
scripts/bootstrap_server.sh "$DNS"        # venv, provision CA + kits for the public DNS, start server, pack kits
```

EC2 console → security group → inbound **TCP 8002** (clients) and **8003** (admin) from `0.0.0.0/0` (mTLS gates access; tighten later).

Hand out kits without scp — print one, paste on the client host:

```sh
scripts/kit_export.sh hunt     # repeat for brev, gefion
```

## 2. Sites (laptop, one terminal per site)

```sh
scripts/kit_import.sh hunt                 # paste the exported line, Enter, Ctrl-D
scripts/start_site.sh hunt .remote/hunt    # mock TRE + FLARE client; look for "Successfully registered client:hunt"
```

```sh
scripts/kit_import.sh brev
TRE_PORT=8001 scripts/start_site.sh brev .remote/brev
```

Check reachability first if a client does not register: `nc -zv <DNS> 8002`.

## 3. Site on Gefion (optional)

On a login node (Teleport → Connect):

```sh
git clone https://github.com/collaborativebioinformatics/Bifrost.git && cd Bifrost
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"      # or: module load python/3.11 first
scripts/kit_import.sh gefion
sbatch --export=TRE_ID=gefion,KIT=$PWD/.remote/gefion scripts/slurm_site.sbatch   # or run start_site.sh in an salloc shell
```

Needs outbound TCP from the compute node to `<DNS>:8002` (`nc -zv <DNS> 8002`). If Gefion has no egress, run `gefion` on the laptop as a third terminal instead — the demo still shows three heterogeneous APIs behind one server.

## 4. Run the analyses (AWS)

```sh
cd ~/Bifrost
.venv/bin/python scripts/run_job.py --mode prod --wait-time 10 spec/examples/allele_freq.json
.venv/bin/python scripts/run_job.py --mode prod --wait-time 10 spec/examples/allele_freq_rejected.json
.venv/bin/python -m server.overseer_queue list        # then: approve <spec_hash> --by <name> --note "..."
.venv/bin/python scripts/run_job.py --mode prod --wait-time 5 --fedavg --rounds 10 spec/examples/fed_linreg.json
```

Each run prints coverage (`2/3 sites, missing ['gefion']` while Gefion is not up), the verify table against ground truth and the overseer queue. Audit trails are on each client host in `audit/<tre_id>/audit.jsonl`; server decisions in `server/out/release_log.jsonl` on AWS.

## 5. Stop

Ctrl-C in each site terminal; on AWS `(cd flare/workspace/federated_apis/prod_00/$DNS && bash startup/stop_fl.sh)`.

## If something breaks

| Symptom | Fix |
|---|---|
| client logs `ClientConnectorCertificateError` | kit was provisioned for a different host name than it dials; re-run `bootstrap_server.sh "$DNS"` and re-export kits |
| client never registers, `nc` fails | security group / corporate firewall; port 8002 outbound from the client host |
| `job ... finished: TIMEOUT` | a site died mid-job; the result is still written — check `server/out/<spec_hash>/result.json` coverage |
| verify shows `FAIL` on a filtered spec | expected: no pooled truth for filtered subsets; informational only |
