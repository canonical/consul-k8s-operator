# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for k8s_resource_handlers module."""

from unittest.mock import MagicMock, Mock, patch

import pytest
from lightkube.models.core_v1 import ServicePort
from lightkube.resources.core_v1 import Service
from ops.testing import Harness

from charm import ConsulCharm
from k8s_resource_handlers import KubernetesServiceHandler, ServiceType


@pytest.fixture()
def harness():
    harness = Harness(ConsulCharm)
    yield harness
    harness.cleanup()


@pytest.fixture()
def mock_client():
    with patch("k8s_resource_handlers.Client") as mock:
        yield mock


@pytest.fixture()
def mock_resource_manager():
    with patch("k8s_resource_handlers.KubernetesResourceManager") as mock:
        yield mock


class TestKubernetesServiceHandler:
    """Test KubernetesServiceHandler class."""

    def test_init_with_nodeport(self, harness):
        """Test initialization with NodePort service type."""
        harness.begin()
        service_ports = [
            ServicePort(8301, name="serf-tcp", protocol="TCP"),
            ServicePort(8500, name="http", protocol="TCP"),
        ]

        handler = KubernetesServiceHandler(
            charm=harness.charm,
            service_ports=service_ports,
            service_type=ServiceType.NodePort,
        )

        assert handler._service_ports == service_ports
        assert handler._service_type == ServiceType.NodePort
        assert handler._service_name == f"{harness.charm.app.name}-lb"
        assert handler._service_label == f"{harness.charm.app.name}-lb"

    def test_init_with_loadbalancer(self, harness):
        """Test initialization with LoadBalancer service type."""
        harness.begin()
        service_ports = [
            ServicePort(8301, name="serf-tcp", protocol="TCP"),
        ]

        handler = KubernetesServiceHandler(
            charm=harness.charm,
            service_ports=service_ports,
            service_type=ServiceType.LoadBalancer,
        )

        assert handler._service_ports == service_ports
        assert handler._service_type == ServiceType.LoadBalancer
        assert handler._service_name == f"{harness.charm.app.name}-lb"
        assert handler._service_label == f"{harness.charm.app.name}-lb"

    def test_construct_service_nodeport(self, harness):
        """Test service construction with NodePort type."""
        harness.begin()
        service_ports = [
            ServicePort(8301, name="serf-tcp", protocol="TCP", nodePort=30301),
            ServicePort(8500, name="http", protocol="TCP", nodePort=30500),
        ]

        handler = KubernetesServiceHandler(
            charm=harness.charm,
            service_ports=service_ports,
            service_type=ServiceType.NodePort,
        )

        service = handler._construct_service()

        assert isinstance(service, Service)
        assert service.metadata.name == f"{harness.charm.app.name}-lb"
        assert service.metadata.namespace == harness.charm.model.name
        assert service.spec.type == "NodePort"
        assert service.spec.ports == service_ports
        assert service.spec.selector == {"app.kubernetes.io/name": harness.charm.app.name}

    def test_construct_service_loadbalancer(self, harness):
        """Test service construction with LoadBalancer type."""
        harness.begin()
        service_ports = [
            ServicePort(8301, name="serf-tcp", protocol="TCP"),
        ]

        handler = KubernetesServiceHandler(
            charm=harness.charm,
            service_ports=service_ports,
            service_type=ServiceType.LoadBalancer,
        )

        service = handler._construct_service()

        assert isinstance(service, Service)
        assert service.metadata.name == f"{harness.charm.app.name}-lb"
        assert service.spec.type == "LoadBalancer"
        assert service.spec.ports == service_ports

    def test_reconcile_service_as_leader(self, harness, mock_resource_manager):
        """Test service reconciliation when unit is leader."""
        harness.set_leader(True)
        harness.begin()

        service_ports = [ServicePort(8301, name="serf-tcp", protocol="TCP")]
        handler = KubernetesServiceHandler(
            charm=harness.charm,
            service_ports=service_ports,
            service_type=ServiceType.NodePort,
        )

        # Mock the lightkube client
        handler._lightkube_client = MagicMock()

        mock_klm = MagicMock()
        mock_resource_manager.return_value = mock_klm

        handler._reconcile_service(None)

        mock_klm.reconcile.assert_called_once()
        args = mock_klm.reconcile.call_args[0][0]
        assert len(args) == 1
        assert isinstance(args[0], Service)

    def test_reconcile_service_as_non_leader(self, harness, mock_resource_manager):
        """Test service reconciliation when unit is not leader - should not reconcile."""
        harness.set_leader(False)
        harness.begin()

        service_ports = [ServicePort(8301, name="serf-tcp", protocol="TCP")]
        handler = KubernetesServiceHandler(
            charm=harness.charm,
            service_ports=service_ports,
            service_type=ServiceType.NodePort,
        )

        mock_klm = MagicMock()
        mock_resource_manager.return_value = mock_klm

        handler._reconcile_service(None)

        mock_klm.reconcile.assert_not_called()

    def test_on_remove_as_leader(self, harness, mock_resource_manager):
        """Test service removal when unit is leader."""
        harness.set_leader(True)
        harness.begin()

        service_ports = [ServicePort(8301, name="serf-tcp", protocol="TCP")]
        handler = KubernetesServiceHandler(
            charm=harness.charm,
            service_ports=service_ports,
            service_type=ServiceType.NodePort,
        )

        # Mock the lightkube client
        handler._lightkube_client = MagicMock()

        mock_klm = MagicMock()
        mock_resource_manager.return_value = mock_klm

        handler._on_remove(None)

        mock_klm.delete.assert_called_once()

    def test_on_remove_as_non_leader(self, harness, mock_resource_manager):
        """Test service removal when unit is not leader - should not delete."""
        harness.set_leader(False)
        harness.begin()

        service_ports = [ServicePort(8301, name="serf-tcp", protocol="TCP")]
        handler = KubernetesServiceHandler(
            charm=harness.charm,
            service_ports=service_ports,
            service_type=ServiceType.NodePort,
        )

        mock_klm = MagicMock()
        mock_resource_manager.return_value = mock_klm

        handler._on_remove(None)

        mock_klm.delete.assert_not_called()

    def test_get_loadbalancer_ip_with_nodeport(self, harness, mock_client):
        """Test getting LoadBalancer IP with NodePort service type - should return None."""
        harness.begin()

        service_ports = [ServicePort(8301, name="serf-tcp", protocol="TCP")]
        handler = KubernetesServiceHandler(
            charm=harness.charm,
            service_ports=service_ports,
            service_type=ServiceType.NodePort,
        )

        ip = handler.get_loadbalancer_ip()

        assert ip is None
        mock_client.return_value.get.assert_not_called()

    def test_get_loadbalancer_ip_success(self, harness, mock_client):
        """Test successfully getting LoadBalancer IP."""
        harness.begin()

        service_ports = [ServicePort(8301, name="serf-tcp", protocol="TCP")]
        handler = KubernetesServiceHandler(
            charm=harness.charm,
            service_ports=service_ports,
            service_type=ServiceType.LoadBalancer,
        )

        # Mock the service with LoadBalancer IP
        mock_ingress = Mock()
        mock_ingress.ip = "203.0.113.10"

        mock_service = Mock()
        mock_service.status.loadBalancer.ingress = [mock_ingress]

        mock_client.return_value.get.return_value = mock_service

        ip = handler.get_loadbalancer_ip()

        assert ip == "203.0.113.10"
        mock_client.return_value.get.assert_called_once_with(
            Service,
            name=f"{harness.charm.app.name}-lb",
            namespace=harness.charm.model.name,
        )

    def test_get_loadbalancer_ip_no_ingress(self, harness, mock_client):
        """Test getting LoadBalancer IP when no ingress is available."""
        harness.begin()

        service_ports = [ServicePort(8301, name="serf-tcp", protocol="TCP")]
        handler = KubernetesServiceHandler(
            charm=harness.charm,
            service_ports=service_ports,
            service_type=ServiceType.LoadBalancer,
        )

        # Mock service with no ingress
        mock_service = Mock()
        mock_service.status.loadBalancer.ingress = None

        mock_client.return_value.get.return_value = mock_service

        ip = handler.get_loadbalancer_ip()

        assert ip is None

    def test_get_loadbalancer_ip_api_error(self, harness, mock_client):
        """Test getting LoadBalancer IP when API call fails."""
        harness.begin()

        service_ports = [ServicePort(8301, name="serf-tcp", protocol="TCP")]
        handler = KubernetesServiceHandler(
            charm=harness.charm,
            service_ports=service_ports,
            service_type=ServiceType.LoadBalancer,
        )

        from lightkube.core.exceptions import ApiError

        # Create a proper mock response for ApiError
        mock_response = Mock()
        mock_response.json.return_value = {
            "kind": "Status",
            "apiVersion": "v1",
            "status": "Failure",
            "code": 404,
        }

        mock_client.return_value.get.side_effect = ApiError(response=mock_response)

        ip = handler.get_loadbalancer_ip()

        assert ip is None

    def test_refresh_event_subscription(self, harness):
        """Test that handler subscribes to provided refresh events."""
        harness.begin()

        service_ports = [ServicePort(8301, name="serf-tcp", protocol="TCP")]

        # This should not raise an error
        handler = KubernetesServiceHandler(
            charm=harness.charm,
            service_ports=service_ports,
            service_type=ServiceType.NodePort,
            refresh_event=[harness.charm.on.config_changed],
        )

        # Verify the handler is properly initialized
        assert handler._service_type == ServiceType.NodePort

    def test_lightkube_client_property(self, harness, mock_client):
        """Test lightkube_client property lazy initialization."""
        harness.begin()

        service_ports = [ServicePort(8301, name="serf-tcp", protocol="TCP")]
        handler = KubernetesServiceHandler(
            charm=harness.charm,
            service_ports=service_ports,
            service_type=ServiceType.NodePort,
        )

        # First access should create client
        client1 = handler.lightkube_client
        assert client1 is not None

        # Second access should return the same client (cached)
        client2 = handler.lightkube_client
        assert client1 is client2
