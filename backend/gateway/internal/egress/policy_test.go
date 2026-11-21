package egress

import (
	"net/netip"
	"strings"
	"testing"
)

func TestDeniedAddresses(t *testing.T) {
	tests := []struct {
		addr   string
		denied bool
	}{
		// The deny list of PLAN.md section 7, at its edges.
		{"0.0.0.0", true},
		{"0.255.255.255", true},
		{"10.0.0.1", true},
		{"10.255.255.255", true},
		{"100.64.0.1", true},
		{"100.127.255.255", true},
		{"127.0.0.1", true},
		{"127.255.255.254", true},
		{"169.254.169.254", true}, // cloud metadata
		{"172.16.0.1", true},
		{"172.18.0.5", true}, // a Docker container
		{"172.31.255.255", true},
		{"192.168.1.1", true},
		{"224.0.0.1", true},
		{"239.255.255.255", true},
		{"240.0.0.1", true},
		{"255.255.255.255", true},
		{"::", true},
		{"::1", true},
		{"fc00::1", true},
		{"fd12:3456::1", true},
		{"fe80::1", true},
		{"fe80::1%eth0", true},
		{"ff02::1", true},
		// IPv4 inside IPv6.
		{"::ffff:127.0.0.1", true},
		{"::ffff:10.0.0.1", true},
		{"::127.0.0.1", true},          // IPv4-compatible
		{"64:ff9b::a00:1", true},       // NAT64 to 10.0.0.1
		{"64:ff9b:1::a9fe:a9fe", true}, // local NAT64 to 169.254.169.254
		{"2002:c0a8:101::1", true},     // 6to4 of 192.168.1.1
		// Public addresses, just outside the ranges.
		{"8.8.8.8", false},
		{"9.255.255.255", false},
		{"11.0.0.0", false},
		{"100.63.255.255", false},
		{"100.128.0.0", false},
		{"172.15.255.255", false},
		{"172.32.0.0", false},
		{"192.167.255.255", false},
		{"192.169.0.0", false},
		{"223.255.255.255", false},
		{"::ffff:8.8.8.8", false},
		{"64:ff9b::808:808", false}, // NAT64 to 8.8.8.8
		{"2002:808:808::1", false},  // 6to4 of 8.8.8.8
		{"2606:4700::1111", false},
		{"fbff:ffff::1", false},
		{"fec0::1", false}, // outside fe80::/10
	}
	for _, tc := range tests {
		if got := Denied(netip.MustParseAddr(tc.addr)); got != tc.denied {
			t.Errorf("Denied(%s) = %v, want %v", tc.addr, got, tc.denied)
		}
	}
	if !Denied(netip.Addr{}) {
		t.Error("the zero Addr must be denied")
	}
}

func TestPolicyExceptions(t *testing.T) {
	p, err := NewPolicy([]string{"mock-backend", " Orders.Internal. ", "10.20.0.0/16", "192.168.1.7", "fd00::/64", ""})
	if err != nil {
		t.Fatalf("NewPolicy: %v", err)
	}

	for host, want := range map[string]bool{
		"mock-backend":    true,
		"MOCK-BACKEND.":   true,
		"orders.internal": true,
		"redis":           false,
		"mock-backend2":   false,
		"10.20.0.1":       false, // addresses are allowed by AllowsAddr, not by name
	} {
		if got := p.AllowsHost(host); got != want {
			t.Errorf("AllowsHost(%q) = %v, want %v", host, got, want)
		}
	}
	for addr, want := range map[string]bool{
		"10.20.3.4":        true,
		"::ffff:10.20.3.4": true,
		"10.21.0.1":        false,
		"192.168.1.7":      true,
		"192.168.1.8":      false,
		"fd00::5":          true,
		"fd00:0:0:1::5":    false,
		"127.0.0.1":        false,
		"8.8.8.8":          true, // public: not on the deny list
		"169.254.169.254":  false,
		"fe80::1%eth0":     false,
	} {
		if got := p.AllowsAddr(netip.MustParseAddr(addr)); got != want {
			t.Errorf("AllowsAddr(%s) = %v, want %v", addr, got, want)
		}
	}
}

func TestNewPolicyRefusesUnreadableEntries(t *testing.T) {
	for _, entry := range []string{"http://mock-backend", "mock-backend:9000", "10.0.0.0/33", "-x", "a..b", "a b", strings.Repeat("a", 64)} {
		if _, err := NewPolicy([]string{entry}); err == nil || !strings.Contains(err.Error(), "not a host name") {
			t.Errorf("NewPolicy(%q) error = %v, want one naming the entry", entry, err)
		}
	}
}

// An entry that could never match is refused at start, so an exception that
// looks set always works.
func TestNewPolicyRefusesEntriesThatCouldNeverMatch(t *testing.T) {
	for entry, want := range map[string]string{
		"fe80::1%eth0":      "zone",
		"::ffff:10.0.0.0/8": "/96 or longer",
	} {
		if _, err := NewPolicy([]string{entry}); err == nil || !strings.Contains(err.Error(), want) {
			t.Errorf("NewPolicy(%q) error = %v, want one mentioning %q", entry, err, want)
		}
	}
}

// An IPv4-mapped network means its IPv4 network; Docker and internal names
// with '_' can be allowed.
func TestNewPolicyAcceptsMappedNetworksAndUnderscores(t *testing.T) {
	p, err := NewPolicy([]string{"::ffff:10.20.0.0/112", "::ffff:192.168.1.7", "billing_api"})
	if err != nil {
		t.Fatalf("NewPolicy: %v", err)
	}
	for addr, want := range map[string]bool{"10.20.3.4": true, "::ffff:10.20.3.4": true, "10.21.0.1": false, "192.168.1.7": true} {
		if got := p.AllowsAddr(netip.MustParseAddr(addr)); got != want {
			t.Errorf("AllowsAddr(%s) = %v, want %v", addr, got, want)
		}
	}
	if !p.AllowsHost("billing_api") || !p.AllowsHost("BILLING_API.") {
		t.Error("billing_api is not allowed")
	}
}

// FuzzNewPolicy feeds UPSTREAM_ALLOW random lists: it never panics, and
// every entry it accepts is really allowed afterwards (by name, address or
// network), so no exception that looks set silently fails.
func FuzzNewPolicy(f *testing.F) {
	for _, seed := range []string{"mock-backend", "10.20.0.0/16, billing_api", "::ffff:10.0.0.0/104", "fe80::1%eth0", "192.168.1.7,,x.internal.", "0.0.0.0/0", "a..b"} {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, list string) {
		entries := strings.Split(list, ",")
		p, err := NewPolicy(entries)
		if err != nil {
			return
		}
		for _, entry := range entries {
			entry = strings.TrimSpace(entry)
			if entry == "" {
				continue
			}
			if prefix, err := netip.ParsePrefix(entry); err == nil {
				if addr := prefix.Masked().Addr(); !p.AllowsAddr(addr) {
					t.Errorf("accepted network %q, but %s isn't allowed", entry, addr)
				}
			} else if addr, err := netip.ParseAddr(entry); err == nil {
				if !p.AllowsAddr(addr) {
					t.Errorf("accepted address %q, but it isn't allowed", entry)
				}
			} else if !p.AllowsHost(entry) {
				t.Errorf("accepted name %q, but it isn't allowed", entry)
			}
		}
	})
}

// FuzzDenied checks the deny list against a second, independent reading of
// the same rules, built from netip's own classifiers, on random addresses.
func FuzzDenied(f *testing.F) {
	for _, seed := range []string{"10.0.0.1", "8.8.8.8", "::1", "::ffff:127.0.0.1", "64:ff9b::a00:1", "2002:c0a8:101::1", "2606:4700::1111", "fe80::1"} {
		f.Add(netip.MustParseAddr(seed).AsSlice())
	}
	f.Fuzz(func(t *testing.T, raw []byte) {
		addr, ok := netip.AddrFromSlice(raw)
		if !ok {
			return
		}
		if got, want := Denied(addr), referenceDenied(addr); got != want {
			t.Errorf("Denied(%s) = %v, reference says %v", addr, got, want)
		}
	})
}

// referenceDenied states the deny list without its prefixes: netip's
// classifiers plus the few ranges they don't name.
func referenceDenied(addr netip.Addr) bool {
	addr = addr.Unmap()
	if addr.Is4() {
		b := addr.As4()
		return addr.IsLoopback() || addr.IsPrivate() || addr.IsLinkLocalUnicast() || addr.IsMulticast() ||
			b[0] == 0 || // 0.0.0.0/8
			b[0] >= 240 || // reserved and broadcast
			(b[0] == 100 && b[1]&0xc0 == 64) // 100.64.0.0/10
	}
	if addr.IsLoopback() || addr.IsUnspecified() || addr.IsPrivate() || addr.IsLinkLocalUnicast() || addr.IsMulticast() {
		return true
	}
	b := addr.As16()
	var inner netip.Addr
	switch {
	case [12]byte(b[:12]) == [12]byte{}: // ::a.b.c.d
		inner = netip.AddrFrom4([4]byte(b[12:]))
	case b[0] == 0x00 && b[1] == 0x64 && b[2] == 0xff && b[3] == 0x9b && [8]byte(b[4:12]) == [8]byte{}: // 64:ff9b::/96
		inner = netip.AddrFrom4([4]byte(b[12:]))
	case b[0] == 0x00 && b[1] == 0x64 && b[2] == 0xff && b[3] == 0x9b && b[4] == 0x00 && b[5] == 0x01: // 64:ff9b:1::/48
		inner = netip.AddrFrom4([4]byte(b[12:]))
	case b[0] == 0x20 && b[1] == 0x02: // 2002::/16
		inner = netip.AddrFrom4([4]byte(b[2:6]))
	default:
		return false
	}
	return referenceDenied(inner)
}
