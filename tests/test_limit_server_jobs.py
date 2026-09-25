"""scripts/limit_server_jobs.py: synthetic kit layout in tmp_path, no network, no FLARE
provisioning. Run only this file: .venv/bin/python -m pytest -q -p no:cacheprovider
tests/test_limit_server_jobs.py"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.limit_server_jobs import find_server_kit_default, limit_max_jobs

# Shape matches nvflare's local_server_resources template (master_template.yml): extra
# components and top-level keys included to prove the rest of the document is preserved.
DEFAULT_DOC = {
    "format_version": 2,
    "class_list_enforcement_mode": "enforce",
    "class_allow_list": [],
    "servers": [{"admin_storage": "transfer", "max_num_clients": 100, "heart_beat_timeout": 600}],
    "snapshot_persistor": {
        "path": "nvflare.app_common.state_persistors.storage_state_persistor.StorageStatePersistor",
        "args": {"uri_root": "/"},
    },
    "components": [
        {
            "id": "job_scheduler",
            "path": "nvflare.app_common.job_schedulers.job_scheduler.DefaultJobScheduler",
            "args": {"max_jobs": 4},
        },
        {
            "id": "job_manager",
            "path": "nvflare.apis.impl.job_def_manager.SimpleJobDefManager",
            "args": {"uri_root": "/tmp/nvflare/jobs-storage", "job_store_id": "job_store"},
        },
    ],
}

# A client kit's local/resources.json.default has no job_scheduler component.
CLIENT_DOC = {
    "format_version": 2,
    "components": [
        {
            "id": "resource_manager",
            "path": "nvflare.app_common.resource_managers.gpu_resource_manager.GPUResourceManager",
            "args": {"num_of_gpus": 0, "mem_per_gpu_in_GiB": 0},
        },
    ],
}


def _write_kit(prod_dir: Path, participant: str, doc: dict) -> Path:
    local = prod_dir / participant / "local"
    local.mkdir(parents=True)
    p = local / "resources.json.default"
    p.write_text(json.dumps(doc, indent=2))
    return p


def test_preserves_full_document_and_only_changes_max_jobs(tmp_path):
    prod_dir = tmp_path / "prod_00"
    _write_kit(prod_dir, "server_org", DEFAULT_DOC)
    _write_kit(prod_dir, "site_a", CLIENT_DOC)

    target = limit_max_jobs(find_server_kit_default(prod_dir))

    result = json.loads(target.read_text())
    expected = json.loads(json.dumps(DEFAULT_DOC))  # deep copy
    expected["components"][0]["args"]["max_jobs"] = 1
    assert result == expected


def test_default_file_left_untouched(tmp_path):
    prod_dir = tmp_path / "prod_00"
    default_path = _write_kit(prod_dir, "server_org", DEFAULT_DOC)
    before = default_path.read_text()

    limit_max_jobs(default_path)

    assert default_path.read_text() == before
    assert json.loads(before)["components"][0]["args"]["max_jobs"] == 4


def test_idempotent_rerun(tmp_path):
    prod_dir = tmp_path / "prod_00"
    default_path = _write_kit(prod_dir, "server_org", DEFAULT_DOC)

    first = limit_max_jobs(default_path).read_text()
    second = limit_max_jobs(default_path).read_text()

    assert first == second


def test_fails_loudly_when_no_job_scheduler_found(tmp_path):
    prod_dir = tmp_path / "prod_00"
    _write_kit(prod_dir, "site_a", CLIENT_DOC)
    _write_kit(prod_dir, "site_b", CLIENT_DOC)

    with pytest.raises(SystemExit, match="found 0"):
        find_server_kit_default(prod_dir)


def test_fails_loudly_when_multiple_job_scheduler_kits_found(tmp_path):
    prod_dir = tmp_path / "prod_00"
    _write_kit(prod_dir, "server_org", DEFAULT_DOC)
    _write_kit(prod_dir, "server_org_2", DEFAULT_DOC)

    with pytest.raises(SystemExit, match="found 2"):
        find_server_kit_default(prod_dir)
