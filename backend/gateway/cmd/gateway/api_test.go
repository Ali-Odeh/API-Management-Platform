package main

import (
	"bytes"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
)

type staticSource struct{ snap *config.Snapshot }

func (s staticSource) Current() *config.Snapshot { return s.snap }

// The whole API path, as main builds it: request ID → logger → recover →
// router → forwarder → a real (fake) backend.
func TestAPIHandlerEndToEnd(t *testing.T) {
	// A channel, not a variable: the backend runs in another goroutine.
	gotRequestID := make(chan string, 1)
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotRequestID <- r.Header.Get("X-Request-ID")
		time.Sleep(20 * time.Millisecond)
		_, _ = io.WriteString(w, "order 7")
	}))
	defer backend.Close()

	upstream, _ := url.Parse(backend.URL)
	snap := &config.Snapshot{Version: 1, Routes: []config.Route{{
		ID: "r1", PathPrefix: "/v1/orders", Methods: []string{"GET"},
		Upstream: upstream, Timeout: 5 * time.Second,
	}}}
	var logs bytes.Buffer
	api := apiHandler(staticSource{snap}, slog.New(slog.NewJSONHandler(&logs, nil)))

	tests := []struct {
		name       string
		path       string
		wantStatus int
		wantRoute  string
	}{
		{name: "forwarded", path: "/v1/orders/7?secret=1", wantStatus: 200, wantRoute: "r1"},
		{name: "no route", path: "/v2/x", wantStatus: 404},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			logs.Reset()
			req := httptest.NewRequest("GET", tt.path, nil)
			req.Header.Set("X-Request-ID", "e2e-1")
			rec := httptest.NewRecorder()

			api.ServeHTTP(rec, req)

			if rec.Code != tt.wantStatus || rec.Header().Get("X-Request-ID") != "e2e-1" {
				t.Fatalf("client got %d with X-Request-ID %q", rec.Code, rec.Header().Get("X-Request-ID"))
			}
			var line map[string]any
			if err := json.Unmarshal(logs.Bytes(), &line); err != nil {
				t.Fatalf("want exactly one JSON log line, got %q", logs.String())
			}
			if line["request_id"] != "e2e-1" || line["route_id"] != tt.wantRoute || line["status"] != float64(tt.wantStatus) {
				t.Errorf("log line = %v", line)
			}
			if strings.Contains(logs.String(), "secret") {
				t.Errorf("query leaked into the log: %s", logs.String())
			}
		})
	}

	// The backend (called once, by the first case) got the same request ID.
	if id := <-gotRequestID; id != "e2e-1" {
		t.Errorf("backend got X-Request-ID %q, want e2e-1", id)
	}
}

func TestAPIHandlerTimesTheBackend(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		time.Sleep(30 * time.Millisecond)
	}))
	defer backend.Close()
	upstream, _ := url.Parse(backend.URL)
	snap := &config.Snapshot{Version: 1, Routes: []config.Route{{
		ID: "r1", PathPrefix: "/", Methods: []string{"GET"}, Upstream: upstream, Timeout: 5 * time.Second,
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
