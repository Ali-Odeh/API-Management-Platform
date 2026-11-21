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
	if err := d.check("tcp", "not-an-address", nil); err == nil {
		t.Error("check accepted an address it can't read")
	}
}

// DNS rebinding: a name first resolves to an allowed address and then, on
// the next lookup, to an internal one. The first dial connects; the second
// is refused, because every connection is checked by the address it uses,
// never by what the name meant earlier.
func TestDNSRebindingIsRefusedAtConnectTime(t *testing.T) {
	port, accepted := listen(t)
	answers := []netip.Addr{
		netip.MustParseAddr("127.0.0.1"), // the "public" answer: allowed below, as a stand-in
		netip.MustParseAddr("127.0.0.2"), // then the internal one
	}
	dns := fakeDNS(t, answers)
	d := newTestDialer(t, "127.0.0.1/32")
	resolver := &net.Resolver{PreferGo: true, Dial: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "udp", dns)
	}}
	d.guarded.Resolver = resolver

	if err := dial(d, "rebind.test.:"+port); err != nil {
		t.Fatalf("first dial (allowed answer): %v", err)
	}
	err := dial(d, "rebind.test.:"+port)
	blocked, ok := errors.AsType[*BlockedError](err)
	if !ok || blocked.Addr != answers[1] {
		t.Fatalf("second dial = %v, want refused for %s", err, answers[1])
	}
	time.Sleep(50 * time.Millisecond) // give a wrongly made second connection time to arrive
	if n := waitForCount(accepted, 1); n != 1 {
		t.Errorf("accepted %d connections, want only the first", n)
	}
}

// fakeDNS answers A queries over UDP with the next address of answers (the
// last one repeats), and AAAA queries with no address. It speaks just
// enough DNS for Go's resolver.
func fakeDNS(t *testing.T, answers []netip.Addr) string {
	t.Helper()
	pc, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen udp: %v", err)
	}
	t.Cleanup(func() { pc.Close() })
	var asked atomic.Int64
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
			reply[6], reply[7] = 0, 0       // no answers ...
			reply[8], reply[9], reply[10], reply[11] = 0, 0, 0, 0
			if qtype == 1 { // A
				i := min(int(asked.Add(1))-1, len(answers)-1)
				ip := answers[i].As4()
				reply[7] = 1 // ... except one
				reply = append(reply, 0xc0, 0x0c, 0, 1, 0, 1, 0, 0, 0, 0, 0, 4)
				reply = append(reply, ip[:]...)
			}
			_, _ = pc.WriteTo(reply, from)
		}
	}()
	return pc.LocalAddr().String()
}
