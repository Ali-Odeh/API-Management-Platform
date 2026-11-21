"""Which upstream hosts a tenant may save (decision 3, SSRF protection).

The real protection is in the gateway: it checks the IP address it actually
connects to. This early check only gives a clear 422 at save time for what is
plainly internal: an IP literal on the deny list, or a name that is internal
by definition (``localhost``, ``*.localhost``, ``*.internal``). Any other name
(``redis``, ``orders.example.com``) passes here; the gateway decides once DNS
has answered.

The deny list is the gateway's (``internal/egress/policy.go``); keep the two
in step.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network

_DENIED: tuple[IPNetwork, ...] = tuple(
    ip_network(cidr)
    for cidr in (
        # IPv4
        "0.0.0.0/8",  # "this network"
        "10.0.0.0/8",  # private
        "100.64.0.0/10",  # carrier-grade NAT
        "127.0.0.0/8",  # loopback
        "169.254.0.0/16",  # link-local, cloud metadata (169.254.169.254)
        "172.16.0.0/12",  # private (Docker's networks)
        "192.168.0.0/16",  # private
        "224.0.0.0/4",  # multicast
        "240.0.0.0/4",  # reserved, and the broadcast address
        # IPv6
        "::/128",  # unspecified
        "::1/128",  # loopback
        "fc00::/7",  # unique local (private)
        "fe80::/10",  # link-local
        "ff00::/8",  # multicast
    )
)

# IPv6 addresses carrying an IPv4 address, judged by the IPv4 address inside.
_IPV4_COMPATIBLE = ip_network("::/96")
_NAT64 = (ip_network("64:ff9b::/96"), ip_network("64:ff9b:1::/48"))
_SIX_TO_FOUR = ip_network("2002::/16")

_INTERNAL_NAMES = frozenset({"localhost"})
_INTERNAL_SUFFIXES = (".localhost", ".internal")

_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def is_denied(ip: IPAddress) -> bool:
    """Whether ip is internal, local or special by default."""
    if isinstance(ip, IPv6Address):
        if ip.ipv4_mapped is not None:  # ::ffff:10.0.0.1 is 10.0.0.1
            return is_denied(ip.ipv4_mapped)
        inner = _embedded_ipv4(ip)
        if inner is not None and is_denied(inner):
            return True
    return any(ip.version == net.version and ip in net for net in _DENIED)


def _embedded_ipv4(ip: IPv6Address) -> IPv4Address | None:
    if ip in _IPV4_COMPATIBLE or any(ip in net for net in _NAT64):
        return IPv4Address(ip.packed[12:])
    if ip in _SIX_TO_FOUR:
        return IPv4Address(ip.packed[2:6])
    return None


def _normalize(host: str) -> str:
    return host.strip().lower().removeprefix("[").removesuffix("]").rstrip(".")


def _is_host_name(name: str) -> bool:
    return 0 < len(name) <= 253 and all(_LABEL.match(label) for label in name.split("."))


@dataclass(frozen=True)
class AllowList:
    """UPSTREAM_ALLOW: internal hosts and networks the Platform Admin allows."""

    hosts: frozenset[str] = frozenset()
    networks: tuple[IPNetwork, ...] = field(default_factory=tuple)

    @classmethod
    def parse(cls, value: str) -> "AllowList":
        """Read a comma-separated list of names, addresses and networks.

        Raises ValueError naming the first entry that is none of them.
        """
        hosts: set[str] = set()
        networks: list[IPNetwork] = []
        for raw in value.split(","):
            entry = raw.strip()
            if not entry:
                continue
            try:
                networks.append(ip_network(entry, strict=False))
                continue
            except ValueError:
                pass
            name = _normalize(entry)
            if not _is_host_name(name):
                raise ValueError(
                    f"{entry!r} is not a host name, an IP address or a network "
                    "such as 10.20.0.0/16"
                )
            hosts.add(name)
        return cls(hosts=frozenset(hosts), networks=tuple(networks))

    def allows_ip(self, ip: IPAddress) -> bool:
        if isinstance(ip, IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if any(ip.version == net.version and ip in net for net in self.networks):
            return True
        return not is_denied(ip)


@lru_cache
def parse_allow_list(value: str) -> AllowList:
    """AllowList.parse, once per value (settings don't change while running)."""
    return AllowList.parse(value)


def internal_host_reason(host: str, allow: AllowList) -> str | None:
    """Why host must not be saved as an upstream, or None if it may.

    host is the URL's host as parsed (an IPv6 literal may keep its brackets).
    """
    name = _normalize(host)
    if name in allow.hosts:
        return None
    try:
        ip: IPAddress | None = ip_address(name.split("%", 1)[0])  # drop an IPv6 zone
    except ValueError:
        ip = None
    if ip is not None:
        return None if allow.allows_ip(ip) else f"{ip} is an internal address"
    if name in _INTERNAL_NAMES or name.endswith(_INTERNAL_SUFFIXES):
        return f"{name} is an internal name"
    return None
