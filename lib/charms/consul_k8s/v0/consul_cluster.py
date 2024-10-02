"""ConsulCluster Provides and Requires module.

This library contains Provider and Requirer classes for
consul-cluster interface.

The provider side updates cluster configuration required
by consul clients in the relation data.

Import `ConsulConfigRequirer` in your charm, with the charm object and the
relation name:
    - self
    - "consul-cluster"

Two events are also available to respond to:
    - config_changed
    - goneaway

A basic example showing the usage of this relation follows:

```
from charms.consul_k8s.v0.consul_cluster import (
    ConsulConfigRequirer
)

class ConsulClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # ConsulConfig Requires
        self.consul = ConsulConfigRequirer(
            self, "consul-cluster",
        )
        self.framework.observe(
            self.consul.on.config_changed,
            self._on_consul_service_config_changed
        )
        self.framework.observe(
            self.consul.on.goneaway,
            self._on_consul_service_goneaway
        )

    def _on_consul_service_config_changed(self, event):
        '''React to the Consul service config changed event.

        This event happens when consul-cluster relation is added to the
        model and relation data is changed.
        '''
        # Do something with the configuration provided by relation.
        pass

    def _on_consul_service_goneaway(self, event):
        '''React to the ConsulService goneaway event.

        This event happens when consul-cluster relation is removed.
        '''
        # ConsulService Relation has goneaway.
        pass
```
"""

import json
import logging

from ops.charm import CharmBase, RelationBrokenEvent, RelationChangedEvent, RelationEvent
from ops.framework import EventSource, Object, ObjectEvents
from ops.model import Relation
from pydantic import BaseModel, Field, ValidationError, field_validator

# The unique Charmhub library identifier, never change it
LIBID = "f10432d106524b82ba68aa6eddbc3308"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

DEFAULT_RELATION_NAME = "consul-cluster"

logger = logging.getLogger(__name__)


class ConsulConfigProviderAppData(BaseModel):
    """Consul config from Consul server."""

    datacenter: str = Field("Datacenter cluster name")
    server_join_addresses: list[str] = Field("Consul server join addresses")

    @field_validator("server_join_addresses", mode="before")
    @classmethod
    def convert_str_to_list_of_str(cls, v: str) -> list[str]:
        """Convert string field to list of str."""
        if not isinstance(v, str):
            return v

        try:
            return json.loads(v)
        except json.decoder.JSONDecodeError:
            raise ValueError("Field not in json format")


class ClusterConfigChangedEvent(RelationEvent):
    """Consul cluster config changed event."""

    pass


class ClusterServerGoneAwayEvent(RelationEvent):
    """Cluster server relation gone away event."""

    pass


class ConsulConfigRequirerEvents(ObjectEvents):
    """Consul Cluster requirer events."""

    config_changed = EventSource(ClusterConfigChangedEvent)
    goneaway = EventSource(ClusterServerGoneAwayEvent)


class ConsulConfigRequirer(Object):
    """Class to be instantiated on the requirer side of the relation."""

    on = ConsulConfigRequirerEvents()  # pyright: ignore

    def __init__(self, charm: CharmBase, relation_name: str = DEFAULT_RELATION_NAME):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

        events = self.charm.on[relation_name]
        self.framework.observe(events.relation_changed, self._on_relation_changed)
        self.framework.observe(events.relation_broken, self._on_relation_changed)

    def _on_relation_changed(self, event: RelationChangedEvent):
        if self._validate_databag_from_relation():
            self.on.config_changed.emit(event.relation)

    def _on_relation_broken(self, event: RelationBrokenEvent):
        """Handle relation broken event."""
        self.on.goneaway.emit()

    def _validate_databag_from_relation(self) -> bool:
        try:
            if self._consul_cluster_rel:
                databag = self._consul_cluster_rel.data[self._consul_cluster_rel.app]
                ConsulConfigProviderAppData(**databag)  # type: ignore
        except ValidationError as e:
            logger.info(f"Incorrect app databag: {str(e)}")
            return False

        return True

    def _get_app_databag_from_relation(self) -> dict:
        try:
            if self._consul_cluster_rel:
                databag = self._consul_cluster_rel.data[self._consul_cluster_rel.app]
                data = ConsulConfigProviderAppData(**databag)  # type: ignore
                return data.model_dump()
        except ValidationError as e:
            logger.info(f"Incorrect app databag: {str(e)}")

        return {}

    @property
    def _consul_cluster_rel(self) -> Relation | None:
        """The Consul cluster relation."""
        return self.framework.model.get_relation(self.relation_name)

    @property
    def datacenter(self) -> str | None:
        """Return datacenter name from provider app data."""
        data = self._get_app_databag_from_relation()
        return data.get("datacenter")

    @property
    def server_join_addresses(self) -> list[str] | None:
        """Return server join addresses from provider app data."""
        data = self._get_app_databag_from_relation()
        return data.get("server_join_addresses")


class ClusterConfigRequestEvent(RelationEvent):
    """Consul cluster config request event."""

    pass


class ConsulConfigProviderEvents(ObjectEvents):
    """Events class for `on`."""

    config_request = EventSource(ClusterConfigRequestEvent)


class ConsulConfigProvider(Object):
    """Class to be instantiated on the provider side of the relation."""

    on = ConsulConfigProviderEvents()  # pyright: ignore

    def __init__(self, charm: CharmBase, relation_name: str = DEFAULT_RELATION_NAME):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

        events = self.charm.on[relation_name]
        self.framework.observe(events.relation_changed, self._on_relation_changed)

    def _on_relation_changed(self, event: RelationChangedEvent):
        """Handle new cluster client connect."""
        self.on.config_request.emit(event.relation)

    def set_cluster_config(
        self, relation: Relation | None, datacenter: str, server_join_addresses: list[str]
    ) -> None:
        """Set consul cluster configuration on the relation.

        If relation is None, send cluster config on all related units.
        """
        if not self.charm.unit.is_leader():
            logging.debug("Not a leader unit, skipping set config")
            return

        try:
            databag = ConsulConfigProviderAppData(
                datacenter=datacenter, server_join_addresses=server_join_addresses
            )
        except ValidationError as e:
            logger.info(f"Provider trying to set incorrect app data {str(e)}")
            return

        # If relation is not provided send config to all the related
        # applications. This happens usually when config data is
        # updated by provider and wants to send the data to all
        # related applications
        # data = databag.model_dump()
        datacenter = databag.datacenter
        server_addresses = json.dumps(databag.server_join_addresses)
        if relation is None:
            logging.debug(
                "Sending config to all related applications of relation" f"{self.relation_name}"
            )
            for relation in self.framework.model.relations[self.relation_name]:
                if relation:
                    relation.data[self.charm.app]["datacenter"] = datacenter
                    relation.data[self.charm.app]["server_join_addresses"] = server_addresses
        else:
            logging.debug(
                f"Sending config on relation {relation.app.name} " f"{relation.name}/{relation.id}"
            )
            relation.data[self.charm.app]["datacenter"] = datacenter
            relation.data[self.charm.app]["server_join_addresses"] = server_addresses
