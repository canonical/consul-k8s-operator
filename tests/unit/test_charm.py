# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import json
from unittest.mock import patch

import pytest
from charms.consul_k8s.v0.consul_cluster import DEFAULT_RELATION_NAME
from lightkube.models.core_v1 import Pod, PodStatus
from ops.model import ActiveStatus
from ops.testing import Harness

from charm import ConsulCharm


@pytest.fixture()
def harness():
    harness = Harness(ConsulCharm)
    yield harness
    harness.cleanup()


@pytest.fixture()
def k8s_client():
    with patch("charm.Client") as p:
        yield p


@pytest.fixture()
def kubernetes_service_patch():
    with patch("charm.KubernetesServicePatch") as p:
        yield p


def test_pebble_ready(harness: Harness[ConsulCharm]):
    # Simulate the container coming up and emission of pebble-ready event
    harness.begin_with_initial_hooks()
    harness.container_pebble_ready("consul")
    # Ensure we set an ActiveStatus with no message
    assert harness.model.unit.status == ActiveStatus()


def test_pebble_ready_with_configs(
    harness: Harness[ConsulCharm], k8s_client, kubernetes_service_patch
):
    """Simulate container coming up with configs changed."""
    harness.update_config({"expose-gossip-and-rpc-ports": True, "serflan-node-port": 30501})
    harness.begin_with_initial_hooks()
    harness.container_pebble_ready("consul")

    # Ensure we set an ActiveStatus with no message
    assert harness.model.unit.status == ActiveStatus()
    assert harness.charm.ports.serf_lan == 30501
    assert k8s_client().list.assert_called_once


def test_all_relations(harness: Harness[ConsulCharm]):
    """Test all relations."""
    model_name = "test-model"
    app_name = harness._backend.app_name
    datacenter = "test-dc"
    expected_join_addresses = [f"{app_name}.{model_name}.svc:8301"]
    expected_cluster_config = {
        "datacenter": datacenter,
        "server_join_addresses": json.dumps(expected_join_addresses),
    }

    harness.set_model_name(model_name)
    harness.update_config({"datacenter": datacenter})
    rel_id = harness.add_relation(DEFAULT_RELATION_NAME, "consul-client", app_data={})
    harness.set_leader()
    harness.begin_with_initial_hooks()

    # Ensure we set an ActiveStatus with no message
    assert harness.model.unit.status == ActiveStatus()

    actual_cluster_config = harness.get_relation_data(rel_id, app_name)
    assert actual_cluster_config == expected_cluster_config


def test_cluster_config_relation_with_exposed_ports(
    harness: Harness[ConsulCharm], k8s_client, kubernetes_service_patch
):
    """Test all relations."""
    model_name = "test-model"
    app_name = harness._backend.app_name
    datacenter = "test-dc"
    seflan_node_port = 30501
    host_ip = "10.10.0.10"

    p = Pod(status=PodStatus(hostIP=host_ip))
    k8s_client().list.return_value = [p]
    expected_join_addresses = [f"{host_ip}:{seflan_node_port}"]
    expected_cluster_config = {
        "datacenter": datacenter,
        "server_join_addresses": json.dumps(expected_join_addresses),
    }

    harness.set_model_name(model_name)
    harness.update_config(
        {
            "datacenter": datacenter,
            "expose-gossip-and-rpc-ports": True,
            "serflan-node-port": seflan_node_port,
        }
    )
    rel_id = harness.add_relation(DEFAULT_RELATION_NAME, "consul-client", app_data={})
    harness.set_leader()
    harness.begin_with_initial_hooks()

    # Ensure we set an ActiveStatus with no message
    assert harness.model.unit.status == ActiveStatus()

    actual_cluster_config = harness.get_relation_data(rel_id, app_name)
    assert actual_cluster_config == expected_cluster_config
