package egress

import (
	"context"
	"encoding/binary"
	"errors"
	"net"
	"net/netip"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// listen starts a TCP listener on 127.0.0.1 that counts the connections it
// accepts, so a test can prove a refused dial never reached it.
func listen(t *testing.T) (port string, accepted *atomic.Int64) {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	t.Cleanup(func() { ln.Close() })
	accepted = new(atomic.Int64)
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			accepted.Add(1)
			conn.Close()
		}
	}()
	_, port, _ = net.SplitHostPort(ln.Addr().String())
	return port, accepted
}

func newTestDialer(t *testing.T, allow ...string) *Dialer {
	t.Helper()
	p, err := NewPolicy(allow)
	if err != nil {
		t.Fatalf("NewPolicy: %v", err)
	}
	return NewDialer(p, time.Second, 0)
}

func dial(d *Dialer, addr string) error {
	conn, err := d.DialContext(context.Background(), "tcp", addr)
	if err == nil {
		conn.Close()
	}
	return err
}

func TestAnInternalAddressIsRefusedBeforeConnecting(t *testing.T) {
	port, accepted := listen(t)
	d := newTestDialer(t)

	err := dial(d, "127.0.0.1:"+port)

	blocked, ok := errors.AsType[*BlockedError](err)
	if !ok {
		t.Fatalf("dial = %v, want a *BlockedError", err)
	}
	if blocked.Host != "127.0.0.1" || blocked.Addr != netip.MustParseAddr("127.0.0.1") {
		t.Errorf("BlockedError = %+v", blocked)
	}
	if !strings.Contains(err.Error(), "not allowed") {
		t.Errorf("message = %q", err.Error())
	}
	time.Sleep(50 * time.Millisecond) // give a wrongly made connection time to arrive
	if n := accepted.Load(); n != 0 {
		t.Errorf("the listener accepted %d connections; a refused dial must never connect", n)
	}
}

// The URL names no IP, but the name resolves to one at connect time: the
// check sees the resolved address.
func TestANameThatResolvesToAnInternalAddressIsRefused(t *testing.T) {
	port, accepted := listen(t)
	d := newTestDialer(t)

	err := dial(d, "localhost:"+port)

	if blocked, ok := errors.AsType[*BlockedError](err); !ok || blocked.Host != "localhost" || !blocked.Addr.IsLoopback() {
		t.Fatalf("dial localhost = %v, want a *BlockedError for a loopback address", err)
	}
	if n := accepted.Load(); n != 0 {
		t.Errorf("the listener accepted %d connections", n)
	}
}

func TestTheAllowListOpensAddressesAndNames(t *testing.T) {
	port, accepted := listen(t)

	if err := dial(newTestDialer(t, "127.0.0.1/32"), "127.0.0.1:"+port); err != nil {
		t.Errorf("an allowed network: %v", err)
	}
	if err := dial(newTestDialer(t, "localhost"), "localhost:"+port); err != nil {
		t.Errorf("an allowed name: %v", err)
	}
	// Allowing a name doesn't open its address under another name.
	if err := dial(newTestDialer(t, "localhost"), "127.0.0.1:"+port); err == nil {
		t.Error("127.0.0.1 was reached through localhost's exception")
	}
	if n := waitForCount(accepted, 2); n != 2 {
		t.Errorf("accepted %d connections, want 2", n)
	}
}

// waitForCount waits up to a second for the listener's goroutine to count
// want connections (a dial returns before the listener's Accept does), and
// returns the count it saw.
func waitForCount(accepted *atomic.Int64, want int64) int64 {
	for deadline := time.Now().Add(time.Second); time.Now().Before(deadline); time.Sleep(5 * time.Millisecond) {
		if n := accepted.Load(); n >= want {
			return n
		}
	}
	return accepted.Load()
}

func TestANilPolicyIsTheDenyListAlone(t *testing.T) {
	port, _ := listen(t)
	d := NewDialer(nil, time.Second, 0)
	if _, ok := errors.AsType[*BlockedError](dial(d, "127.0.0.1:"+port)); !ok {
		t.Error("a dialer without a policy reached 127.0.0.1")
	}
}

func TestOtherDialErrorsPassThrough(t *testing.T) {
	d := newTestDialer(t, "127.0.0.1")
	if err := dial(d, "no-port"); err == nil {
		t.Error("an address without a port dialed")
	}
	// Nothing listens there: a refused connection, not a policy refusal.
	err := dial(d, "127.0.0.1:1")
	if _, blocked := errors.AsType[*BlockedError](err); err == nil || blocked {
		t.Errorf("dial a closed port = %v, want a plain connection error", err)
	}
	if err := d.check(context.Background(), "tcp", "not-an-address", nil); err == nil {
		t.Error("check accepted an address it can't read")
	}
}

// DNS rebinding: a name first resolves to an allowed address and then, on
// the next lookup, to an internal one. The first dial connects; the second
// is refused, because every connection is checked by the address it uses,
// never by what the name meant earlier.
func TestDNSRebindingIsRefusedAtConnectTime(t *testing.T) {
	port, accepted := listen(t)
	d, answer := dialerWithFakeDNS(t, "127.0.0.1/32")

	answer(netip.MustParseAddr("127.0.0.1")) // the "public" answer: allowed above, as a stand-in
	if err := dial(d, "rebind.test.:"+port); err != nil {
		t.Fatalf("first dial (allowed answer): %v", err)
	}
	internal := netip.MustParseAddr("127.0.0.2")
	answer(internal) // the same name, now internal
	err := dial(d, "rebind.test.:"+port)
	blocked, ok := errors.AsType[*BlockedError](err)
	if !ok || blocked.Host != "rebind.test." || blocked.Addr != internal {
		t.Fatalf("second dial = %v, want refused for %s", err, internal)
	}
	time.Sleep(50 * time.Millisecond) // give a wrongly made second connection time to arrive
	if n := waitForCount(accepted, 1); n != 1 {
		t.Errorf("accepted %d connections, want only the first", n)
	}
}

// A name with an internal and an allowed address: the internal one is never
// connected to, and when the allowed one fails too, the error is the
// connection's, not the policy's (whatever order Go tries them in).
func TestAMixedAnswerIsNotBlamedOnThePolicy(t *testing.T) {
	d, answer := dialerWithFakeDNS(t, "127.0.0.1/32")
	answer(netip.MustParseAddr("127.0.0.2"), netip.MustParseAddr("127.0.0.1"))

	err := dial(d, "mixed.test.:1") // nothing listens on 127.0.0.1:1

	if err == nil {
		t.Fatal("dial to a closed port succeeded")
	}
	if _, blocked := errors.AsType[*BlockedError](err); blocked {
		t.Errorf("dial = %v; an allowed address was tried and failed, so this is no policy refusal", err)
	}
}

// dialerWithFakeDNS is a dialer whose names resolve through fakeDNS; answer
// sets the addresses the next lookups return.
func dialerWithFakeDNS(t *testing.T, allow ...string) (*Dialer, func(...netip.Addr)) {
	t.Helper()
	dns, answer := fakeDNS(t)
	d := newTestDialer(t, allow...)
	d.guarded.Resolver = &net.Resolver{PreferGo: true, Dial: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "udp", dns)
	}}
	return d, answer
}

// fakeDNS answers A queries over UDP with the addresses last given to
// answer, and AAAA queries with none. It speaks just enough DNS for Go's
// resolver. The test changes the answer between dials, so a query Go's
// resolver retries gets the same answer.
func fakeDNS(t *testing.T) (addr string, answer func(...netip.Addr)) {
	t.Helper()
	pc, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen udp: %v", err)
	}
	t.Cleanup(func() { pc.Close() })
	var current atomic.Pointer[[]netip.Addr]
	current.Store(&[]netip.Addr{})
	go func() {
		buf := make([]byte, 512)
		for {
			n, from, err := pc.ReadFrom(buf)
			if err != nil {
				return
			}
			query := buf[:n]
			if len(query) < 17 {
				continue
			}
			// The question: header (12 bytes), the name, type (2), class (2).
			end := 12
			for end < len(query) && query[end] != 0 {
				end += int(query[end]) + 1
			}
			end += 5 // the name's final zero byte, type and class
			if end > len(query) {
				continue
			}
			qtype := binary.BigEndian.Uint16(query[end-4 : end-2])

			reply := append([]byte{}, query[:end]...)
			reply[2], reply[3] = 0x81, 0x80 // a response, recursion available, no error
			reply[6], reply[7] = 0, 0       // the answer count, set below
			reply[8], reply[9], reply[10], reply[11] = 0, 0, 0, 0
			if qtype == 1 { // A
				addrs := *current.Load()
				reply[7] = byte(len(addrs))
				for _, a := range addrs {
					ip := a.As4()
					// The name (a pointer to the question's), type A, class IN, TTL 0, 4 bytes.
					reply = append(reply, 0xc0, 0x0c, 0, 1, 0, 1, 0, 0, 0, 0, 0, 4)
					reply = append(reply, ip[:]...)
				}
			}
			_, _ = pc.WriteTo(reply, from)
		}
	}()
	return pc.LocalAddr().String(), func(addrs ...netip.Addr) { current.Store(&addrs) }
}
