package proxy

import (
	"strings"
	"testing"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
)

// route is a short way to write a test route.
func route(id, prefix string, methods ...string) config.Route {
	return config.Route{ID: id, PathPrefix: prefix, Methods: methods}
}

// Sorted longest prefix first, as config.Snapshot delivers them.
var testRoutes = []config.Route{
	route("archive", "/v1/orders/archive", "GET"),
	route("orders", "/v1/orders", "GET", "POST"),
	route("v1", "/v1", "GET", "DELETE"),
}

func TestMatch(t *testing.T) {
	tests := []struct {
		name        string
		routes      []config.Route
		method      string
		path        string
		wantRoute   string // route ID; empty means no match
		wantAllowed string // joined Allow list when there is no match
	}{
		// The longest fitting prefix wins.
		{name: "deeper path", routes: testRoutes, method: "GET", path: "/v1/orders/7", wantRoute: "orders"},
		{name: "exact prefix", routes: testRoutes, method: "GET", path: "/v1/orders", wantRoute: "orders"},
		{name: "trailing slash", routes: testRoutes, method: "GET", path: "/v1/orders/", wantRoute: "orders"},
		{name: "most specific of three", routes: testRoutes, method: "GET", path: "/v1/orders/archive/2024", wantRoute: "archive"},
		{name: "only the short prefix fits", routes: testRoutes, method: "GET", path: "/v1/users", wantRoute: "v1"},

		// Whole segments only.
		{name: "similar segment is not the same segment", routes: testRoutes, method: "GET", path: "/v1/orders-archive", wantRoute: "v1"},
		{name: "prefix glued to a longer word", routes: testRoutes, method: "GET", path: "/v1beta/x"},

		// The method decides among the routes whose path fits.
		{name: "falls back to a shorter route that allows the method", routes: testRoutes, method: "DELETE", path: "/v1/orders/7", wantRoute: "v1"},
		{name: "longest route that allows the method", routes: testRoutes, method: "POST", path: "/v1/orders/archive/1", wantRoute: "orders"},
		{name: "path fits but no method does", routes: testRoutes, method: "PUT", path: "/v1/orders/7", wantAllowed: "DELETE,GET,POST"},
		{name: "methods match exactly (no HEAD for GET)", routes: testRoutes, method: "HEAD", path: "/v1", wantAllowed: "DELETE,GET"},

		// No fitting path at all.
		{name: "unknown path", routes: testRoutes, method: "GET", path: "/v2/x"},
		{name: "paths are case-sensitive", routes: testRoutes, method: "GET", path: "/V1/orders"},
		{name: "root path without a root route", routes: testRoutes, method: "GET", path: "/"},
		{name: "no routes at all", routes: nil, method: "GET", path: "/v1"},

		// A root route catches whatever nothing longer catches.
		{name: "root route catches the rest", routes: append(testRoutes[:3:3], route("root", "/", "GET")), method: "GET", path: "/anything/else", wantRoute: "root"},
		{name: "root route loses to a longer one", routes: append(testRoutes[:3:3], route("root", "/", "GET")), method: "GET", path: "/v1/orders/1", wantRoute: "orders"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, allowed := match(tt.routes, tt.path, tt.method)

			if tt.wantRoute != "" {
				if got == nil || got.ID != tt.wantRoute {
					t.Fatalf("match(%s %s) = %v, want route %q", tt.method, tt.path, got, tt.wantRoute)
				}
				if allowed != nil {
					t.Errorf("allowed = %v, want nil on a match", allowed)
				}
				return
			}
			if got != nil {
				t.Fatalf("match(%s %s) = route %q, want no match", tt.method, tt.path, got.ID)
			}
			if joined := strings.Join(allowed, ","); joined != tt.wantAllowed {
				t.Errorf("allowed = %q, want %q", joined, tt.wantAllowed)
			}
		})
	}
}

func TestMatchReturnsTheRouteFromTheList(t *testing.T) {
	// The caller gets a pointer into the snapshot's own list, not a copy.
	got, _ := match(testRoutes, "/v1/orders/7", "GET")
	if got != &testRoutes[1] {
		t.Error("match did not return a pointer to the snapshot's route")
	}
}
