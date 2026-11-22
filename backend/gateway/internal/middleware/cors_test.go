package middleware

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/proxy"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/tenant"
)

const appOrigin = "https://app.example.com"

// corsSettings are a route's CORS settings, as the control plane sends them.
type corsSettings struct {
	origins, methods, headers []string
	credentials               bool
	maxAge                    int
}

// corsRoute is a /v1 route with the given CORS settings (nil: CORS off).
func corsRoute(t *testing.T, s *corsSettings) *config.Route {
	t.Helper()
	route := &config.Route{ID: "r", PathPrefix: "/v1", Methods: []string{"GET", "POST"}, AuthRequired: true}
	if s != nil {
		c, err := config.NewCORS(s.origins, s.methods, s.headers, s.credentials, s.maxAge)
		if err != nil {
			t.Fatalf("NewCORS: %v", err)
		}
		route.CORS = c
	}
	return route
}

// corsGateway is tenant → router → CORS → a backend that answers 200 with
// its own (wrong) CORS header and counts its calls.
func corsGateway(t *testing.T, cors *corsSettings) (http.Handler, *int) {
	t.Helper()
	route := corsRoute(t, cors)
	snap := snapshotOf(&config.Workspace{Version: 1, Routes: []config.Route{*route}})
	calls := new(int)
	backend := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		*calls++
		w.Header().Set("Access-Control-Allow-Origin", "https://evil.example")
		w.Header().Set("Access-Control-Allow-Credentials", "true")
		_, _ = io.WriteString(w, "ok")
	})
	h := tenant.Middleware(staticSource{snap}, "localhost")(proxy.NewRouter(CORS(slog.New(slog.DiscardHandler))(backend)))
	return h, calls
}

func preflight(h http.Handler, origin, method string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodOptions, "/v1/orders", nil)
	req.Header.Set("Origin", origin)
	req.Header.Set("Access-Control-Request-Method", method)
	req.Header.Set("Access-Control-Request-Headers", "content-type")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req.WithContext(reqctx.With(req.Context(), &reqctx.Info{})))
	return rec
}

func call(h http.Handler, origin string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodGet, "/v1/orders", nil)
	if origin != "" {
		req.Header.Set("Origin", origin)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req.WithContext(reqctx.With(req.Context(), &reqctx.Info{})))
	return rec
}

var appCORS = &corsSettings{
	origins: []string{appOrigin}, methods: []string{"GET", "POST"},
	headers: []string{"Content-Type", "X-API-Key"}, credentials: true, maxAge: 600,
}

func TestPreflightFromAnAllowedOrigin(t *testing.T) {
	gw, calls := corsGateway(t, appCORS)

	rec := preflight(gw, appOrigin, "POST")

	if rec.Code != http.StatusNoContent || *calls != 0 {
		t.Fatalf("status %d, backend called %d times; want 204 answered by the gateway", rec.Code, *calls)
	}
	want := map[string]string{
		"Access-Control-Allow-Origin":      appOrigin,
		"Access-Control-Allow-Methods":     "GET, POST",
		"Access-Control-Allow-Headers":     "Content-Type, X-API-Key",
		"Access-Control-Allow-Credentials": "true",
		"Access-Control-Max-Age":           "600",
	}
	for header, value := range want {
		if got := rec.Header().Get(header); got != value {
			t.Errorf("%s = %q, want %q", header, got, value)
		}
	}
	if !strings.Contains(rec.Header().Get("Vary"), "Origin") {
		t.Errorf("Vary = %q, want it to name Origin", rec.Header().Get("Vary"))
	}
}

// A refused preflight still gets 204, but no allow headers: the browser
// then blocks the call itself.
func TestRefusedPreflights(t *testing.T) {
	for name, tc := range map[string]struct {
		cors           *corsSettings
		origin, method string
	}{
		"unknown origin":      {appCORS, "https://evil.example", "GET"},
		"method not allowed":  {appCORS, appOrigin, "DELETE"},
		"route without CORS":  {nil, appOrigin, "GET"},
		"CORS with no origin": {&corsSettings{methods: []string{"GET"}, maxAge: 600}, appOrigin, "GET"},
	} {
		gw, calls := corsGateway(t, tc.cors)
		rec := preflight(gw, tc.origin, tc.method)
		if rec.Code != http.StatusNoContent || *calls != 0 {
			t.Errorf("%s: status %d, backend called %d times; want 204 from the gateway", name, rec.Code, *calls)
		}
		if got := rec.Header().Get("Access-Control-Allow-Origin"); got != "" {
			t.Errorf("%s: Access-Control-Allow-Origin = %q, want none", name, got)
		}
	}
}

// On a normal call the gateway's CORS headers replace the backend's.
func TestCallFromAnAllowedOriginWithCredentials(t *testing.T) {
	gw, calls := corsGateway(t, appCORS)

	rec := call(gw, appOrigin)

	if rec.Code != 200 || *calls != 1 {
		t.Fatalf("status %d, backend called %d times", rec.Code, *calls)
	}
	if got := rec.Header().Values("Access-Control-Allow-Origin"); len(got) != 1 || got[0] != appOrigin {
		t.Errorf("Access-Control-Allow-Origin = %v, want only %s", got, appOrigin)
	}
	if rec.Header().Get("Access-Control-Allow-Credentials") != "true" {
		t.Error("no Access-Control-Allow-Credentials")
	}
	if rec.Header().Get("Vary") != "Origin" {
		t.Errorf("Vary = %q, want Origin", rec.Header().Get("Vary"))
	}
}

func TestCallFromAnotherOriginGetsNoCORSHeaders(t *testing.T) {
	gw, _ := corsGateway(t, appCORS)
	for _, origin := range []string{"https://evil.example", ""} {
		rec := call(gw, origin)
		if rec.Code != 200 {
			t.Fatalf("status %d", rec.Code)
		}
		// Not even the backend's: on a route with CORS on, the gateway decides.
		if got := rec.Header().Get("Access-Control-Allow-Origin"); got != "" {
			t.Errorf("origin %q: Access-Control-Allow-Origin = %q, want none", origin, got)
		}
		if got := rec.Header().Get("Access-Control-Allow-Credentials"); got != "" {
			t.Errorf("origin %q: Access-Control-Allow-Credentials = %q, want none", origin, got)
		}
	}
}

func TestAnyOriginAnswersStar(t *testing.T) {
	gw, _ := corsGateway(t, &corsSettings{origins: []string{"*"}, methods: []string{"GET"}})

	rec := call(gw, "https://anyone.example")
	if got := rec.Header().Get("Access-Control-Allow-Origin"); got != "*" {
		t.Errorf("Access-Control-Allow-Origin = %q, want *", got)
	}
	if rec.Header().Get("Access-Control-Allow-Credentials") != "" {
		t.Error("credentials allowed with *")
	}
	rec = preflight(gw, "https://anyone.example", "GET")
	if rec.Header().Get("Access-Control-Allow-Origin") != "*" || rec.Header().Get("Access-Control-Allow-Headers") != "" ||
		rec.Header().Get("Access-Control-Max-Age") != "0" {
		t.Errorf("preflight headers = %v", rec.Header())
	}
}

// Without CORS on the route, the backend's own CORS headers pass unchanged.
func TestARouteWithoutCORSLeavesTheBackendsHeaders(t *testing.T) {
	gw, _ := corsGateway(t, nil)
	rec := call(gw, appOrigin)
	if got := rec.Header().Get("Access-Control-Allow-Origin"); got != "https://evil.example" {
		t.Errorf("Access-Control-Allow-Origin = %q, want the backend's", got)
	}
}

// The origin is compared without case, as browsers may send it.
func TestOriginsMatchWithoutCase(t *testing.T) {
	gw, _ := corsGateway(t, appCORS)
	if rec := call(gw, "HTTPS://APP.example.com"); rec.Header().Get("Access-Control-Allow-Origin") == "" {
		t.Error("an allowed origin in other case was refused")
	}
}

func TestCORSWithoutARouteIs500(t *testing.T) {
	var logs jsonLogger
	h := CORS(logs.logger())(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		t.Error("reached next without a route")
	}))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "/x", nil))
	if rec.Code != http.StatusInternalServerError || !strings.Contains(logs.buf.String(), "without a matched route") {
		t.Errorf("status %d, log %s", rec.Code, logs.buf.String())
	}
}

// A streamed answer gets its CORS headers on the first flush too.
func TestCORSHeadersOnAFlushedAnswer(t *testing.T) {
	route := corsRoute(t, appCORS)
	snap := snapshotOf(&config.Workspace{Version: 1, Routes: []config.Route{*route}})
	var unwrapped bool
	backend := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.(http.Flusher).Flush()
		w.WriteHeader(http.StatusTeapot) // too late: already started
		// The reverse proxy reaches the original writer through Unwrap.
		_, unwrapped = w.(interface{ Unwrap() http.ResponseWriter }).Unwrap().(*httptest.ResponseRecorder)
	})
	h := tenant.Middleware(staticSource{snap}, "localhost")(proxy.NewRouter(CORS(slog.New(slog.DiscardHandler))(backend)))

	rec := call(h, appOrigin)

	if rec.Header().Get("Access-Control-Allow-Origin") != appOrigin {
		t.Errorf("headers = %v", rec.Header())
	}
	if !unwrapped {
		t.Error("Unwrap did not lead to the original writer")
	}
}

func TestNewCORS(t *testing.T) {
	if c, err := config.NewCORS(nil, []string{"GET"}, nil, false, 600); c != nil || err != nil {
		t.Errorf("no origin = %v, %v; want CORS off", c, err)
	}
	for name, args := range map[string]corsSettings{
		"no methods":          {origins: []string{appOrigin}},
		"negative max age":    {origins: []string{appOrigin}, methods: []string{"GET"}, maxAge: -1},
		"any with credential": {origins: []string{"*"}, methods: []string{"GET"}, credentials: true},
	} {
		if _, err := config.NewCORS(args.origins, args.methods, args.headers, args.credentials, args.maxAge); err == nil {
			t.Errorf("%s: no error", name)
		}
	}
}
