# Copyright 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Handles management of kubernetes resources."""

import functools
import logging
from enum import Enum

from lightkube.core.client import (
    Client,
)
from lightkube.core.exceptions import (
    ApiError,
)
from lightkube.models.core_v1 import (
    ServicePort,
    ServiceSpec,
)
from lightkube.models.meta_v1 import (
    ObjectMeta,
)
from lightkube.resources.core_v1 import (
    Service,
)
from lightkube_extensions.batch import (  # type: ignore[import-untyped, import-not-found]
    KubernetesResourceManager,
    create_charm_default_labels,
)
from ops.charm import CharmBase
from ops.framework import (
    BoundEvent,
    Object,
)

logger = logging.getLogger(__name__)


class ServiceType(Enum):
    """Kubernetes Service types supported by the handler."""

    NodePort = "NodePort"
    LoadBalancer = "LoadBalancer"


class KubernetesServiceHandler(Object):
    """Manage Kubernetes services.

    Creates a new Kubernetes service of type NodePort or LoadBalancer
    with name as {app.name}-lb. Patch the service on
    events defined by the charm.
    Remove the kubernetes service on removal of application
    or the last unit.
    """

    def __init__(
        self,
        charm: CharmBase,
        service_ports: list[ServicePort],
        service_type: ServiceType,
        refresh_event: list[BoundEvent] | None = None,
    ):
        super().__init__(charm, "kubernetes-service-handler")
        self.charm = charm
        self._service_ports = service_ports
        self._service_type = service_type

        self._lightkube_client = None
        self._lightkube_field_manager: str = self.charm.app.name

        self._service_label = f"{self.charm.app.name}-lb"
        self._service_name: str = f"{self.charm.app.name}-lb"

        # apply user defined events
        if refresh_event:
            if not isinstance(refresh_event, list):
                refresh_event = [refresh_event]

            for evt in refresh_event:
                self.framework.observe(evt, self._reconcile_service)

        # Remove service if the last unit is removed
        self.framework.observe(charm.on.remove, self._on_remove)

    @property
    def lightkube_client(self):
        """Returns a lightkube client configured for this charm."""
        if self._lightkube_client is None:
            self._lightkube_client = Client(
                namespace=self.charm.model.name,
                field_manager=self._lightkube_field_manager,
            )
        return self._lightkube_client

    def _get_service_resource_manager(self):
        return KubernetesResourceManager(
            labels=create_charm_default_labels(
                self.charm.app.name,
                self.charm.model.name,
                scope=self._service_label,
            ),
            resource_types={Service},
            lightkube_client=self.lightkube_client,
            logger=logger,
        )

    def _construct_service(self) -> Service:
        return Service(
            metadata=ObjectMeta(
                name=f"{self._service_name}",
                namespace=self.charm.model.name,
                labels={"app.kubernetes.io/name": self.charm.app.name},
            ),
            spec=ServiceSpec(
                ports=self._service_ports,
                selector={"app.kubernetes.io/name": self.charm.app.name},
                type=self._service_type.value,
            ),
        )

    def _reconcile_service(self, _) -> None:
        """Reconcile the service's state."""
        if not self.charm.unit.is_leader():
            return

        klm = self._get_service_resource_manager()
        resources_list = [self._construct_service()]
        logger.info(f"Patching k8s service object {self._service_name}")
        klm.reconcile(resources_list)  # type: ignore[arg-type]

    def _on_remove(self, _) -> None:
        if not self.charm.unit.is_leader():
            return

        # juju scale down on kubernetes charms removes non-leader units.
        # So removal of leader unit can be considered as application is
        # getting destroyed or all the units are removed. Remove the
        # service in this case.
        logger.info(f"Removing k8s service object {self._service_name}")
        klm = self._get_service_resource_manager()
        klm.delete()

    @functools.cache
    def get_loadbalancer_ip(self) -> str | None:
        """Get loadbalancer IP.

        Result is cached for the whole duration of a hook.
        """
        if not self._service_type == ServiceType.LoadBalancer:
            return None

        try:
            svc = self.lightkube_client.get(
                Service, name=self._service_name, namespace=self.charm.model.name
            )
        except ApiError as e:
            logger.error(f"Failed to fetch LoadBalancer {self._service_name}: {e}")
            return None

        if not (status := getattr(svc, "status", None)):
            return None
        if not (load_balancer_status := getattr(status, "loadBalancer", None)):
            return None
        if not (ingress_addresses := getattr(load_balancer_status, "ingress", None)):
            return None
        if not (ingress_address := ingress_addresses[0]):
            return None

        return ingress_address.ip
