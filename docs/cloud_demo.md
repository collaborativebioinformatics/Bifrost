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

## 4. Site on HUNT Cloud (optional)

HUNT Cloud opens no outbound connection by default. The data space leader files a network-opening request with HUNT Cloud support ([lab orders → network opening](https://docs.hdc.ntnu.no/administer-science/service-desk/lab-orders#network-opening)) naming the address and port the client will dial: `<DNS>:8002`, TLS. The security-group rule from section 1 already admits the connection on the server side; restrict it to HUNT Cloud's egress address once that is known.

In the HUNT Cloud lab, the same flow as Gefion (the kit holds only certificates, so the host needs a checkout too):

```sh
git clone https://github.com/collaborativebioinformatics/Bifrost.git && cd Bifrost
conda create -n heimdall python=3.12 && conda activate heimdall      # or a venv as in section 3
pip install -e ".[dev]" "nvflare==2.9.0"
scripts/kit_import.sh hunt                 # paste the kit exported in section 1
scripts/start_site.sh hunt .remote/hunt    # mock TRE + FLARE client, as in section 2
```

If pasting into the lab terminal is impractical, move `flare/kits/hunt.tgz` through a restricted share instead (for Drive: `gdown <file-id>`), then `tar -xzf hunt.tgz -C .remote`. The archive holds the client's private key: no open links, and delete the copy afterwards.

Before starting, the handshake can be checked with the kit's own credentials from `.remote/hunt`:

```sh
openssl s_client -connect <DNS>:8002 -servername <DNS> -CAfile startup/rootCA.pem -cert startup/client.crt -key startup/client.key -alpn h2
```

If the kit was packed for a name the lab cannot resolve, pass the address it can reach as a third argument, `scripts/start_site.sh hunt .remote/hunt <ip>:8002`; that patches a copy of the kit rather than `/etc/hosts`. To run only the FLARE client, without the restart loop: `cd .remote/hunt && bash startup/sub_start.sh --once` (jobs then have no mock TRE to query).

## 5. Run the analyses (AWS)

```sh
cd ~/Bifrost
.venv/bin/python scripts/run_job.py --mode prod --wait-time 10 spec/examples/allele_freq.json
.venv/bin/python scripts/run_job.py --mode prod --wait-time 10 spec/examples/allele_freq_rejected.json
.venv/bin/python -m server.overseer_queue list        # then: approve <spec_hash> --by <name> --note "..."
.venv/bin/python scripts/run_job.py --mode prod --wait-time 5 --fedavg --rounds 10 spec/examples/fed_linreg.json
```

Each run prints coverage (`2/3 sites, missing ['gefion']` while Gefion is not up), the verify table against ground truth and the overseer queue. Audit trails are on each client host in `audit/<tre_id>/audit.jsonl`; server decisions in `server/out/release_log.jsonl` on AWS.

## 6. Stop

Ctrl-C in each site terminal; on AWS `(cd flare/workspace/federated_apis/prod_00/$DNS && bash startup/stop_fl.sh)`.

## If something breaks

| Symptom | Fix |
|---|---|
| client logs `ClientConnectorCertificateError` | kit was provisioned for a different host name than it dials; re-run `bootstrap_server.sh "$DNS"` and re-export kits |
| client never registers, `nc` fails | security group / corporate firewall; port 8002 outbound from the client host |
| `job ... finished: TIMEOUT` | a site died mid-job; the result is still written — check `server/out/<spec_hash>/result.json` coverage |
| verify shows `FAIL` on a filtered spec | expected: no pooled truth for filtered subsets; informational only |
