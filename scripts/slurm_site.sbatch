#!/usr/bin/env bash
# Run one Bifrost site (mock TRE + FLARE client) as a Slurm job, e.g. on Gefion.
#   sbatch --export=TRE_ID=gefion,KIT=$HOME/gefion,SERVER=flare.brev.example:8002 scripts/slurm_site.sbatch
#SBATCH --job-name=bifrost-site
#SBATCH --nodes=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=04:00:00
#SBATCH --output=bifrost-%j.log
cd "$SLURM_SUBMIT_DIR"
exec scripts/start_site.sh "${TRE_ID:?}" "${KIT:?}" "${SERVER:-}"
