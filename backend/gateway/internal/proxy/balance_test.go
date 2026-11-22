package proxy

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
)

// counting is a backend that answers status and counts its requests.
type counting struct {
	url    *url.URL
	status atomic.Int64
	calls  atomic.Int64
}

func newCounting(t *testing.T) *counting {
	t.Helper()
	c := &counting{}
	c.status.Store(http.StatusOK)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		c.calls.Add(1)
		w.WriteHeader(int(c.status.Load()))
	}))
	t.Cleanup(srv.Close)
	c.url, _ = url.Parse(srv.URL)
	return c
}

// balanced is the router and forwarder in front of a service whose targets
// are the given backends, each with its weight.
func balanced(t *testing.T, health config.Health, backends []*counting, weights []int) (http.Handler, *strings.Builder) {
	t.Helper()
	svc := &config.Service{ID: "s1", WorkspaceID: "ws-default", Timeout: 5 * time.Second, Health: health, Fingerprint: "fp"}
	for i, b := range backends {
		svc.Targets = append(svc.Targets, config.Target{ID: string(rune('a' + i)), URL: b.url, Weight: weights[i]})
	}
	snap := snapshotOf(&config.Workspace{Version: 1, Services: []*config.Service{svc}, Routes: []config.Route{{
		ID: "r1", ServiceID: "s1", PathPrefix: "/v1", Methods: []string{"GET"}, Service: svc,
	}}})
	logs := &strings.Builder{}
	f := NewForwarder(slog.New(slog.NewJSONHandler(logs, nil)), testBackends, testRegistry(t))
	return routed(snap, f), logs
}

func get(h http.Handler) (*httptest.ResponseRecorder, *reqctx.Info) {
	req := httptest.NewRequest("GET", "/v1/x", nil)
	info := &reqctx.Info{}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req.WithContext(reqctx.With(req.Context(), info)))
	return rec, info
}

func TestRequestsAreSpreadByWeight(t *testing.T) {
	a, b := newCounting(t), newCounting(t)
	gw, _ := balanced(t, config.Health{}, []*counting{a, b}, []int{3, 1})

	targets := map[string]int{}
	for range 40 {
		rec, info := get(gw)
		if rec.Code != 200 {
			t.Fatalf("status %d", rec.Code)
		}
		targets[info.TargetID]++ // the request log's target_id
	}
	if a.calls.Load() != 30 || b.calls.Load() != 10 || targets["a"] != 30 || targets["b"] != 10 {
		t.Errorf("a got %d, b %d (log: %v); want 30 and 10", a.calls.Load(), b.calls.Load(), targets)
	}
}

// Passive checks from real traffic: failures in a row set a target aside,
// the others take its share, and with none left the answer is 503.
func TestFailingTargetsAreSetAsideThen503(t *testing.T) {
	a, b := newCounting(t), newCounting(t)
	gw, _ := balanced(t, config.Health{PassiveFailures: 2, PassiveEject: time.Minute}, []*counting{a, b}, []int{1, 1})

	a.status.Store(http.StatusInternalServerError)
	for range 4 { // a fails twice (a b a b), then is set aside
		get(gw)
	}
	before := a.calls.Load()
	for range 4 {
		if rec, info := get(gw); rec.Code != 200 || info.TargetID != "b" {
			t.Fatalf("got %d from %s, want 200 from b", rec.Code, info.TargetID)
		}
	}
	if a.calls.Load() != before {
		t.Error("a set-aside target still got requests")
	}

	b.status.Store(http.StatusBadGateway)
	get(gw)
	get(gw) // b's second failure in a row
	rec, info := get(gw)
	var body map[string]string
	_ = json.Unmarshal(rec.Body.Bytes(), &body)
	if rec.Code != http.StatusServiceUnavailable || body["error"] != "no_healthy_upstream" || info.TargetID != "" {
		t.Errorf("every target aside: %d %q (target %q), want 503 no_healthy_upstream", rec.Code, rec.Body.String(), info.TargetID)
	}
}

func TestAConnectionErrorCountsAsAFailure(t *testing.T) {
	dead := newCounting(t)
	dead.url = &url.URL{Scheme: "http", Host: "127.0.0.1:1"} // nothing listens there
	gw, _ := balanced(t, config.Health{PassiveFailures: 1, PassiveEject: time.Minute}, []*counting{dead}, []int{1})

	if rec, _ := get(gw); rec.Code != http.StatusBadGateway {
		t.Fatalf("first request: %d, want 502", rec.Code)
	}
	if rec, _ := get(gw); rec.Code != http.StatusServiceUnavailable {
		t.Errorf("after the failure: %d, want 503 (the only target set aside)", rec.Code)
	}
}

// A successful answer starts the count again: failures must be in a row.
func TestASuccessResetsTheFailures(t *testing.T) {
	a := newCounting(t)
	gw, _ := balanced(t, config.Health{PassiveFailures: 2, PassiveEject: time.Minute}, []*counting{a}, []int{1})
	for range 5 {
		a.status.Store(http.StatusInternalServerError)
		get(gw)
		a.status.Store(http.StatusOK)
		if rec, _ := get(gw); rec.Code != 200 {
			t.Fatalf("got %d; one failure at a time must never set the target aside", rec.Code)
		}
	}
}

// A client that leaves says nothing about the target.
func TestAClientHangingUpIsNotATargetFailure(t *testing.T) {
	slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		<-r.Context().Done()
	}))
	defer slow.Close()
	u, _ := url.Parse(slow.URL)
	gw, _ := balanced(t, config.Health{PassiveFailures: 1, PassiveEject: time.Minute}, []*counting{{url: u}}, []int{1})

	ctx, cancel := context.WithCancel(context.Background())
	req := httptest.NewRequest("GET", "/v1/x", nil).WithContext(ctx)
	go func() { time.Sleep(20 * time.Millisecond); cancel() }()
	rec := httptest.NewRecorder()
	gw.ServeHTTP(rec, req)
	if rec.Code != StatusClientClosedRequest {
		t.Fatalf("got %d, want 499", rec.Code)
	}

	// Still usable: the next request reaches the backend (and hangs until we leave).
	ctx2, cancel2 := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel2()
	rec = httptest.NewRecorder()
	gw.ServeHTTP(rec, httptest.NewRequest("GET", "/v1/x", nil).WithContext(ctx2))
	if rec.Code == http.StatusServiceUnavailable {
		t.Error("a client hanging up set the target aside")
	}
}
