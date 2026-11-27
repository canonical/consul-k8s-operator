#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]
CHARM_BASE = os.environ.get("CHARM_BASE", "ubuntu@24.04")


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Deploy the charm and verify the Consul unit reaches active status."""
    filename = f"{APP_NAME}_{CHARM_BASE}-amd64.charm"
    if artifact := os.environ.get("CHARM_PATH"):
        # The CI workflow passes one of the artifacts from the pack job.
        charm = Path(artifact).resolve().with_name(filename)
        assert charm.is_file(), f"Expected packed charm at {charm}"
    else:
        built_charms = await ops_test.build_charm(".", return_all=True)
        charm = next((path for path in built_charms if path.name == filename), None)
        assert charm is not None, f"Charmcraft did not produce {filename}"
    resources = {"consul-image": METADATA["resources"]["consul-image"]["upstream-source"]}

    # Deploy the charm and wait for active/idle status
    await ops_test.model.deploy(
        charm, resources=resources, application_name=APP_NAME, base=CHARM_BASE
    )
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=1000
    )

    assert ops_test.model.applications[APP_NAME].units[0].workload_status == "active"
