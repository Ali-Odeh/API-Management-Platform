package proxy

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/tenant"
)

// fakeSource is a SnapshotSource that always returns the same snapshot.
type fakeSource struct{ snap *config.Snapshot }

func (f fakeSource) Current() *config.Snapshot { return f.snap }

func TestRouterAnswersItself(t *testing.T) {
	loaded := snapshotOf(&config.Workspace{Version: 7, Routes: testRoutes})

	tests := []struct {
		name       string
		snap       *config.Snapshot
		method     string
		path       string
		wantStatus int
		wantError  string
		wantHeader [2]string // a header that must be set, if any
	}{
		{name: "no config yet", snap: nil, method: "GET", path: "/v1", wantStatus: 503, wantError: "config_not_loaded", wantHeader: [2]string{"Retry-After", "1"}},
		{name: "unknown path", snap: loaded, method: "GET", path: "/v2", wantStatus: 404, wantError: "route_not_found"},
		// A request without a workspace subdomain never reaches another
		// workspace's routes: without `default`, the Host's workspace is unknown.
		{name: "no default workspace", snap: config.NewSnapshot(7, &config.Workspace{ID: "w1", Hosts: []string{"acme"}, Routes: testRoutes}), method: "GET", path: "/v1", wantStatus: 404, wantError: "workspace_not_found"},
		{name: "wrong method", snap: loaded, method: "PUT", path: "/v1/orders/7", wantStatus: 405, wantError: "method_not_allowed", wantHeader: [2]string{"Allow", "DELETE, GET, POST"}},

		// Encoded traversal arrives decoded; it must never be matched.
		{name: "encoded dot-dot", snap: loaded, method: "GET", path: "/v1/orders/%2e%2e/admin", wantStatus: 400, wantError: "invalid_path"},
		{name: "encoded slash and dot-dot", snap: loaded, method: "GET", path: "/v1/orders/..%2fadmin", wantStatus: 400, wantError: "invalid_path"},
		{name: "encoded dot segment", snap: loaded, method: "GET", path: "/v1/orders/%2e/7", wantStatus: 400, wantError: "invalid_path"},
		{name: "encoded double slash (found by fuzzing)", snap: loaded, method: "GET", path: "/%2F", wantStatus: 400, wantError: "invalid_path"},
		// Tenant runs first: without a config there is no workspace to check a path in.
		{name: "unclean path without config", snap: nil, method: "GET", path: "/v1/%2e%2e/x", wantStatus: 503, wantError: "config_not_loaded"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			next := http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
				t.Error("next was called for a request the router should answer itself")
			})
			rec := httptest.NewRecorder()

			routed(tt.snap, next).ServeHTTP(rec, httptest.NewRequest(tt.method, tt.path, nil))

			if rec.Code != tt.wantStatus {
				t.Fatalf("status = %d, want %d", rec.Code, tt.wantStatus)
			}
			var body map[string]string
			if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil || body["error"] != tt.wantError {
				t.Errorf("body = %q, want error %q", rec.Body.String(), tt.wantError)
			}
			if h := tt.wantHeader; h[0] != "" && rec.Header().Get(h[0]) != h[1] {
				t.Errorf("%s = %q, want %q", h[0], rec.Header().Get(h[0]), h[1])
			}
		})
	}
}

func TestRouterPassesTheMatchOn(t *testing.T) {
	snap := snapshotOf(&config.Workspace{Version: 7, Routes: testRoutes})

	var got Match
	var ok bool
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got, ok = FromContext(r.Context())
		w.WriteHeader(http.StatusTeapot)
	})
	rec := httptest.NewRecorder()

	routed(snap, next).ServeHTTP(rec, httptest.NewRequest("POST", "/v1/orders/7", nil))

	if rec.Code != http.StatusTeapot {
		t.Fatalf("status = %d, want next's 418", rec.Code)
	}
	if !ok {
		t.Fatal("FromContext found no match in next")
	}
	if got.Route.ID != "orders" {
		t.Errorf("route = %q, want orders", got.Route.ID)
	}
	if got.Workspace != snap.Default() {
		t.Error("next got a different workspace than the one used for matching")
	}
}

func TestIsCleanPath(t *testing.T) {
	tests := map[string]bool{
		"/":                   true,
		"/v1/orders":          true,
		"/v1/orders/":         true, // one trailing slash is fine
		"/v1/orders..x/y":     true, // ".." inside a name is not a segment
		"/v1/../admin":        false,
		"/v1/./orders":        false,
		"/v1//orders":         false,
		"/v1/orders//":        false,
		"/..":                 false,
		"v1/orders":           false, // not absolute
		"":                    false,
		"/v1/orders/../../x/": false,
		"//":                  false, // the root plus a slash is an empty segment (found by fuzzing)

	}
	for p, want := range tests {
		if got := isCleanPath(p); got != want {
			t.Errorf("isCleanPath(%q) = %v, want %v", p, got, want)
		}
	}
}

func TestFromContextWithoutRouter(t *testing.T) {
	if _, ok := FromContext(httptest.NewRequest("GET", "/", nil).Context()); ok {
		t.Error("FromContext found a match on a request that never went through the router")
	}
}

// snapshotOf is a config with ws as its `default` workspace (until MT.7
// every request is served from it).
func snapshotOf(ws *config.Workspace) *config.Snapshot {
	ws.ID, ws.Slug, ws.Status = "ws-default", config.DefaultHost, config.WorkspaceActive
	ws.Hosts = []string{config.DefaultHost}
	return config.NewSnapshot(ws.Version, ws)
}

// routed is the router behind tenant, as main wires it: a request (with no
// workspace subdomain) is served from the snapshot's `default` workspace.
func routed(snap *config.Snapshot, next http.Handler) http.Handler {
	return tenant.Middleware(fakeSource{snap}, "localhost")(NewRouter(next))
}

// Without tenant before it the router has no workspace to match in: a
// wiring bug, answered with 500 rather than guessing a workspace.
func TestRouterWithoutTenantIs500(t *testing.T) {
	next := http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		t.Error("next was called without a workspace")
	})
	rec := httptest.NewRecorder()

	NewRouter(next).ServeHTTP(rec, httptest.NewRequest("GET", "/v1", nil))

	if rec.Code != http.StatusInternalServerError {
		t.Errorf("status = %d, want 500", rec.Code)
	}
}

// A CORS preflight is matched by path alone (MT.11): it asks about another
// method, so the route's own methods don't decide. A plain OPTIONS (no
// Origin or no requested method) is an ordinary request.
func TestPreflightsAreMatchedByPathOnly(t *testing.T) {
	snap := snapshotOf(&config.Workspace{Version: 1, Routes: []config.Route{
		{ID: "orders", PathPrefix: "/v1/orders", Methods: []string{"POST"}},
		{ID: "v1", PathPrefix: "/v1", Methods: []string{"GET"}},
	}})
	var matched string
	h := routed(snap, http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		m, _ := FromContext(r.Context())
		matched = m.Route.ID
	}))
	send := func(origin, requested, path string) int {
		req := httptest.NewRequest(http.MethodOptions, path, nil)
		if origin != "" {
			req.Header.Set("Origin", origin)
		}
		if requested != "" {
			req.Header.Set("Access-Control-Request-Method", requested)
		}
		matched = ""
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		return rec.Code
	}

	if code := send("https://a.example", "PUT", "/v1/orders/7"); code != 200 || matched != "orders" {
		t.Errorf("preflight: %d, matched %q; want the longest prefix, orders", code, matched)
	}
	if code := send("https://a.example", "GET", "/v2"); code != http.StatusNotFound {
		t.Errorf("preflight without a route: %d, want 404", code)
	}
	for _, tc := range []struct{ origin, requested string }{{"", "PUT"}, {"https://a.example", ""}} {
		if code := send(tc.origin, tc.requested, "/v1/orders"); code != http.StatusMethodNotAllowed {
			t.Errorf("OPTIONS (origin %q, method %q): %d, want 405 like any other method", tc.origin, tc.requested, code)
		}
	}
}
