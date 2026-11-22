package upstream

import (
	"context"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/egress"
)

// loopback allows the addresses httptest servers listen on; the egress
// policy refuses them otherwise.
func loopback(t *testing.T) *egress.Policy {
	t.Helper()
	p, err := egress.NewPolicy([]string{"127.0.0.0/8", "::1"})
	if err != nil {
		t.Fatal(err)
	}
	return p
}

// backend is a test server whose /health answers status, counting calls by path.
type backend struct {
	srv    *httptest.Server
	status atomic.Int64
	mu     sync.Mutex
	hits   map[string]int
}

func newBackend(t *testing.T) *backend {
	t.Helper()
	b := &backend{hits: map[string]int{}}
	b.status.Store(http.StatusOK)
	b.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b.mu.Lock()
		b.hits[r.URL.Path]++
		b.mu.Unlock()
		w.WriteHeader(int(b.status.Load()))
	}))
	t.Cleanup(b.srv.Close)
	return b
}

func (b *backend) count(path string) int {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.hits[path]
}

// serviceAt is a one-target service at the backend's URL.
func serviceAt(t *testing.T, id, rawURL string, health config.Health) *config.Service {
	t.Helper()
	u, err := url.Parse(rawURL)
	if err != nil {
		t.Fatal(err)
	}
	svc := &config.Service{ID: id, WorkspaceID: "ws1", Timeout: time.Second, Health: health,
		Targets: []config.Target{{ID: id + "-t", URL: u, Weight: 100}}}
	svc.Fingerprint = rawURL + health.Path + health.Interval.String()
	return svc
}

// checkerFor runs checks by hand (no goroutine), so thresholds are exact.
func checkerFor(t *testing.T, policy *egress.Policy, rawURL string, health config.Health) (*checker, *Balancer) {
	t.Helper()
	b := newBalancer(serviceAt(t, "s", rawURL, health), slog.New(slog.DiscardHandler), time.Now)
	m := b.targets[0]
	return &checker{b: b, m: m, client: newCheckClient(policy), url: healthURL(m, health.Path)}, b
}

var health3x2 = config.Health{Path: "/health", Interval: time.Second, Unhealthy: 3, Healthy: 2}

func TestActiveChecksApplyTheThresholds(t *testing.T) {
	be := newBackend(t)
	c, b := checkerFor(t, loopback(t), be.srv.URL, health3x2)
	healthy := func() bool { return b.targets[0].healthy.Load() }

	be.status.Store(http.StatusServiceUnavailable)
	c.check(t.Context())
	c.check(t.Context())
	if !healthy() {
		t.Fatal("unhealthy after 2 failed checks; the threshold is 3")
	}
	c.check(t.Context())
	if healthy() {
		t.Fatal("still healthy after 3 failed checks in a row")
	}

	be.status.Store(http.StatusOK)
	c.check(t.Context())
	if healthy() {
		t.Fatal("healthy after 1 passed check; the threshold is 2")
	}
	be.status.Store(http.StatusInternalServerError) // a failure resets the passes
	c.check(t.Context())
	be.status.Store(http.StatusOK)
	c.check(t.Context())
	if healthy() {
		t.Fatal("healthy although the passes were not in a row")
	}
	c.check(t.Context())
	if !healthy() {
		t.Fatal("not healthy after 2 passed checks in a row")
	}
	if n := be.count("/health"); n != 7 {
		t.Errorf("%d calls to /health, want 7", n)
	}
}

func TestA3xxIsHealthyAndNotFollowed(t *testing.T) {
	be := newBackend(t)
	be.status.Store(http.StatusFound) // no Location: following would fail
	c, _ := checkerFor(t, loopback(t), be.srv.URL, health3x2)
	if ok, reason, counts := c.probe(t.Context()); !ok || !counts {
		t.Errorf("302: ok %v (%s), counts %v; want healthy", ok, reason, counts)
	}
}

func TestAClosedTargetFailsItsCheck(t *testing.T) {
	be := newBackend(t)
	be.srv.Close()
	c, _ := checkerFor(t, loopback(t), be.srv.URL, health3x2)
	if ok, reason, counts := c.probe(t.Context()); ok || !counts || reason == "" {
		t.Errorf("closed target: ok %v, reason %q, counts %v; want a counted failure", ok, reason, counts)
	}
}

// A target the egress policy refuses is the tenant's configuration, not a
// sick backend: its checks don't count, so traffic keeps getting the clear
// 502 upstream_not_allowed instead of 503 no_healthy_upstream.
func TestAPolicyRefusalDoesNotCount(t *testing.T) {
	be := newBackend(t)
	c, b := checkerFor(t, nil, be.srv.URL, health3x2) // nil policy: loopback refused
	for range 5 {
		c.check(t.Context())
	}
	if !b.targets[0].healthy.Load() {
		t.Error("a refused target was marked unhealthy")
	}
	if n := be.count("/health"); n != 0 {
		t.Errorf("the refused target was reached %d times", n)
	}
}

func TestACheckStoppedByShutdownDoesNotCount(t *testing.T) {
	be := newBackend(t)
	c, _ := checkerFor(t, loopback(t), be.srv.URL, health3x2)
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	if _, _, counts := c.probe(ctx); counts {
		t.Error("a check cancelled by shutdown counted")
	}
}

func TestHealthURL(t *testing.T) {
	for base, want := range map[string]string{
		"http://orders:9000":           "http://orders:9000/health",
		"http://orders:9000/":          "http://orders:9000/health",
		"http://orders:9000/api":       "http://orders:9000/api/health",
		"https://orders/api/":          "https://orders/api/health",
		"http://orders:9000/x?debug=1": "http://orders:9000/x/health",
	} {
		u, _ := url.Parse(base)
		if got := healthURL(&member{target: config.Target{URL: u}}, "/health"); got != want {
			t.Errorf("healthURL(%s) = %s, want %s", base, got, want)
		}
	}
}

// The goroutines: a target that starts failing is taken out within the
// thresholds, comes back when it recovers, and stop ends the checks.
func TestCheckersRunUntilStopped(t *testing.T) {
	be := newBackend(t)
	health := config.Health{Path: "/health", Interval: 10 * time.Millisecond, Unhealthy: 2, Healthy: 2}
	b := newBalancer(serviceAt(t, "s", be.srv.URL, health), slog.New(slog.DiscardHandler), time.Now)
	b.startCheckers(t.Context(), newCheckClient(loopback(t)))

	be.status.Store(http.StatusServiceUnavailable)
	waitFor(t, "the target to be taken out", func() bool { _, ok := b.Pick(); return !ok })
	be.status.Store(http.StatusOK)
	waitFor(t, "the target to come back", func() bool { _, ok := b.Pick(); return ok })

	b.stop() // returns once every checker has ended
	before := be.count("/health")
	time.Sleep(50 * time.Millisecond)
	if after := be.count("/health"); after != before {
		t.Errorf("%d checks after stop", after-before)
	}
}

func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	for deadline := time.Now().Add(3 * time.Second); time.Now().Before(deadline); time.Sleep(5 * time.Millisecond) {
		if cond() {
			return
		}
	}
	t.Fatalf("timed out waiting for %s", what)
}
