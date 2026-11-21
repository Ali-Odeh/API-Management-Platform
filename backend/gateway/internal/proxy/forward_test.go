package proxy

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
)

// received is what a fake backend saw.
type received struct {
	method     string
	requestURI string // the path and query exactly as sent on the wire
	host       string
	header     http.Header
	body       string
}

// newEchoBackend answers 201 with a header and a body, and reports every
// request it receives on the returned channel.
func newEchoBackend(t *testing.T) (*httptest.Server, <-chan received) {
	t.Helper()
	got := make(chan received, 1)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		got <- received{method: r.Method, requestURI: r.RequestURI, host: r.Host, header: r.Header.Clone(), body: string(body)}
		w.Header().Set("X-Backend", "yes")
		w.WriteHeader(http.StatusCreated)
		_, _ = io.WriteString(w, "from backend")
	}))
	t.Cleanup(srv.Close)
	return srv, got
}

// testRoute describes the one route of a test gateway.
type testRoute struct {
	prefix   string
	upstream string
	strip    bool
	timeout  time.Duration
}

// newGateway is the router plus the forwarder, as main wires them, with one
// route. logs collects the forwarder's log lines.
func newGateway(t *testing.T, tr testRoute) (http.Handler, *strings.Builder) {
	t.Helper()
	upstream, err := url.Parse(tr.upstream)
	if err != nil {
		t.Fatalf("bad upstream %q: %v", tr.upstream, err)
	}
	if tr.timeout == 0 {
		tr.timeout = 5 * time.Second
	}
	snap := snapshotOf(&config.Workspace{Version: 1, Routes: []config.Route{{
		ID: "r1", PathPrefix: tr.prefix, Methods: []string{"GET", "POST"},
		StripPrefix: tr.strip, Upstream: upstream, Timeout: tr.timeout,
	}}})
	logs := &strings.Builder{}
	f := NewForwarder(slog.New(slog.NewJSONHandler(logs, nil)))
	return routed(snap, f), logs
}

func errorCode(t *testing.T, rec *httptest.ResponseRecorder) string {
	t.Helper()
	var body map[string]string
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("body %q is not a JSON error: %v", rec.Body.String(), err)
	}
	return body["error"]
}

func TestForwardBuildsTheBackendURL(t *testing.T) {
	tests := []struct {
		name    string
		prefix  string
		base    string // path on the upstream URL
		strip   bool
		target  string
		wantURI string
	}{
		{name: "path and query kept", prefix: "/v1/orders", target: "/v1/orders/7?x=1&y=2", wantURI: "/v1/orders/7?x=1&y=2"},
		{name: "strip prefix", prefix: "/v1/orders", strip: true, target: "/v1/orders/7?x=1", wantURI: "/7?x=1"},
		{name: "strip the bare prefix", prefix: "/v1/orders", strip: true, target: "/v1/orders", wantURI: "/"},
		{name: "strip prefix with trailing slash", prefix: "/v1/orders", strip: true, target: "/v1/orders/", wantURI: "/"},
		{name: "upstream base path, strip", prefix: "/v1/orders", base: "/api", strip: true, target: "/v1/orders/7", wantURI: "/api/7"},
		{name: "upstream base path, no strip", prefix: "/v1/orders", base: "/api", target: "/v1/orders/7", wantURI: "/api/v1/orders/7"},
		{name: "root route with strip keeps the path", prefix: "/", strip: true, target: "/v1/x", wantURI: "/v1/x"},

		// Encoded characters reach the backend exactly as the client sent them.
		{name: "encoded slash kept", prefix: "/v1/files", target: "/v1/files/a%2Fb.txt", wantURI: "/v1/files/a%2Fb.txt"},
		{name: "encoded slash kept after strip", prefix: "/v1/files", strip: true, target: "/v1/files/a%2Fb.txt", wantURI: "/a%2Fb.txt"},
		{name: "encoded space kept", prefix: "/v1/files", target: "/v1/files/a%20b", wantURI: "/v1/files/a%20b"},
		// The client encoded the prefix itself: still routed correctly, the
		// rest is re-encoded from the decoded path.
		{name: "client-encoded prefix", prefix: "/v1/files", strip: true, target: "/v1/%66iles/a%2Fb", wantURI: "/a/b"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			backend, got := newEchoBackend(t)
			gw, _ := newGateway(t, testRoute{prefix: tt.prefix, upstream: backend.URL + tt.base, strip: tt.strip})
			rec := httptest.NewRecorder()

			gw.ServeHTTP(rec, httptest.NewRequest("GET", tt.target, nil))

			if rec.Code != http.StatusCreated {
				t.Fatalf("status = %d, want the backend's 201 (body %q)", rec.Code, rec.Body.String())
			}
			if r := <-got; r.requestURI != tt.wantURI {
				t.Errorf("backend got %q, want %q", r.requestURI, tt.wantURI)
			}
		})
	}
}

func TestForwardPassesRequestAndAnswerThrough(t *testing.T) {
	backend, got := newEchoBackend(t)
	gw, _ := newGateway(t, testRoute{prefix: "/v1/orders", upstream: backend.URL})
	req := httptest.NewRequest("POST", "/v1/orders", strings.NewReader(`{"item":"book"}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Custom", "kept")
	rec := httptest.NewRecorder()

	gw.ServeHTTP(rec, req)

	r := <-got
	if r.method != "POST" || r.body != `{"item":"book"}` {
		t.Errorf("backend got %s with body %q", r.method, r.body)
	}
	if r.header.Get("X-Custom") != "kept" || r.header.Get("Content-Type") != "application/json" {
		t.Errorf("backend headers = %v", r.header)
	}
	// The backend's answer comes back unchanged.
	if rec.Code != http.StatusCreated || rec.Body.String() != "from backend" || rec.Header().Get("X-Backend") != "yes" {
		t.Errorf("client got %d %q headers %v", rec.Code, rec.Body.String(), rec.Header())
	}
}

func TestForwardSetsTrustedForwardingHeaders(t *testing.T) {
	backend, got := newEchoBackend(t)
	gw, _ := newGateway(t, testRoute{prefix: "/v1", upstream: backend.URL})
	req := httptest.NewRequest("GET", "http://api.example.com/v1/x", nil)
	req.RemoteAddr = "203.0.113.7:51234"
	// A client trying to pass as an internal caller.
	req.Header.Set("X-Forwarded-For", "10.0.0.1")
	req.Header.Set("X-Forwarded-Host", "admin.internal")
	req.Header.Set("Forwarded", "for=10.0.0.1")
	// Tracing headers pass through untouched (W3C Trace Context, v3 OpenTelemetry).
	const trace = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
	req.Header.Set("Traceparent", trace)

	gw.ServeHTTP(httptest.NewRecorder(), req)

	r := <-got
	backendHost := strings.TrimPrefix(backend.URL, "http://")
	checks := map[string][2]string{
		"Host":              {r.host, backendHost},
		"X-Forwarded-For":   {r.header.Get("X-Forwarded-For"), "203.0.113.7"},
		"X-Forwarded-Host":  {r.header.Get("X-Forwarded-Host"), "api.example.com"},
		"X-Forwarded-Proto": {r.header.Get("X-Forwarded-Proto"), "http"},
		"Forwarded":         {r.header.Get("Forwarded"), ""},
		"Traceparent":       {r.header.Get("Traceparent"), trace},
	}
	for name, c := range checks {
		if c[0] != c[1] {
			t.Errorf("%s = %q, want %q", name, c[0], c[1])
		}
	}
}

func TestForwardPassesBackendErrorsThrough(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "backend broke", http.StatusInternalServerError)
	}))
	defer backend.Close()
	gw, _ := newGateway(t, testRoute{prefix: "/v1", upstream: backend.URL})
	rec := httptest.NewRecorder()

	gw.ServeHTTP(rec, httptest.NewRequest("GET", "/v1/x", nil))

	// The backend's own 500, not a gateway error.
	if rec.Code != http.StatusInternalServerError || !strings.Contains(rec.Body.String(), "backend broke") {
		t.Errorf("client got %d %q, want the backend's 500", rec.Code, rec.Body.String())
	}
}

func TestForwardSlowBackendIs504(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done(): // the gateway gave up
		case <-time.After(5 * time.Second):
		}
	}))
	defer backend.Close()
	gw, logs := newGateway(t, testRoute{prefix: "/v1", upstream: backend.URL, timeout: 50 * time.Millisecond})
	rec := httptest.NewRecorder()

	start := time.Now()
	gw.ServeHTTP(rec, httptest.NewRequest("GET", "/v1/x", nil))

	if rec.Code != http.StatusGatewayTimeout || errorCode(t, rec) != "gateway_timeout" {
		t.Fatalf("client got %d %q, want 504 gateway_timeout", rec.Code, rec.Body.String())
	}
	if took := time.Since(start); took > 2*time.Second {
		t.Errorf("took %s, want about the 50ms route timeout", took)
	}
	if !strings.Contains(logs.String(), `"status":504`) {
		t.Errorf("no warning logged: %s", logs.String())
	}
}

func TestForwardDeadBackendIs502(t *testing.T) {
	backend := httptest.NewServer(http.NotFoundHandler())
	deadURL := backend.URL
	backend.Close() // nothing listens there any more

	gw, logs := newGateway(t, testRoute{prefix: "/v1", upstream: deadURL})
	rec := httptest.NewRecorder()

	gw.ServeHTTP(rec, httptest.NewRequest("GET", "/v1/x", nil))

	if rec.Code != http.StatusBadGateway || errorCode(t, rec) != "bad_gateway" {
		t.Fatalf("client got %d %q, want 502 bad_gateway", rec.Code, rec.Body.String())
	}
	for _, want := range []string{`"msg":"backend call failed"`, `"route_id":"r1"`, `"status":502`} {
		if !strings.Contains(logs.String(), want) {
			t.Errorf("log does not contain %s: %s", want, logs.String())
		}
	}
}

func TestForwardClientGoneIs499(t *testing.T) {
	backend, _ := newEchoBackend(t)
	gw, logs := newGateway(t, testRoute{prefix: "/v1", upstream: backend.URL})
	ctx, cancel := context.WithCancel(t.Context())
	cancel() // the client already hung up
	rec := httptest.NewRecorder()

	gw.ServeHTTP(rec, httptest.NewRequest("GET", "/v1/x", nil).WithContext(ctx))

	if rec.Code != StatusClientClosedRequest || rec.Body.Len() != 0 {
		t.Errorf("got %d %q, want 499 with no body", rec.Code, rec.Body.String())
	}
	if logs.Len() != 0 {
		t.Errorf("a client leaving is not a backend problem, but it was logged: %s", logs.String())
	}
}

func TestForwardWithoutMatchIs500(t *testing.T) {
	logs := &strings.Builder{}
	rec := httptest.NewRecorder()

	NewForwarder(slog.New(slog.NewJSONHandler(logs, nil))).ServeHTTP(rec, httptest.NewRequest("GET", "/v1/x", nil))

	if rec.Code != http.StatusInternalServerError || errorCode(t, rec) != "internal_error" {
		t.Errorf("got %d %q, want 500 internal_error", rec.Code, rec.Body.String())
	}
	if !strings.Contains(logs.String(), "without a matched route") {
		t.Errorf("wiring error not logged: %s", logs.String())
	}
}

// timeoutError is a network error that reports a timeout, like a dial
// timeout does.
type timeoutError struct{}

func (timeoutError) Error() string   { return "i/o timeout" }
func (timeoutError) Timeout() bool   { return true }
func (timeoutError) Temporary() bool { return true }

func TestClassify(t *testing.T) {
	tests := []struct {
		name       string
		ctxErr     error
		err        error
		wantStatus int
		wantCode   string
	}{
		{"route timeout", context.DeadlineExceeded, context.DeadlineExceeded, 504, "gateway_timeout"},
		{"client left", context.Canceled, context.Canceled, 499, "client_closed_request"},
		{"connect timeout", nil, timeoutError{}, 504, "gateway_timeout"},
		{"refused or reset", nil, errors.New("connection refused"), 502, "bad_gateway"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			status, code := classify(tt.ctxErr, tt.err)
			if status != tt.wantStatus || code != tt.wantCode {
				t.Errorf("classify() = %d %q, want %d %q", status, code, tt.wantStatus, tt.wantCode)
			}
		})
	}
}

// The forwarder notes the backend's time and the router the route, for the
// request log.
func TestForwardReportsRouteAndUpstreamTime(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		time.Sleep(30 * time.Millisecond)
	}))
	defer backend.Close()
	gw, _ := newGateway(t, testRoute{prefix: "/v1", upstream: backend.URL})
	info := &reqctx.Info{}
	req := httptest.NewRequest("GET", "/v1/x", nil)

	gw.ServeHTTP(httptest.NewRecorder(), req.WithContext(reqctx.With(req.Context(), info)))

	if info.RouteID != "r1" {
		t.Errorf("RouteID = %q, want r1", info.RouteID)
	}
	if info.Upstream < 30*time.Millisecond || info.Upstream > 2*time.Second {
		t.Errorf("Upstream = %s, want about 30ms", info.Upstream)
	}
}

func TestTransportLimits(t *testing.T) {
	tr := newTransport()
	if tr.MaxIdleConnsPerHost != 100 || tr.MaxIdleConns != 1000 || tr.IdleConnTimeout != 90*time.Second {
		t.Errorf("pool = %d per host, %d total, idle %s", tr.MaxIdleConnsPerHost, tr.MaxIdleConns, tr.IdleConnTimeout)
	}
	if tr.TLSHandshakeTimeout != 5*time.Second || tr.Proxy != nil {
		t.Errorf("TLS timeout %s, proxy set %v", tr.TLSHandshakeTimeout, tr.Proxy != nil)
	}
}
