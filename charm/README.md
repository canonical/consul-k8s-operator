# Consul Kubernetes Charmed Operator

This [Juju](https://juju.is) charmed operator deploys and manages [Consul](https://www.consul.io/) on a kubernetes platform.
Consul provides service discovery, service mesh, traffic management, configuration, failure detection of nodes capabilities.

The charmed operator deploys and manages consul agent in server mode on k8s environment.

Currently supported features:
* Failure detection of nodes using serf/gossip protocol
* Expose serf/gossip endpoints over Node ports (to join External services on cluster) 

Planned features:
* Enable Service mesh configuration (Includes installing necessary CRDs)
* Enable DNS service
* Enable Consul UI
* Expose Consul HTTP/HTTPS server via Ingress
* Enable TLS based communication
* Enable consul clustering over WAN


## Usage

```sh
juju deploy ./consul-k8s_amd64.charm consul-server --trust --resource consul-image=ghcr.io/canonical/consul:1.19.2
```

## OCI Images

This charm by default uses the latest version of the [canonical/consul](https://ghcr.io/canonical/consul) image.

## Configurations

* `datacenter` allows user to set a datacenter name for the consul cluster.
* `expose-gossip-and-rpc-ports` allows user to expose consul gossip ports over the node port so that consul agents external
to kubernetes can be joined to the consul cluster.
* `serflan-node-port` allows user to set node port for gossip protocol communication.

## Relations

### Providing Consul cluster config

* `consul-cluster`: Provides cluster server endpoints to the related apps.
  Serf/gossip and http internal/external endpoints are provided over relation data.
  This is useful for external consul clients to join the consul cluster.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this
charm following best practice guidelines, and
[CONTRIBUTING.md](https://github.com/canonical/catalogue-k8s-operator/blob/main/CONTRIBUTING.md) for developer
guidance.
