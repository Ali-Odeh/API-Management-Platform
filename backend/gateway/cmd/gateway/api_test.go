package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/middleware"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/proxy"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/ratelimit"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/tenant"
)

type staticSource struct{ snap *config.Snapshot }

func (s staticSource) Current() *config.Snapshot { return s.snap }

const testKey = "ak_live_0123456789abcdef0123456789abcdef"

func sha256Hex(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])
}

// The whole API path, as main builds it: request ID → logger → recover →
// router → auth → forwarder → a real (fake) backend.
func TestAPIHandlerEndToEnd(t *testing.T) {
	// A channel, not variables: the backend runs in another goroutine.
	type atBackend struct{ requestID, apiKey, consumerID string }
	got := make(chan atBackend, 1)
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got <- atBackend{r.Header.Get("X-Request-ID"), r.Header.Get("X-API-Key"), r.Header.Get("X-Consumer-ID")}
		_, _ = io.WriteString(w, "order 7")
	}))
	defer backend.Close()

	upstream, _ := url.Parse(backend.URL)
	snap := snapshotOf(&config.Workspace{
		Version: 1,
		Routes: []config.Route{{
			ID: "r1", PathPrefix: "/v1/orders", Methods: []string{"GET"}, AuthRequired: true,
			Upstream: upstream, Timeout: 5 * time.Second,
		}},
		Keys: map[string]config.Key{sha256Hex(testKey): {ID: "k1", ConsumerID: "c1", ConsumerStatus: config.ConsumerActive}},
	})
	var logs bytes.Buffer
	api := apiHandler(staticSource{snap}, "localhost", allowAll{}, slog.New(slog.NewJSONHandler(&logs, nil)))

	tests := []struct {
		name         string
		path         string
		key          string
		wantStatus   int
		wantRoute    string
		wantConsumer string
	}{
		{name: "forwarded", path: "/v1/orders/7?secret=1", key: testKey, wantStatus: 200, wantRoute: "r1", wantConsumer: "c1"},
		{name: "no key", path: "/v1/orders/7", wantStatus: 401, wantRoute: "r1"},
		{name: "no route (checked before the key)", path: "/v2/x", wantStatus: 404},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			logs.Reset()
			req := httptest.NewRequest("GET", tt.path, nil)
			req.Header.Set("X-Request-ID", "e2e-1")
			if tt.key != "" {
				req.Header.Set("X-API-Key", tt.key)
			}
			rec := httptest.NewRecorder()

			api.ServeHTTP(rec, req)

			if rec.Code != tt.wantStatus || rec.Header().Get("X-Request-ID") != "e2e-1" {
				t.Fatalf("client got %d with X-Request-ID %q", rec.Code, rec.Header().Get("X-Request-ID"))
			}
			var line map[string]any
			if err := json.Unmarshal(logs.Bytes(), &line); err != nil {
				t.Fatalf("want exactly one JSON log line, got %q", logs.String())
			}
			if line["request_id"] != "e2e-1" || line["route_id"] != tt.wantRoute ||
				line["consumer_id"] != tt.wantConsumer || line["status"] != float64(tt.wantStatus) {
				t.Errorf("log line = %v", line)
			}
			if strings.Contains(logs.String(), "secret") || strings.Contains(logs.String(), testKey) {
				t.Errorf("query or API key leaked into the log: %s", logs.String())
			}
		})
	}

	// The backend (called once, by the first case) got the request ID and
	// the consumer, but not the key.
	b := <-got
	if b.requestID != "e2e-1" || b.consumerID != "c1" || b.apiKey != "" {
		t.Errorf("backend got %+v, want request ID e2e-1, consumer c1 and no key", b)
	}
}

func TestAPIHandlerTimesTheBackend(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		time.Sleep(30 * time.Millisecond)
	}))
	defer backend.Close()
	upstream, _ := url.Parse(backend.URL)
	snap := snapshotOf(&config.Workspace{Version: 1, Routes: []config.Route{{
		ID: "r1", PathPrefix: "/", Methods: []string{"GET"}, AuthRequired: false, Upstream: upstream, Timeout: 5 * time.Second,
	}}})
	var logs bytes.Buffer
	api := apiHandler(staticSource{snap}, "localhost", allowAll{}, slog.New(slog.NewJSONHandler(&logs, nil)))

	api.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/x", nil))

	var line map[string]any
	if err := json.Unmarshal(logs.Bytes(), &line); err != nil {
		t.Fatalf("log: %q", logs.String())
	}
	latency, upstreamMS, gateway := line["latency_ms"].(float64), line["upstream_ms"].(float64), line["gateway_ms"].(float64)
	if upstreamMS < 30 || latency < upstreamMS || gateway < 0 || gateway > latency {
		t.Errorf("latency %v, upstream %v, gateway %v: want upstream >= 30 and gateway = latency - upstream", latency, upstreamMS, gateway)
	}
}

// swappingSource is a SnapshotSource whose snapshot a test replaces while
// requests run, like the poller does.
type swappingSource struct {
	current atomic.Pointer[config.Snapshot]
}

func (s *swappingSource) Current() *config.Snapshot { return s.current.Load() }

// Many requests through the whole chain while the config keeps changing.
// Under `go test -race` (CI) this fails on any data race; every request
// must still get a correct answer from one consistent config version.
func TestAPIHandlerUnderLoadWhileConfigChanges(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, r.Header.Get("X-Consumer-ID"))
	}))
	defer backend.Close()
	upstream, _ := url.Parse(backend.URL)

	// Two versions that differ in every field the chain reads.
	version := func(v int64, consumer string, timeout time.Duration) *config.Snapshot {
		return snapshotOf(&config.Workspace{
			Version: v,
			Routes: []config.Route{{
				ID: fmt.Sprintf("r%d", v), PathPrefix: "/v1", Methods: []string{"GET"}, AuthRequired: true,
				Upstream: upstream, Timeout: timeout,
			}},
			Keys: map[string]config.Key{sha256Hex(testKey): {ID: "k1", ConsumerID: consumer, ConsumerStatus: config.ConsumerActive}},
		})
	}
	a, b := version(1, "consumer-a", 5*time.Second), version(2, "consumer-b", 4*time.Second)
	source := &swappingSource{}
	source.current.Store(a)
	api := apiHandler(source, "localhost", allowAll{}, slog.New(slog.DiscardHandler))

	stopSwapping := make(chan struct{})
	swapped := make(chan struct{})
	go func() {
		defer close(swapped)
		for i := 0; ; i++ {
			select {
			case <-stopSwapping:
				return
			default:
			}
			if i%2 == 0 {
				source.current.Store(b)
			} else {
				source.current.Store(a)
			}
			time.Sleep(time.Millisecond)
		}
	}()

	var wg sync.WaitGroup
	errs := make(chan string, 50*20)
	for range 50 {
		wg.Go(func() {
			for range 20 {
				req := httptest.NewRequest("GET", "/v1/x", nil)
				req.Header.Set("X-API-Key", testKey)
				rec := httptest.NewRecorder()
				api.ServeHTTP(rec, req)
				if body := rec.Body.String(); rec.Code != 200 || (body != "consumer-a" && body != "consumer-b") {
					errs <- fmt.Sprintf("%d %q", rec.Code, body)
				}
			}
		})
	}
	wg.Wait()
	close(stopSwapping)
	<-swapped
	close(errs)

	for e := range errs {
		t.Errorf("bad answer during config changes: %s", e)
	}
}

// The gateway's own work per request, without any network: request ID,
// logger (real JSON, written to nowhere), recover, router, auth, then a
// backend that answers at once. Run by hand:
//
//	go test -run=^$ -bench=. -benchmem ./cmd/gateway
func BenchmarkAPIHandlerChain(b *testing.B) {
	snap := snapshotOf(&config.Workspace{
		Version: 1,
		Routes:  []config.Route{{ID: "r1", PathPrefix: "/v1/orders", Methods: []string{"GET"}, AuthRequired: true}},
		Keys:    map[string]config.Key{sha256Hex(testKey): {ID: "k1", ConsumerID: "c1", ConsumerStatus: config.ConsumerActive}},
	})
	log := slog.New(slog.NewJSONHandler(io.Discard, nil))
	// The same chain as apiHandler, with an instant backend instead of the
	// forwarder, so only the gateway's own cost is measured.
	instant := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	chain := middleware.Chain(
		tenant.Middleware(staticSource{snap}, "localhost")(proxy.NewRouter(middleware.Auth(log)(instant))),
		middleware.RequestID, middleware.Logger(log), middleware.Recover(log),
	)

	for b.Loop() {
		req := httptest.NewRequest("GET", "/v1/orders/7", nil)
		req.Header.Set("X-API-Key", testKey)
		chain.ServeHTTP(httptest.NewRecorder(), req)
	}
}

// allowAll is a rate limiter that always says yes, for tests about other
// layers.
type allowAll struct{}

func (allowAll) Allow(_ context.Context, req ratelimit.Request) (ratelimit.Result, error) {
	return ratelimit.Result{Allowed: true, Limit: req.WorkspaceLimit, Remaining: req.WorkspaceLimit - 1}, nil
}

// snapshotOf is a config with ws as its `default` workspace (until MT.7
// every request is served from it).
func snapshotOf(ws *config.Workspace) *config.Snapshot {
	ws.ID, ws.Slug, ws.Status = "ws-default", config.DefaultHost, config.WorkspaceActive
	ws.Hosts = []string{config.DefaultHost}
	return config.NewSnapshot(ws.Version, ws)
}

// Two workspaces publish the same path with different backends and keys:
// each Host reaches only its own, a key works only on its own workspace's
// host, and the backend learns the workspace from the gateway alone.
func TestAPIHandlerKeepsWorkspacesApart(t *testing.T) {
	type atBackend struct{ name, workspaceID string }
	got := make(chan atBackend, 1)
	backend := func(name string) *url.URL {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			got <- atBackend{name, r.Header.Get("X-Workspace-ID")}
		}))
		t.Cleanup(srv.Close)
		u, _ := url.Parse(srv.URL)
		return u
	}
	const acmeKey, globexKey = "ak_live_acme0000000000000000000000000000", "ak_live_globex00000000000000000000000000"
	tenantWith := func(id, slug, key string, upstream *url.URL) *config.Workspace {
		return &config.Workspace{
			ID: id, Slug: slug, Status: config.WorkspaceActive, Hosts: []string{slug},
			Routes: []config.Route{{ID: "r-" + slug, PathPrefix: "/v1/orders", Methods: []string{"GET"},
				AuthRequired: true, Upstream: upstream, Timeout: 5 * time.Second}},
			Keys: map[string]config.Key{sha256Hex(key): {ID: "k-" + slug, ConsumerID: "c-" + slug, ConsumerStatus: config.ConsumerActive}},
		}
	}
	snap := config.NewSnapshot(1,
		tenantWith("w-acme", "acme", acmeKey, backend("acme")),
		tenantWith("w-globex", "globex", globexKey, backend("globex")),
	)
	var logs bytes.Buffer
	api := apiHandler(staticSource{snap}, "localhost", allowAll{}, slog.New(slog.NewJSONHandler(&logs, nil)))

	call := func(host, key string) (int, string, map[string]any) {
		logs.Reset()
		req := httptest.NewRequest("GET", "/v1/orders/7", nil)
		req.Host = host
		req.Header.Set("X-API-Key", key)
		req.Header.Set("X-Workspace-ID", "w-forged")
		rec := httptest.NewRecorder()
		api.ServeHTTP(rec, req)
		var line map[string]any
		if err := json.Unmarshal(logs.Bytes(), &line); err != nil {
			t.Fatalf("want one JSON log line, got %q", logs.String())
		}
		var body map[string]string
		_ = json.Unmarshal(rec.Body.Bytes(), &body)
		return rec.Code, body["error"], line
	}

	for _, tc := range []struct{ host, key, backend, workspace string }{
		{"acme.localhost:8081", acmeKey, "acme", "w-acme"},
		{"globex.localhost:8081", globexKey, "globex", "w-globex"},
	} {
		status, _, line := call(tc.host, tc.key)
		if status != 200 {
			t.Fatalf("%s: status %d, want 200", tc.host, status)
		}
		if b := <-got; b.name != tc.backend || b.workspaceID != tc.workspace {
			t.Errorf("%s reached %+v, want backend %s told workspace %s", tc.host, b, tc.backend, tc.workspace)
		}
		if line["workspace_id"] != tc.workspace {
			t.Errorf("%s: log workspace_id = %v, want %s", tc.host, line["workspace_id"], tc.workspace)
		}
	}

	// A valid key of another workspace is unknown here, exactly like a wrong key.
	if status, code, _ := call("globex.localhost", acmeKey); status != 401 || code != "invalid_api_key" {
		t.Errorf("acme's key on globex's host: %d %s, want 401 invalid_api_key", status, code)
	}
	// Neither tenant's routes are served on the bare host: it is `default`'s, which isn't here.
	if status, code, line := call("localhost", acmeKey); status != 404 || code != "workspace_not_found" || line["workspace_id"] != "" {
		t.Errorf("bare host: %d %s (log workspace %v), want 404 workspace_not_found", status, code, line["workspace_id"])
	}
}
