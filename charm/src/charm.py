#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm the application.

Consul is a tool for service discovery, service
mesh, traffic monitoring and configuration.
The charm manages the consul deployment and
day 2 operations.

The charm deploys and configures consul as a server.
Supported features: Failure detection of nodes in
the cluster.
Service mesh, service discovery, and configuration are
not yet supported.
"""

import json
import logging
from typing import Union

from charms.consul_k8s.v0.consul_cluster import ConsulServiceProvider
from charms.observability_libs.v1.kubernetes_service_patch import KubernetesServicePatch
from charms.tls_certificates_interface.v3.tls_certificates import (
    AllCertificatesInvalidatedEvent,
    CertificateAvailableEvent,
    CertificateExpiringEvent,
    CertificateInvalidatedEvent,
    TLSCertificatesRequiresV3,
    generate_csr,
    generate_private_key,
)
from lightkube import Client
from lightkube.models.core_v1 import ServicePort
from lightkube.resources.core_v1 import Pod
from ops import main
from ops.charm import CharmBase, RelationCreatedEvent, RelationEvent
from ops.model import ActiveStatus, BlockedStatus, Port, WaitingStatus
from ops.pebble import ChangeError, Error, Layer

from config_builder import ConsulConfigBuilder, Ports

logger = logging.getLogger(__name__)

CONSUL_CONFIG_PATH = "/consul/config/server.json"


class ConsulCharm(CharmBase):
    """Consul charm class."""

    def __init__(self, *args):
        super().__init__(*args)
        self.name = "consul"
        self.ports: Ports = self.get_consul_ports()

        self.consul = ConsulServiceProvider(charm=self)
        self.service_patch = self.open_ports()

        self.framework.observe(self.on.consul_pebble_ready, self._on_consul_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade)
        self.framework.observe(self.consul.on.endpoints_request, self._on_endpoints_request)

        self.certificates = TLSCertificatesRequiresV3(self, "certificates")
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(
            self.on.certificates_relation_created, self._on_certificates_relation_created
        )
        self.framework.observe(
            self.certificates.on.certificate_available, self._on_certificate_available
        )
        self.framework.observe(
            self.certificates.on.certificate_expiring, self._on_certificate_expiring
        )
        self.framework.observe(
            self.certificates.on.certificate_invalidated, self._on_certificate_invalidated
        )
        self.framework.observe(
            self.certificates.on.all_certificates_invalidated,
            self._on_all_certificates_invalidated,
        )

    def get_consul_ports(self) -> Ports:
        """Return consul ports with supported values."""
        ports = {
            "dns": -1,  # Not supported
            "http": 8500,
            "https": -1,  # Not supported
            "grpc": -1,  # Not supported
            "grpc_tls": -1,  # Not supported
            "serf_lan": 8301,
            "serf_wan": -1,  # Not supported
            "server": 8300,
            "sidecar_min_port": 0,  # Not supported
            "sidecar_max_port": 0,  # Not supported
            "expose_min_port": 0,  # Not supported
            "expose_max_port": 0,  # Not supported
        }

        if self.config.get("expose-gossip-and-rpc-ports"):
            ports["serf_lan"] = self.config.get("serflan-node-port")  # pyright: ignore

        return Ports(**ports)

    def open_ports(self) -> KubernetesServicePatch | None:
        """Open necessary service ports.

        If config expose-gossip-and-rpc-ports is not set, expose
        ports as Cluster Service ports.
        Otherwise, expose ports as Node ports using KubernetesServicePatch
        and return the object.

        Ports that are opened: serf_lan tcp/udp, http.
        """
        if not self.config.get("expose-gossip-and-rpc-ports"):
            # Expose as ClusterService
            logger.info("Creating service ports as ClusterIP")
            self.unit.set_ports(
                Port("tcp", self.ports.serf_lan),
                Port("udp", self.ports.serf_lan),
                Port("tcp", self.ports.http),
            )
            return

        # TODO: Expose RPC ports and see how cluster agent clients can use that port
        node_ports = [
            ServicePort(
                self.ports.serf_lan,
                name=f"juju-{self.ports.serf_lan}-tcp",
                protocol="TCP",
                nodePort=self.ports.serf_lan,
            ),
            ServicePort(
                self.ports.serf_lan,
                name=f"juju-{self.ports.serf_lan}-udp",
                protocol="UDP",
                nodePort=self.ports.serf_lan,
            ),
            ServicePort(
                self.ports.http,
                name=f"juju-{self.ports.http}-tcp",
                protocol="TCP",
                targetPort=self.ports.http,
            ),
        ]

        # TODO: Can we change externaltrafficpolicy, internaltrafficpolicy? need change in lib
        logger.info(f"Creating service ports as NodePort: {node_ports}")
        return KubernetesServicePatch(
            self,
            node_ports,
            service_name=f"{self.model.app.name}",
            service_type="NodePort",  # type: ignore NodePort should be added in KuberenetesServicePatch library ServiceType
            refresh_event=self.on.config_changed,
        )

    def _on_consul_pebble_ready(self, _):
        self._configure()

    def _on_config_changed(self, _):
        # TODO: Validate if serflan-node-port is in range 30000-32767
        self._configure()

    def _on_upgrade(self, _):
        self._configure()

    def _on_endpoints_request(self, event: RelationEvent):
        """Send cluster endpoints to consul client."""
        self._set_endpoints_on_related_apps(event)

    def _update_status(self, status):
        if self.unit.is_leader():
            self.app.status = status
        self.unit.status = status

    def _configure(self):
        if not self.workload.can_connect():
            self._update_status(WaitingStatus("Waiting for Pebble ready"))
            return

        consul_config_changed = self._update_consul_config()
        pebble_layer_changed = self._update_pebble_layer()
        restart = any([consul_config_changed, pebble_layer_changed])

        if restart:
            try:
                logger.debug("Restarting the consul service")
                self.workload.restart(self.name)
            except ChangeError as e:
                msg = f"Failed to restart Consul: {e}"
                self._update_status(BlockedStatus(msg))
                logger.error(msg)
                return

        # Send updates on cluster join addresses/datacenter to all related apps.
        self._set_endpoints_on_related_apps()
        self._update_status(ActiveStatus())

    def _update_consul_config(self) -> bool:
        datacenter: str = self.config.get("datacenter")  # pyright: ignore
        number_of_units = self.model.app.planned_units()
        join_addresses = self._get_internal_join_addresses()
        tls_certificates = {
            "ca_certificate_path": "/consul/config/certs/ca.pem",
            "server_certificate_path": "/consul/config/certs/server-cert.pem",
            "server_key_path": "/consul/config/certs/server-key.pem",
        }
        consul_config = ConsulConfigBuilder(
            self.ports, datacenter, number_of_units, join_addresses, tls_certificates
        ).build()

        if self._running_consul_config == consul_config:
            return False

        self.workload.push(CONSUL_CONFIG_PATH, json.dumps(consul_config, indent=2), make_dirs=True)
        logger.info("Consul configuration file updated")
        return True

    def _update_pebble_layer(self) -> bool:
        current_layer = self.workload.get_plan()

        if current_layer.services == self._pebble_layer.services:
            return False

        self.workload.add_layer(self.name, self._pebble_layer, combine=True)
        logger.info("Pebble layer is updated")
        return True

    def _get_hostips_for_consul_service(self, app: str, namespace: str) -> set:
        hostips = set()

        client = Client()  # pyright: ignore
        pods = client.list(Pod, namespace=namespace, labels={"app.kubernetes.io/name": app})
        for pod in pods:
            pod_status = pod.status
            if pod_status and (hostip := pod_status.hostIP):
                hostips.add(hostip)

        logger.debug("Consul pods are running on Host IPs: {hostips}")
        return hostips

    def _get_internal_join_addresses(self) -> list[str]:
        """Get consul server join addresses.

        If the consul agents are within k8s cluster, internal service dns
        name with configured serf lan port will be returned.
        Return type is list of string as this may change in future to
        return Pod IP addresses instead of cluster ip.

        Return value should be in format [<IP/dns name>:<Port>, ...]
        """
        # Return ClusterIP dns service name
        return [f"{self.model.app.name}.{self.model.name}.svc:{self.ports.serf_lan}"]

    def _get_external_join_addresses(self) -> list[str] | None:
        """Get consul server join addresses exposed at node level.

        Return Host IPs and serf lan port if expose-gossip-and-rpc-ports
        is set to True.

        Return value should be in format [<IP/dns name>:<Port>, ...]
        """
        if self.config.get("expose-gossip-and-rpc-ports"):
            ip_addresses = self._get_hostips_for_consul_service(
                self.model.app.name, self.model.name
            )
            return [f"{ip_address}:{self.ports.serf_lan}" for ip_address in ip_addresses]

        return None

    def _get_internal_http_endpoint(self) -> str:
        # Return ClusterIP dns service name
        return f"{self.model.app.name}.{self.model.name}.svc:{self.ports.http}"

    def _get_exernal_http_endpoint(self) -> str | None:
        # Placeholder to send ingress endpoint once ingress relation is implemented
        return None

    def _set_endpoints_on_related_apps(self, event: RelationEvent | None = None):
        """Send cluster endpoints on the related app.

        If event is None, the cluster endpoints will be sent on all related apps.
        """
        # charm config have checks to determine if the value is string.
        # The config parameter also have default value and so datacenter
        # always return string, ignore the pyright static check.
        datacenter: str = self.config.get("datacenter")  # pyright: ignore

        internal_join_addresses = self._get_internal_join_addresses()
        external_join_addresses = self._get_external_join_addresses()
        internal_http_endpoint = self._get_internal_http_endpoint()
        external_http_endpoint = self._get_exernal_http_endpoint()

        relation = event.relation if event else None
        self.consul.set_cluster_endpoints(
            relation,
            datacenter,
            internal_join_addresses,
            external_join_addresses,
            internal_http_endpoint,
            external_http_endpoint,
        )

    def _on_install(self, event) -> None:
        private_key_password = b"banana"  # What do we want to set this to?
        private_key = generate_private_key(password=private_key_password)
        certificates_relation = self.model.get_relation("certificates")
        if not certificates_relation:
            self.unit.status = WaitingStatus("Waiting for peer relation to be created")
            event.defer()
            return
        certificates_relation.data[self.app].update(
            {"private_key_password": "banana", "private_key": private_key.decode()}
        )

    def _on_certificates_relation_created(self, event: RelationCreatedEvent) -> None:
        """Handle the creation of the TLS certificates relation."""
        certificates_relation = self.model.get_relation("certificates")
        if not certificates_relation:
            self.unit.status = WaitingStatus("Waiting for peer relation to be created")
            event.defer()
            return
        private_key_password = certificates_relation.data[self.app].get("private_key_password")
        private_key = certificates_relation.data[self.app].get("private_key")
        csr = generate_csr(
            private_key=private_key.encode(),
            private_key_password=private_key_password.encode(),
            subject=self.cert_subject,
        )
        certificates_relation.data[self.app].update({"csr": csr.decode()})
        self.certificates.request_certificate_creation(certificate_signing_request=csr)

    def _on_certificate_available(self, event: CertificateAvailableEvent) -> None:
        certificates_relation = self.model.get_relation("certificates")
        if not certificates_relation:
            self.unit.status = WaitingStatus("Waiting for peer relation to be created")
            event.defer()
            return

        cert_dir = "/consul/config/certs"
        cert_file = f"{cert_dir}/server-cert.pem"
        key_file = f"{cert_dir}/server-key.pem"
        ca_file = f"{cert_dir}/ca.pem"

        private_key = certificates_relation.data[self.app].get("private_key")

        self.workload.push(cert_file, event.certificate, make_dirs=True)
        self.workload.push(key_file, private_key, make_dirs=True)
        self.workload.push(ca_file, event.ca, make_dirs=True)

        self.unit.status = ActiveStatus()

    def _on_certificate_expiring(
        self, event: Union[CertificateExpiringEvent, CertificateInvalidatedEvent]
    ) -> None:
        certificates_relation = self.model.get_relation("certificates")
        if not certificates_relation:
            self.unit.status = WaitingStatus("Waiting for peer relation to be created")
            event.defer()
            return
        old_csr = certificates_relation.data[self.app].get("csr")
        private_key_password = certificates_relation.data[self.app].get("private_key_password")
        private_key = certificates_relation.data[self.app].get("private_key")
        new_csr = generate_csr(
            private_key=private_key.encode(),
            private_key_password=private_key_password.encode(),
            subject=self.cert_subject,
        )
        self.certificates.request_certificate_renewal(
            old_certificate_signing_request=old_csr,
            new_certificate_signing_request=new_csr,
        )
        certificates_relation.data[self.app].update({"csr": new_csr.decode()})

    def _certificate_revoked(self) -> None:
        certificates_relation = self.model.get_relation("certificates")
        old_csr = certificates_relation.data[self.app].get("csr")
        private_key_password = certificates_relation.data[self.app].get("private_key_password")
        private_key = certificates_relation.data[self.app].get("private_key")
        new_csr = generate_csr(
            private_key=private_key.encode(),
            private_key_password=private_key_password.encode(),
            subject=self.cert_subject,
        )
        self.certificates.request_certificate_renewal(
            old_certificate_signing_request=old_csr,
            new_certificate_signing_request=new_csr,
        )
        certificates_relation.data[self.app].update({"csr": new_csr.decode()})
        certificates_relation.data[self.app].pop("certificate")
        certificates_relation.data[self.app].pop("ca")
        certificates_relation.data[self.app].pop("chain")
        self.unit.status = WaitingStatus("Waiting for new certificate")

    def _on_certificate_invalidated(self, event: CertificateInvalidatedEvent) -> None:
        certificates_relation = self.model.get_relation("certificates")
        if not certificates_relation:
            self.unit.status = WaitingStatus("Waiting for peer relation to be created")
            event.defer()
            return
        if event.reason == "revoked":
            self._certificate_revoked()
        if event.reason == "expired":
            self._on_certificate_expiring(event)

    def _on_all_certificates_invalidated(self, event: AllCertificatesInvalidatedEvent) -> None:
        # Do what you want with this information, probably remove all certificates.
        pass

    @property
    def _pebble_layer(self) -> Layer:
        # TODO: Add health checks
        # curl http://127.0.0.1:8500/v1/status/leader
        command = f"consul agent -config-file {CONSUL_CONFIG_PATH}"
        return Layer(
            {
                "summary": "consul layer",
                "description": "pebble config layer for the consul",
                "services": {
                    self.name: {
                        "override": "replace",
                        "summary": "consul",
                        "command": command,
                        "startup": "enabled",
                    }
                },
            }
        )

    @property
    def _running_consul_config(self) -> dict:
        """Get the on-disk Consul config."""
        if not self.workload.can_connect():
            return {}

        try:
            return json.loads(self.workload.pull(CONSUL_CONFIG_PATH, encoding="utf-8").read())
        except (FileNotFoundError, Error) as e:
            logger.error("Failed to retrieve Consul config %s", e)
            return {}

    @property
    def workload(self):
        """The main workload of the charm."""
        return self.unit.get_container(self.name)


if __name__ == "__main__":
    main(ConsulCharm)
