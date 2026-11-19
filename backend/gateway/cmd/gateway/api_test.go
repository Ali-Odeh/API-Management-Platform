package main

import (
	"bytes"
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
	snap := &config.Snapshot{
		Version: 1,
		Routes: []config.Route{{
			ID: "r1", PathPrefix: "/v1/orders", Methods: []string{"GET"}, AuthRequired: true,
			Upstream: upstream, Timeout: 5 * time.Second,
		}},
		Keys: map[string]config.Key{sha256Hex(testKey): {ID: "k1", ConsumerID: "c1", ConsumerStatus: config.ConsumerActive}},
	}
	var logs bytes.Buffer
	api := apiHandler(staticSource{snap}, slog.New(slog.NewJSONHandler(&logs, nil)))

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
	snap := &config.Snapshot{Version: 1, Routes: []config.Route{{
		ID: "r1", PathPrefix: "/", Methods: []string{"GET"}, AuthRequired: false, Upstream: upstream, Timeout: 5 * time.Second,
	}}}
	var logs bytes.Buffer
	api := apiHandler(staticSource{snap}, slog.New(slog.NewJSONHandler(&logs, nil)))

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
		return &config.Snapshot{
			Version: v,
			Routes: []config.Route{{
				ID: fmt.Sprintf("r%d", v), PathPrefix: "/v1", Methods: []string{"GET"}, AuthRequired: true,
				Upstream: upstream, Timeout: timeout,
			}},
			Keys: map[string]config.Key{sha256Hex(testKey): {ID: "k1", ConsumerID: consumer, ConsumerStatus: config.ConsumerActive}},
		}
	}
	a, b := version(1, "consumer-a", 5*time.Second), version(2, "consumer-b", 4*time.Second)
	source := &swappingSource{}
	source.current.Store(a)
	api := apiHandler(source, slog.New(slog.DiscardHandler))

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
