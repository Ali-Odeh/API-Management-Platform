package proxy

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
)

// fakeSource is a SnapshotSource that always returns the same snapshot.
type fakeSource struct{ snap *config.Snapshot }

func (f fakeSource) Current() *config.Snapshot { return f.snap }

func TestRouterAnswersItself(t *testing.T) {
	loaded := &config.Snapshot{Version: 7, Routes: testRoutes}

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
		{name: "wrong method", snap: loaded, method: "PUT", path: "/v1/orders/7", wantStatus: 405, wantError: "method_not_allowed", wantHeader: [2]string{"Allow", "DELETE, GET, POST"}},

		// Encoded traversal arrives decoded; it must never be matched.
		{name: "encoded dot-dot", snap: loaded, method: "GET", path: "/v1/orders/%2e%2e/admin", wantStatus: 400, wantError: "invalid_path"},
		{name: "encoded slash and dot-dot", snap: loaded, method: "GET", path: "/v1/orders/..%2fadmin", wantStatus: 400, wantError: "invalid_path"},
		{name: "encoded dot segment", snap: loaded, method: "GET", path: "/v1/orders/%2e/7", wantStatus: 400, wantError: "invalid_path"},
		{name: "unclean path checked before config", snap: nil, method: "GET", path: "/v1/%2e%2e/x", wantStatus: 400, wantError: "invalid_path"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			next := http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
				t.Error("next was called for a request the router should answer itself")
			})
			rec := httptest.NewRecorder()

			NewRouter(fakeSource{tt.snap}, next).ServeHTTP(rec, httptest.NewRequest(tt.method, tt.path, nil))

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
	snap := &config.Snapshot{Version: 7, Routes: testRoutes}

	var got Match
	var ok bool
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got, ok = FromContext(r.Context())
		w.WriteHeader(http.StatusTeapot)
	})
	rec := httptest.NewRecorder()

	NewRouter(fakeSource{snap}, next).ServeHTTP(rec, httptest.NewRequest("POST", "/v1/orders/7", nil))

	if rec.Code != http.StatusTeapot {
		t.Fatalf("status = %d, want next's 418", rec.Code)
	}
	if !ok {
		t.Fatal("FromContext found no match in next")
	}
	if got.Route.ID != "orders" {
		t.Errorf("route = %q, want orders", got.Route.ID)
	}
	if got.Snapshot != snap {
		t.Error("next got a different snapshot than the one used for matching")
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
