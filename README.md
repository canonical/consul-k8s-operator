# Consul Kubernetes Charmed Operator

This [Juju](https://juju.is) charmed operator deploys and manages [Consul](https://www.consul.io/) on a kubernetes platform.
Consul provides service discovery, service mesh, traffic management, configuration, failure detection of nodes capabilities.

The charmed operator currently supports failure detection of nodes.

## Usage

```sh
juju deploy ./consul-k8s_ubuntu-22.04-amd64.charm consul-server --trust --resource consul-image=docker.io/hashicorp/consul:1.19.2
```

## OCI Images

TODO

## Configurations

* `datacenter` allows user to set a datacenter name for the consul cluster.
* `expose-gossip-and-rpc-ports` allows user to expose consul gossip ports over the node port so that consul agents external
to kubernetes can be joined to the consul cluster.
* `serflan-node-port` allows user to set node port for gossip protocol communication.

## Relations

### Providing Consul cluster config

* `consul-cluster`: Provides cluster server join addresses to the related apps.
  This is useful for external consul clients to join the consul cluster.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this
charm following best practice guidelines, and
[CONTRIBUTING.md](https://github.com/canonical/catalogue-k8s-operator/blob/main/CONTRIBUTING.md) for developer
guidance.
