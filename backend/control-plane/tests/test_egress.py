"""The early SSRF check (decision 3): the deny list, internal names and the allow list.

The deny list is the gateway's; these cases mirror its policy tests.
"""

import random
from ipaddress import ip_address, ip_interface

import pytest

from app.core.egress import AllowList, internal_host_reason, is_denied

DENIED = [
    "0.0.0.0",
    "10.0.0.1",
    "100.64.0.1",
    "127.0.0.1",
    "169.254.169.254",
    "172.16.0.1",
    "172.18.0.5",
    "172.31.255.255",
    "192.168.1.1",
    "224.0.0.1",
    "240.0.0.1",
    "255.255.255.255",
    "::",
    "::1",
    "fc00::1",
    "fe80::1",
    "ff02::1",
    "::ffff:127.0.0.1",
    "::127.0.0.1",
    "64:ff9b::a00:1",
    "64:ff9b:1::a9fe:a9fe",
    "2002:c0a8:101::1",
]
PUBLIC = [
    "8.8.8.8",
    "100.63.255.255",
    "100.128.0.0",
    "172.15.255.255",
    "172.32.0.0",
    "192.169.0.0",
    "223.255.255.255",
    "::ffff:8.8.8.8",
    "64:ff9b::808:808",
    "2002:808:808::1",
    "2606:4700::1111",
    "fec0::1",
]


@pytest.mark.parametrize("addr", DENIED)
def test_internal_addresses_are_denied(addr: str) -> None:
    assert is_denied(ip_address(addr))


@pytest.mark.parametrize("addr", PUBLIC)
def test_public_addresses_are_not(addr: str) -> None:
    assert not is_denied(ip_address(addr))


NOBODY = AllowList()


@pytest.mark.parametrize(
    ("host", "refused"),
    [
        ("127.0.0.1", True),
        ("[::1]", True),
        ("[::ffff:7f00:1]", True),
        ("169.254.169.254", True),
        ("localhost", True),
        ("LOCALHOST.", True),
        ("api.localhost", True),
        ("metadata.google.internal", True),
        # Names are the gateway's to judge, once DNS has answered.
        ("redis", False),
        ("mock-backend", False),
        ("orders.example.com", False),
        ("internal.example.com", False),
        ("8.8.8.8", False),
        ("[2606:4700::1111]", False),
    ],
)
def test_internal_host_reason(host: str, refused: bool) -> None:
    assert (internal_host_reason(host, NOBODY) is not None) is refused


def test_the_allow_list_opens_names_and_networks() -> None:
    allow = AllowList.parse(" orders.internal , 10.20.0.0/16,192.168.1.7, fd00::/64,,")

    assert internal_host_reason("orders.internal", allow) is None
    assert internal_host_reason("ORDERS.INTERNAL.", allow) is None
    assert internal_host_reason("10.20.3.4", allow) is None
    assert internal_host_reason("[::ffff:a14:304]", allow) is None  # 10.20.3.4, mapped
    assert internal_host_reason("192.168.1.7", allow) is None
    assert internal_host_reason("[fd00::5]", allow) is None
    assert internal_host_reason("10.21.0.1", allow) == "10.21.0.1 is an internal address"
    assert internal_host_reason("other.internal", allow) == "other.internal is an internal name"


@pytest.mark.parametrize(
    "entry",
    ["http://mock-backend", "mock-backend:9000", "10.0.0.0/33", "-x", "a..b", "a b"],
)
def test_unreadable_allow_entries_are_refused(entry: str) -> None:
    with pytest.raises(ValueError, match="not a host name"):
        AllowList.parse(f"mock-backend,{entry}")


@pytest.mark.parametrize(
    ("entry", "message"),
    [("fe80::1%eth0", "zone"), ("::ffff:10.0.0.0/8", "/96 or longer")],
)
def test_allow_entries_that_could_never_match_are_refused(entry: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        AllowList.parse(entry)


def test_mapped_networks_and_underscored_names_can_be_allowed() -> None:
    allow = AllowList.parse("::ffff:10.20.0.0/112, ::ffff:192.168.1.7, billing_api")

    assert internal_host_reason("10.20.3.4", allow) is None
    assert internal_host_reason("[::ffff:a14:304]", allow) is None
    assert internal_host_reason("192.168.1.7", allow) is None
    assert internal_host_reason("10.21.0.1", allow) is not None
    assert internal_host_reason("billing_api", allow) is None
    assert internal_host_reason("BILLING_API.", allow) is None


# ---- Randomized checks (the gateway fuzzes the same rules with go test -fuzz)


def _reference_denied(raw: bytes) -> bool:
    """The deny list stated byte by byte, independent of the network list."""
    if len(raw) == 4:
        a, b = raw[0], raw[1]
        return (
            a in (0, 10, 127)
            or a >= 224  # multicast, reserved, broadcast
            or (a == 100 and b & 0xC0 == 64)  # 100.64.0.0/10
            or (a == 169 and b == 254)
            or (a == 172 and b & 0xF0 == 16)  # 172.16.0.0/12
            or (a == 192 and b == 168)
        )
    if raw[:10] == bytes(10) and raw[10:12] == b"\xff\xff":  # ::ffff:a.b.c.d
        return _reference_denied(raw[12:])
    if raw == bytes(16) or raw == bytes(15) + b"\x01":  # :: and ::1
        return True
    if raw[0] & 0xFE == 0xFC or raw[0] == 0xFF or (raw[0] == 0xFE and raw[1] & 0xC0 == 0x80):
        return True  # unique local, multicast, link-local
    if raw[:12] == bytes(12):  # ::a.b.c.d
        return _reference_denied(raw[12:])
    if raw[:4] == b"\x00\x64\xff\x9b" and (raw[4:12] == bytes(8) or raw[4:6] == b"\x00\x01"):
        return _reference_denied(raw[12:])  # NAT64
    if raw[:2] == b"\x20\x02":  # 6to4
        return _reference_denied(raw[2:6])
    return False


def test_random_addresses_match_the_reference() -> None:
    rng = random.Random(9)  # fixed: a failure is reproducible
    prefixes = [
        b"",
        b"\x00" * 10 + b"\xff\xff",
        b"\x00" * 12,
        b"\x00\x64\xff\x9b" + bytes(8),
        b"\x20\x02",
    ]
    for _ in range(20_000):
        if rng.random() < 0.4:
            raw = bytes(rng.getrandbits(8) for _ in range(4))
        else:
            head = rng.choice(prefixes)
            raw = head + bytes(rng.getrandbits(8) for _ in range(16 - len(head)))
        ip = ip_address(raw)
        host = str(ip) if ip.version == 4 else f"[{ip}]"
        refused = internal_host_reason(host, NOBODY) is not None
        assert refused == _reference_denied(raw), f"{ip}: refused={refused}"


def test_random_allow_lists_allow_what_they_accept() -> None:
    rng = random.Random(10)
    pieces = [
        "mock-backend",
        "billing_api",
        "x.internal.",
        "10.20.0.0/16",
        "192.168.1.7",
        "::ffff:10.0.0.0/104",
        "fd00::/64",
        "0.0.0.0/0",
        "a..b",
        "-x",
        "fe80::1%eth0",
        "10.0.0.0/33",
        " ",
        "",
        "::1",
        "127.0.0.1/8",
    ]
    for _ in range(5_000):
        entries = [rng.choice(pieces) for _ in range(rng.randint(1, 4))]
        try:
            allow = AllowList.parse(",".join(entries))
        except ValueError:
            continue
        for entry in (e.strip() for e in entries):
            if not entry:
                continue
            try:
                host = str(ip_interface(entry).ip)
            except ValueError:
                host = entry
            if ":" in host:
                host = f"[{host}]"
            assert internal_host_reason(host, allow) is None, f"{entries}: {entry} not allowed"
