"""The early SSRF check (decision 3): the deny list, internal names and the allow list.

The deny list is the gateway's; these cases mirror its policy tests.
"""

from ipaddress import ip_address

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
    ["http://mock-backend", "mock-backend:9000", "10.0.0.0/33", "-x", "a..b", "under_score"],
)
def test_unreadable_allow_entries_are_refused(entry: str) -> None:
    with pytest.raises(ValueError, match="not a host name"):
        AllowList.parse(f"mock-backend,{entry}")
