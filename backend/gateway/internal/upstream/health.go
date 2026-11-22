package upstream

import (
	"context"
	"errors"
	"io"
	"math/rand/v2"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/egress"
)

// maxCheckTimeout caps one active check: a target that takes longer to say
// "I'm fine" isn't fine. Shorter intervals get their interval as timeout.
const maxCheckTimeout = 5 * time.Second

// startCheckers starts one goroutine per target that calls GET
// <target><health path> every interval, and sets b.stop to end them all.
// Without health settings (Interval 0) nothing runs.
func (b *Balancer) startCheckers(ctx context.Context, client *http.Client) {
	if b.health.Interval <= 0 {
		return
	}
	ctx, cancel := context.WithCancel(ctx)
	var wg sync.WaitGroup
	for _, m := range b.targets {
		c := &checker{b: b, m: m, client: client, url: healthURL(m, b.health.Path)}
		wg.Go(func() { c.run(ctx) })
	}
	// Waits for the goroutines: when stop returns, none is left running.
	b.stop = func() {
		cancel()
		wg.Wait()
	}
}

// healthURL is the target's base URL with the health path after its own
// path: http://orders:9000/api + /health → http://orders:9000/api/health.
func healthURL(m *member, path string) string {
	u := *m.target.URL
	u.Path = strings.TrimSuffix(u.Path, "/") + path
	u.RawPath, u.RawQuery, u.Fragment = "", "", ""
	return u.String()
}

// checker runs one target's active checks. Its counters are touched only by
// its own goroutine.
type checker struct {
	b      *Balancer
	m      *member
	client *http.Client
	url    string

	passes int // checks passed in a row
	fails  int // checks failed in a row
}

func (c *checker) run(ctx context.Context) {
	interval := c.b.health.Interval
	// A random first wait spreads many targets' checks (and a restarted
	// gateway's) over the interval instead of firing them all at once.
	timer := time.NewTimer(rand.N(interval))
	defer timer.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-timer.C:
		}
		c.check(ctx)
		timer.Reset(interval)
	}
}

// check runs one probe and applies the thresholds: Unhealthy failures in a
// row take the target out, Healthy passes in a row bring it back.
func (c *checker) check(ctx context.Context) {
	ok, reason, counts := c.probe(ctx)
	switch {
	case !counts:
		return
	case ok:
		c.fails = 0
		c.passes++
		if c.passes >= c.b.health.Healthy {
			c.b.setHealthy(c.m, true, "")
		}
	default:
		c.passes = 0
		c.fails++
		if c.fails >= c.b.health.Unhealthy {
			c.b.setHealthy(c.m, false, reason)
		}
	}
}

// probe calls the health URL once. ok is a 2xx or 3xx answer. counts is
// false when the result says nothing about the target: the gateway is
// stopping, or the egress policy refused the address (that is the tenant's
// configuration, answered 502 upstream_not_allowed on traffic; marking the
// target unhealthy would hide it behind 503 no_healthy_upstream).
func (c *checker) probe(ctx context.Context) (ok bool, reason string, counts bool) {
	timeout := min(c.b.health.Interval, maxCheckTimeout)
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.url, nil)
	if err != nil {
		return false, "invalid health URL", true
	}
	req.Header.Set("User-Agent", "apim-gateway-health-check")
	resp, err := c.client.Do(req)
	if err != nil {
		if _, blocked := errors.AsType[*egress.BlockedError](err); blocked || ctx.Err() == context.Canceled {
			return false, "", false
		}
		return false, err.Error(), true
	}
	// Read a little of the body so the connection can be reused.
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
	resp.Body.Close()
	if resp.StatusCode >= 200 && resp.StatusCode < 400 {
		return true, "", true
	}
	return false, resp.Status, true
}

// newCheckClient is the HTTP client for active checks: through the egress
// dialer (the SSRF policy applies to checks too), a short connect timeout,
// no redirects followed (a 3xx already counts as healthy) and no proxy from
// the environment.
func newCheckClient(policy *egress.Policy) *http.Client {
	dialer := egress.NewDialer(policy, maxCheckTimeout, 30*time.Second)
	return &http.Client{
		Transport: &http.Transport{
			DialContext:         dialer.DialContext,
			TLSHandshakeTimeout: maxCheckTimeout,
			MaxIdleConnsPerHost: 1, // one check at a time per target
			IdleConnTimeout:     90 * time.Second,
		},
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
}
