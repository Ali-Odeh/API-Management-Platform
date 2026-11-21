package egress

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/netip"
	"syscall"
	"time"
)

// BlockedError is the answer for a connection the policy refused. The proxy
// turns it into 502 upstream_not_allowed.
type BlockedError struct {
	Host string     // as written in the upstream URL
	Addr netip.Addr // the address it led to
}

func (e *BlockedError) Error() string {
	return fmt.Sprintf("upstream %s (%s) is not allowed: internal address", e.Host, e.Addr)
}

// Dialer opens connections for the proxy and refuses the ones the policy
// forbids.
type Dialer struct {
	policy  *Policy
	open    *net.Dialer // for hosts the allow list names
	guarded *net.Dialer // for everyone else: every address checked before connecting
}

// NewDialer builds a dialer with the given connect timeout and TCP
// keep-alive interval. A nil policy is the default deny list alone.
func NewDialer(policy *Policy, timeout, keepAlive time.Duration) *Dialer {
	if policy == nil {
		policy, _ = NewPolicy(nil)
	}
	d := &Dialer{
		policy: policy,
		open:   &net.Dialer{Timeout: timeout, KeepAlive: keepAlive},
	}
	d.guarded = &net.Dialer{Timeout: timeout, KeepAlive: keepAlive, Control: d.check}
	return d
}

// DialContext is http.Transport's DialContext. addr is "host:port" from the
// upstream URL. A host the allow list names connects as is. Any other is
// resolved by the dialer as usual, and each address is checked by check
// right before its connection is opened: the check sees the address really
// used, never a name that could resolve differently a moment later.
func (d *Dialer) DialContext(ctx context.Context, network, addr string) (net.Conn, error) {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return nil, err
	}
	if d.policy.AllowsHost(host) {
		return d.open.DialContext(ctx, network, addr)
	}
	conn, err := d.guarded.DialContext(ctx, network, addr)
	if blocked, ok := errors.AsType[*BlockedError](err); ok {
		// The hook only knows the address; name the host for the log.
		return nil, &BlockedError{Host: host, Addr: blocked.Addr}
	}
	return conn, err
}

// check is net.Dialer's Control hook. It runs after DNS has answered and
// the socket exists, but before it connects: address is the resolved
// "ip:port" about to be used.
func (d *Dialer) check(_, address string, _ syscall.RawConn) error {
	ap, err := netip.ParseAddrPort(address)
	if err != nil {
		return fmt.Errorf("unexpected address %q: %w", address, err)
	}
	if !d.policy.AllowsAddr(ap.Addr()) {
		return &BlockedError{Addr: ap.Addr()}
	}
	return nil
}
