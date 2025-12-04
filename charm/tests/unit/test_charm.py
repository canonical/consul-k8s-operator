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
def kubernetes_service_handler():
    with patch("charm.KubernetesServiceHandler") as p:
        yield p


def test_pebble_ready(harness: Harness[ConsulCharm], kubernetes_service_handler):
    # Simulate the container coming up and emission of pebble-ready event
    harness.begin_with_initial_hooks()
    harness.container_pebble_ready("consul")
    # Ensure we set an ActiveStatus with no message
    assert harness.model.unit.status == ActiveStatus()


def test_pebble_ready_with_configs(
    harness: Harness[ConsulCharm], k8s_client, kubernetes_service_handler
):
    """Simulate container coming up with configs changed."""
    harness.update_config({"expose-gossip-and-rpc-ports": "nodeport", "serflan-node-port": 30501})
    harness.begin_with_initial_hooks()
    harness.container_pebble_ready("consul")

    # Ensure we set an ActiveStatus with no message
    assert harness.model.unit.status == ActiveStatus()
    assert harness.charm.ports.serf_lan == 30501
    assert k8s_client().list.assert_called_once


def test_all_relations(harness: Harness[ConsulCharm], kubernetes_service_handler):
    """Test all relations."""
    model_name = "test-model"
    app_name = harness._backend.app_name
    datacenter = "test-dc"
    expected_join_addresses = [f"{app_name}.{model_name}.svc:8301"]
    expected_http_address = f"{app_name}.{model_name}.svc:8500"
    expected_cluster_config = {
        "datacenter": datacenter,
        "internal_gossip_endpoints": json.dumps(expected_join_addresses),
        "external_gossip_endpoints": json.dumps(None),
        "internal_http_endpoint": json.dumps(expected_http_address),
        "external_http_endpoint": json.dumps(None),
        "external_gossip_healthcheck_endpoints": json.dumps(None),
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
    harness: Harness[ConsulCharm], k8s_client, kubernetes_service_handler
):
    """Test all relations."""
    model_name = "test-model"
    app_name = harness._backend.app_name
    datacenter = "test-dc"
    serflan_node_port = 30501
    host_ip = "10.10.0.10"

    # Configure the mock to return None for get_loadbalancer_ip
    mock_handler_instance = kubernetes_service_handler.return_value
    mock_handler_instance.get_loadbalancer_ip.return_value = None

    p = Pod(status=PodStatus(hostIP=host_ip))
    k8s_client().list.return_value = [p]
    expected_external_join_addresses = [f"{host_ip}:{serflan_node_port}"]
    expected_internal_join_addresses = [f"{app_name}-lb.{model_name}.svc:{serflan_node_port}"]
    expected_http_address = f"{app_name}-lb.{model_name}.svc:8500"
    expected_cluster_config = {
        "datacenter": datacenter,
        "internal_gossip_endpoints": json.dumps(expected_internal_join_addresses),
        "external_gossip_endpoints": json.dumps(expected_external_join_addresses),
        "internal_http_endpoint": json.dumps(expected_http_address),
        "external_http_endpoint": json.dumps(None),
        "external_gossip_healthcheck_endpoints": json.dumps(None),
    }

    harness.set_model_name(model_name)
    harness.update_config(
        {
            "datacenter": datacenter,
            "expose-gossip-and-rpc-ports": "nodeport",
            "serflan-node-port": serflan_node_port,
        }
    )
    rel_id = harness.add_relation(DEFAULT_RELATION_NAME, "consul-client", app_data={})
    harness.set_leader()
    harness.begin_with_initial_hooks()

    # Ensure we set an ActiveStatus with no message
    assert harness.model.unit.status == ActiveStatus()

    actual_cluster_config = harness.get_relation_data(rel_id, app_name)
    assert actual_cluster_config == expected_cluster_config


def test_open_ports_with_nodeport_service(
    harness: Harness[ConsulCharm], kubernetes_service_handler
):
    """Test that NodePort service is created when expose-gossip-and-rpc-ports is 'nodeport'."""
    from k8s_resource_handlers import ServiceType

    harness.update_config({"expose-gossip-and-rpc-ports": "nodeport", "serflan-node-port": 30501})
    harness.begin()

    # Verify KubernetesServiceHandler was called with NodePort service type
    kubernetes_service_handler.assert_called_once()
    call_args = kubernetes_service_handler.call_args
    assert call_args[1]["service_type"] == ServiceType.NodePort


def test_open_ports_with_loadbalancer_service(
    harness: Harness[ConsulCharm], kubernetes_service_handler
):
    """Test that LoadBalancer service is created when expose-gossip-and-rpc-ports is 'loadbalancer'."""
    from k8s_resource_handlers import ServiceType

    harness.update_config(
        {
            "expose-gossip-and-rpc-ports": "loadbalancer",
            "serflan-node-port": 30501,
        }
    )
    harness.begin()

    # Verify KubernetesServiceHandler was called with LoadBalancer service type
    kubernetes_service_handler.assert_called_once()
    call_args = kubernetes_service_handler.call_args
    assert call_args[1]["service_type"] == ServiceType.LoadBalancer


def test_open_ports_without_expose_returns_none(harness: Harness[ConsulCharm]):
    """Test that no KubernetesServiceHandler is created when expose-gossip-and-rpc-ports is 'false'."""
    harness.update_config({"expose-gossip-and-rpc-ports": "false"})
    harness.begin()

    # Verify that k8s_service_handler is None
    assert harness.charm.k8s_service_handler is None


def test_open_ports_with_invalid_value_returns_none(harness: Harness[ConsulCharm]):
    """Test that no KubernetesServiceHandler is created when expose-gossip-and-rpc-ports has invalid value."""
    harness.update_config({"expose-gossip-and-rpc-ports": "invalid"})
    harness.begin()

    # Verify that k8s_service_handler is None
    assert harness.charm.k8s_service_handler is None


def test_service_handler_instance_stored(
    harness: Harness[ConsulCharm], kubernetes_service_handler
):
    """Test that KubernetesServiceHandler instance is stored to prevent garbage collection."""
    harness.update_config({"expose-gossip-and-rpc-ports": "nodeport", "serflan-node-port": 30501})
    harness.begin()

    # Verify that the handler instance is stored in the charm
    assert hasattr(harness.charm, "k8s_service_handler")
    assert harness.charm.k8s_service_handler is not None


def test_service_ports_configuration(harness: Harness[ConsulCharm], kubernetes_service_handler):
    """Test that service ports are correctly configured."""
    harness.update_config({"expose-gossip-and-rpc-ports": "nodeport", "serflan-node-port": 30501})
    harness.begin()

    # Verify KubernetesServiceHandler was called with correct ports
    kubernetes_service_handler.assert_called_once()
    call_args = kubernetes_service_handler.call_args
    service_ports = call_args[0][1]  # Second positional argument is service_ports

    # Should have 3 ports: serf_lan TCP, serf_lan UDP, and http
    assert len(service_ports) == 3

    # Check serf_lan TCP port
    tcp_port = next((p for p in service_ports if p.protocol == "TCP" and p.port == 30501), None)
    assert tcp_port is not None
    assert tcp_port.nodePort == 30501

    # Check serf_lan UDP port
    udp_port = next((p for p in service_ports if p.protocol == "UDP" and p.port == 30501), None)
    assert udp_port is not None
    assert udp_port.nodePort == 30501

    # Check http port
    http_port = next((p for p in service_ports if p.port == 8500), None)
    assert http_port is not None
    assert http_port.protocol == "TCP"


def test_external_gossip_healthcheck_with_loadbalancer_ip(
    harness: Harness[ConsulCharm], k8s_client, kubernetes_service_handler
):
    """Test external gossip healthcheck endpoints when LoadBalancer IP is available."""
    model_name = "test-model"
    app_name = harness._backend.app_name
    datacenter = "test-dc"
    serflan_node_port = 30501
    lb_ip = "203.0.113.10"

    # Configure the mock to return a LoadBalancer IP
    mock_handler_instance = kubernetes_service_handler.return_value
    mock_handler_instance.get_loadbalancer_ip.return_value = lb_ip

    p = Pod(status=PodStatus(hostIP="10.10.0.10"))
    k8s_client().list.return_value = [p]

    expected_healthcheck_endpoints = [f"{lb_ip}:{serflan_node_port}"]
    expected_cluster_config = {
        "datacenter": datacenter,
        "internal_gossip_endpoints": json.dumps(
            [f"{app_name}-lb.{model_name}.svc:{serflan_node_port}"]
        ),
        "external_gossip_endpoints": json.dumps([f"10.10.0.10:{serflan_node_port}"]),
        "internal_http_endpoint": json.dumps(f"{app_name}-lb.{model_name}.svc:8500"),
        "external_http_endpoint": json.dumps(None),
        "external_gossip_healthcheck_endpoints": json.dumps(expected_healthcheck_endpoints),
    }

    harness.set_model_name(model_name)
    harness.update_config(
        {
            "datacenter": datacenter,
            "expose-gossip-and-rpc-ports": "loadbalancer",
            "serflan-node-port": serflan_node_port,
        }
    )
    rel_id = harness.add_relation(DEFAULT_RELATION_NAME, "consul-client", app_data={})
    harness.set_leader()
    harness.begin_with_initial_hooks()

    # Ensure we set an ActiveStatus with no message
    assert harness.model.unit.status == ActiveStatus()

    actual_cluster_config = harness.get_relation_data(rel_id, app_name)
    assert actual_cluster_config == expected_cluster_config


def test_config_changed_with_invalid_expose_value(
    harness: Harness[ConsulCharm], kubernetes_service_handler
):
    """Test that invalid expose-gossip-and-rpc-ports value sets BlockedStatus."""
    from ops.model import BlockedStatus

    harness.begin()
    harness.container_pebble_ready("consul")

    # Update config with invalid value
    harness.update_config({"expose-gossip-and-rpc-ports": "invalid-value"})

    # Verify charm is in BlockedStatus with appropriate message
    assert isinstance(harness.model.unit.status, BlockedStatus)
    assert "Invalid value 'invalid-value' for expose-gossip-and-rpc-ports" in str(
        harness.model.unit.status.message
    )
    assert "Valid values are: false, nodeport, loadbalancer" in str(
        harness.model.unit.status.message
    )


def test_config_changed_with_serflan_port_too_low(
    harness: Harness[ConsulCharm], kubernetes_service_handler
):
    """Test that serflan-node-port below 30000 sets BlockedStatus."""
    from ops.model import BlockedStatus

    harness.begin()
    harness.container_pebble_ready("consul")

    # Update config with port below valid range
    harness.update_config({"expose-gossip-and-rpc-ports": "nodeport", "serflan-node-port": 29999})

    # Verify charm is in BlockedStatus with appropriate message
    assert isinstance(harness.model.unit.status, BlockedStatus)
    assert "Invalid value '29999' for serflan-node-port" in str(harness.model.unit.status.message)
    assert "Valid range is 30000-32767" in str(harness.model.unit.status.message)


def test_config_changed_with_serflan_port_too_high(
    harness: Harness[ConsulCharm], kubernetes_service_handler
):
    """Test that serflan-node-port above 32767 sets BlockedStatus."""
    from ops.model import BlockedStatus

    harness.begin()
    harness.container_pebble_ready("consul")

    # Update config with port above valid range
    harness.update_config(
        {"expose-gossip-and-rpc-ports": "loadbalancer", "serflan-node-port": 32768}
    )

    # Verify charm is in BlockedStatus with appropriate message
    assert isinstance(harness.model.unit.status, BlockedStatus)
    assert "Invalid value '32768' for serflan-node-port" in str(harness.model.unit.status.message)
    assert "Valid range is 30000-32767" in str(harness.model.unit.status.message)


def test_config_changed_with_valid_serflan_port_boundary_low(
    harness: Harness[ConsulCharm], k8s_client, kubernetes_service_handler
):
    """Test that serflan-node-port at 30000 (lower boundary) is valid."""
    from lightkube.models.core_v1 import Pod, PodStatus

    p = Pod(status=PodStatus(hostIP="10.10.0.10"))
    k8s_client().list.return_value = [p]

    harness.begin()
    harness.container_pebble_ready("consul")

    # Update config with port at lower boundary
    harness.update_config({"expose-gossip-and-rpc-ports": "nodeport", "serflan-node-port": 30000})

    # Verify charm is in ActiveStatus
    assert harness.model.unit.status == ActiveStatus()


def test_config_changed_with_valid_serflan_port_boundary_high(
    harness: Harness[ConsulCharm], k8s_client, kubernetes_service_handler
):
    """Test that serflan-node-port at 32767 (upper boundary) is valid."""
    from lightkube.models.core_v1 import Pod, PodStatus

    p = Pod(status=PodStatus(hostIP="10.10.0.10"))
    k8s_client().list.return_value = [p]

    harness.begin()
    harness.container_pebble_ready("consul")

    # Update config with port at upper boundary
    harness.update_config(
        {"expose-gossip-and-rpc-ports": "loadbalancer", "serflan-node-port": 32767}
    )

    # Verify charm is in ActiveStatus
    assert harness.model.unit.status == ActiveStatus()
