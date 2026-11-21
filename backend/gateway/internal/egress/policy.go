// Package egress decides which addresses the gateway may connect to on a
// tenant's behalf (decision 3, SSRF protection).
//
// A tenant chooses its services' URLs. Without a check, a URL such as
// http://redis:6379 or http://169.254.169.254/ would make the gateway reach
// the platform's own internal services. The check runs where it can't be
// fooled: on the IP address actually being connected to, after DNS has
// answered, so a name that resolves to an internal address (also one that
// changes its answer between two lookups, "DNS rebinding") is refused too.
package egress

import (
	"fmt"
	"net/netip"
	"strings"
)

// deniedPrefixes are the internal, local and special addresses no tenant
// may reach (PLAN.md section 7), unless UPSTREAM_ALLOW names them.
var deniedPrefixes = mustPrefixes(
	// IPv4
	"0.0.0.0/8",      // "this network"
	"10.0.0.0/8",     // private
	"100.64.0.0/10",  // carrier-grade NAT
	"127.0.0.0/8",    // loopback
	"169.254.0.0/16", // link-local, cloud metadata (169.254.169.254)
	"172.16.0.0/12",  // private (Docker's networks)
	"192.168.0.0/16", // private
	"224.0.0.0/4",    // multicast
	"240.0.0.0/4",    // reserved, and the broadcast address 255.255.255.255
	// IPv6
	"::/128",    // unspecified
	"::1/128",   // loopback
	"fc00::/7",  // unique local (private)
	"fe80::/10", // link-local
	"ff00::/8",  // multicast
)

// IPv6 addresses that carry an IPv4 address inside them. Each is judged by
// the IPv4 address it leads to, so ::ffff:10.0.0.1 or 64:ff9b::a00:1 can't
// slip a private address past the list.
var (
	ipv4Compatible = netip.MustParsePrefix("::/96")          // ::a.b.c.d (deprecated form)
	nat64          = netip.MustParsePrefix("64:ff9b::/96")   // NAT64, well-known prefix
	nat64Local     = netip.MustParsePrefix("64:ff9b:1::/48") // NAT64, local use (last 32 bits)
	sixToFour      = netip.MustParsePrefix("2002::/16")      // 6to4: the IPv4 address in bits 16-48
)

// Policy is the default deny list plus the Platform Admin's exceptions.
// It is never changed after it is built, so it is safe to share.
type Policy struct {
	hosts    map[string]bool // allowed host names, with every address they resolve to
	prefixes []netip.Prefix  // allowed addresses
}

// NewPolicy builds a policy from UPSTREAM_ALLOW's entries: host names
// ("mock-backend"), addresses ("10.20.0.5") and networks ("10.20.0.0/16").
// Empty entries are ignored; any other unreadable entry is an error.
func NewPolicy(allow []string) (*Policy, error) {
	p := &Policy{hosts: make(map[string]bool)}
	for _, entry := range allow {
		entry = strings.TrimSpace(entry)
		if entry == "" {
			continue
		}
		if prefix, err := netip.ParsePrefix(entry); err == nil {
			p.prefixes = append(p.prefixes, prefix.Masked())
			continue
		}
		if addr, err := netip.ParseAddr(entry); err == nil {
			p.prefixes = append(p.prefixes, netip.PrefixFrom(addr.Unmap(), addr.Unmap().BitLen()))
			continue
		}
		name := normalizeHost(entry)
		if !isHostName(name) {
			return nil, fmt.Errorf("%q is not a host name, an IP address or a network such as 10.20.0.0/16", entry)
		}
		p.hosts[name] = true
	}
	return p, nil
}

// AllowsHost reports whether host (as written in the upstream URL) is on the
// allow list by name: then every address it resolves to may be reached.
func (p *Policy) AllowsHost(host string) bool {
	return p.hosts[normalizeHost(host)]
}

// AllowsAddr reports whether the gateway may connect to addr: either the
// allow list names it, or it is not on the deny list.
func (p *Policy) AllowsAddr(addr netip.Addr) bool {
	addr = addr.WithZone("").Unmap()
	for _, prefix := range p.prefixes {
		if prefix.Contains(addr) {
			return true
		}
	}
	return !Denied(addr)
}

// Denied reports whether addr is internal, local or special by default.
func Denied(addr netip.Addr) bool {
	addr = addr.WithZone("").Unmap() // ::ffff:10.0.0.1 is 10.0.0.1
	if !addr.IsValid() {
		return true
	}
	if inner, ok := embeddedIPv4(addr); ok && Denied(inner) {
		return true
	}
	for _, prefix := range deniedPrefixes {
		if prefix.Contains(addr) {
			return true
		}
	}
	return false
}

// embeddedIPv4 returns the IPv4 address an IPv6 transition address leads
// to. ::/96 also holds :: and ::1, which the list covers by themselves.
func embeddedIPv4(addr netip.Addr) (netip.Addr, bool) {
	if !addr.Is6() {
		return netip.Addr{}, false
	}
	b := addr.As16()
	switch {
	case ipv4Compatible.Contains(addr), nat64.Contains(addr), nat64Local.Contains(addr):
		return netip.AddrFrom4([4]byte(b[12:16])), true
	case sixToFour.Contains(addr):
		return netip.AddrFrom4([4]byte(b[2:6])), true
	}
	return netip.Addr{}, false
}

// normalizeHost is how host names are compared: lowercase, without the
// brackets of an IPv6 literal and without a trailing dot.
func normalizeHost(host string) string {
	host = strings.TrimSuffix(strings.TrimPrefix(host, "["), "]")
	return strings.TrimSuffix(strings.ToLower(host), ".")
}

// isHostName reports whether s is a DNS name: dot-separated labels of a-z,
// 0-9 and '-', each 1 to 63 long, not starting or ending with '-'.
func isHostName(s string) bool {
	if s == "" || len(s) > 253 {
		return false
	}
	for _, label := range strings.Split(s, ".") {
		if label == "" || len(label) > 63 || label[0] == '-' || label[len(label)-1] == '-' {
			return false
		}
		for _, c := range []byte(label) {
			if (c < 'a' || c > 'z') && (c < '0' || c > '9') && c != '-' {
				return false
			}
		}
	}
	return true
}

func mustPrefixes(cidrs ...string) []netip.Prefix {
	prefixes := make([]netip.Prefix, len(cidrs))
	for i, c := range cidrs {
		prefixes[i] = netip.MustParsePrefix(c)
	}
	return prefixes
}
