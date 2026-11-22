package upstream

import (
	"context"
	"log/slog"
	"net/http"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
)

func snapshot(status config.WorkspaceStatus, services ...*config.Service) *config.Snapshot {
	return config.NewSnapshot(1, &config.Workspace{ID: "ws1", Slug: "acme", Status: status, Hosts: []string{"acme"}, Services: services})
}

func newTestRegistry(t *testing.T) *Registry {
	t.Helper()
	r := NewRegistry(t.Context(), loopback(t), slog.New(slog.DiscardHandler))
	t.Cleanup(r.Close)
	return r
}

// Health state and the round-robin position survive a new config version
// that leaves the service's targets alone (a new *Service, same fingerprint).
func TestABalancerSurvivesAnUnchangedService(t *testing.T) {
	r := newTestRegistry(t)
	v1 := service("s", config.Health{}, 1, 1)
	r.Sync(snapshot(config.WorkspaceActive, v1))
	b := r.Balancer(v1)
	b.setHealthy(b.targets[0], false, "test")

	v2 := service("s", config.Health{}, 1, 1) // the next config version
	r.Sync(snapshot(config.WorkspaceActive, v2))

	if r.Balancer(v2) != b {
		t.Fatal("an unchanged service got a new balancer")
	}
	if b.targets[0].healthy.Load() {
		t.Error("the target's health was forgotten")
	}
}

// A changed target list starts fresh, and the old balancer's checkers stop.
func TestAChangedServiceStartsFresh(t *testing.T) {
	be := newBackend(t)
	r := newTestRegistry(t)
	fast := config.Health{Path: "/old", Interval: 5 * time.Millisecond, Unhealthy: 1, Healthy: 1}
	v1 := serviceAt(t, "s", be.srv.URL, fast)
	r.Sync(snapshot(config.WorkspaceActive, v1))
	old := r.Balancer(v1)
	waitFor(t, "the old checks to run", func() bool { return be.count("/old") > 0 })

	fast.Path = "/new"
	v2 := serviceAt(t, "s", be.srv.URL, fast)
	r.Sync(snapshot(config.WorkspaceActive, v2))

	if b := r.Balancer(v2); b == old {
		t.Fatal("a changed service kept its balancer")
	}
	waitFor(t, "the new checks to run", func() bool { return be.count("/new") > 0 })
	before := be.count("/old")
	time.Sleep(30 * time.Millisecond)
	if after := be.count("/old"); after != before {
		t.Errorf("the replaced balancer still checks: %d more", after-before)
	}
}

// Services that leave the config, or whose workspace is suspended, have no
// balancer and no checks.
func TestSyncDropsWhatLeftTheConfig(t *testing.T) {
	be := newBackend(t)
	r := newTestRegistry(t)
	sent := countRequests(r)
	health := config.Health{Path: "/health", Interval: 5 * time.Millisecond, Unhealthy: 1, Healthy: 1}
	svc := serviceAt(t, "s", be.srv.URL, health)
	r.Sync(snapshot(config.WorkspaceActive, svc))
	waitFor(t, "checks to run", func() bool { return be.count("/health") > 0 })

	for name, snap := range map[string]*config.Snapshot{
		"suspended": snapshot(config.WorkspaceSuspended, svc),
		"removed":   snapshot(config.WorkspaceActive),
		"no config": nil,
	} {
		r.Sync(snapshot(config.WorkspaceActive, svc))
		r.Sync(snap)
		if _, ok := r.load("s"); ok {
			t.Errorf("%s: the service still has a balancer", name)
		}
		// Counted where checks leave the gateway, not where they arrive: a
		// check written just before stop can still reach the server after
		// Sync returns, but stop waits for the checkers, so none starts.
		before := sent.Load()
		time.Sleep(30 * time.Millisecond) // six intervals
		if after := sent.Load(); after != before {
			t.Errorf("%s: %d checks after it left", name, after-before)
		}
	}
}

// countRequests counts the requests r's active checks start. Call it before
// the first Sync: the checkers read the client's transport.
func countRequests(r *Registry) *atomic.Int64 {
	var n atomic.Int64
	next := r.client.Transport
	r.client.Transport = roundTripFunc(func(req *http.Request) (*http.Response, error) {
		n.Add(1)
		return next.RoundTrip(req)
	})
	return &n
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) { return f(req) }

// A request still on an older config gets a balancer of its own, which the
// registry doesn't keep: nothing outlives its service.
func TestARequestOnAnotherConfigGetsATemporaryBalancer(t *testing.T) {
	r := newTestRegistry(t)
	current := service("s", config.Health{}, 1)
	r.Sync(snapshot(config.WorkspaceActive, current))

	gone := service("gone", config.Health{}, 1)
	if b := r.Balancer(gone); b == nil {
		t.Fatal("no balancer for a request on an older config")
	}
	if _, ok := r.load("gone"); ok {
		t.Error("the registry kept a balancer for a service not in its config")
	}
	older := service("s", config.Health{}, 1, 1) // another fingerprint of s
	if r.Balancer(older) == r.Balancer(current) {
		t.Error("an older version of s shared the current balancer")
	}
}

// Before any Sync (a test, or the first requests at start), the registry
// keeps what requests ask for.
func TestBeforeSyncRequestsMakeBalancers(t *testing.T) {
	r := newTestRegistry(t)
	svc := service("s", config.Health{}, 1)
	if r.Balancer(svc) != r.Balancer(svc) {
		t.Error("two requests got two balancers")
	}
}

// fakeSource is a config store the test changes by hand.
type fakeSource struct {
	mu      sync.Mutex
	snap    *config.Snapshot
	changed chan struct{}
}

func (f *fakeSource) Current() *config.Snapshot {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.snap
}

func (f *fakeSource) Changed() <-chan struct{} {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.changed
}

func (f *fakeSource) set(snap *config.Snapshot) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.snap = snap
	close(f.changed)
	f.changed = make(chan struct{})
}

func TestRunFollowsTheConfigAndStopsEverything(t *testing.T) {
	be := newBackend(t)
	r := NewRegistry(t.Context(), loopback(t), slog.New(slog.DiscardHandler))
	src := &fakeSource{changed: make(chan struct{})}
	ctx, cancel := context.WithCancel(t.Context())
	done := make(chan struct{})
	go func() { r.Run(ctx, src); close(done) }()

	health := config.Health{Path: "/health", Interval: 5 * time.Millisecond, Unhealthy: 1, Healthy: 1}
	src.set(snapshot(config.WorkspaceActive, serviceAt(t, "s", be.srv.URL, health)))
	waitFor(t, "the new service's checks", func() bool { return be.count("/health") > 0 })

	cancel()
	<-done
	before := be.count("/health")
	time.Sleep(30 * time.Millisecond)
	if after := be.count("/health"); after != before {
		t.Errorf("%d checks after Run ended", after-before)
	}
	if _, ok := r.load("s"); ok {
		t.Error("balancers kept after Run ended")
	}
}

// Under -race: requests pick while the config changes.
func TestConcurrentBalancerAndSync(t *testing.T) {
	r := newTestRegistry(t)
	v1, v2 := service("s", config.Health{}, 1, 1), service("s", config.Health{}, 2, 1)
	r.Sync(snapshot(config.WorkspaceActive, v1))
	var wg sync.WaitGroup
	for i := range 8 {
		wg.Go(func() {
			svc := v1
			if i%2 == 0 {
				svc = v2
			}
			for range 500 {
				if _, ok := r.Balancer(svc).Pick(); !ok {
					t.Error("no pick")
					return
				}
			}
		})
	}
	wg.Go(func() {
		for i := range 200 {
			if i%2 == 0 {
				r.Sync(snapshot(config.WorkspaceActive, v1))
			} else {
				r.Sync(snapshot(config.WorkspaceActive, v2))
			}
		}
	})
	wg.Wait()
}
