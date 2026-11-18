package proxy

import (
	"context"
	"net/http"
	"path"
	"strings"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/respond"
)

// SnapshotSource gives the config snapshot in use, or nil before the first
// one arrives. *config.Store satisfies it; tests pass a fake.
//
// The interface is declared here, where it is used, not in package config:
// the router asks only for what it needs.
type SnapshotSource interface {
	Current() *config.Snapshot
}

// Router matches every API request to a route. It answers 503, 404 or 405
// itself; a matched request goes on to next with the match in its context.
type Router struct {
	source SnapshotSource
	next   http.Handler
}

// NewRouter builds a router that reads routes from source and passes matched
// requests to next.
func NewRouter(source SnapshotSource, next http.Handler) *Router {
	return &Router{source: source, next: next}
}

func (rt *Router) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// An encoded "../" ("%2e%2e/", "..%2f") arrives decoded and uncleaned.
	// Matched as is, "/v1/orders/../admin" would fit the /v1/orders route
	// and could reach a path outside it on the backend.
	if !isCleanPath(r.URL.Path) {
		respond.Error(w, http.StatusBadRequest, "invalid_path")
		return
	}

	// Read the config once. The whole request (matching now, the key check
	// in step 3.6) uses this one snapshot, even if a newer one arrives
	// while the request is running.
	snap := rt.source.Current()
	if snap == nil {
		w.Header().Set("Retry-After", "1")
		respond.Error(w, http.StatusServiceUnavailable, "config_not_loaded")
		return
	}

	route, allowed := match(snap.Routes, r.URL.Path, r.Method)
	if route == nil {
		if len(allowed) > 0 {
			w.Header().Set("Allow", strings.Join(allowed, ", "))
			respond.Error(w, http.StatusMethodNotAllowed, "method_not_allowed")
			return
		}
		respond.Error(w, http.StatusNotFound, "route_not_found")
		return
	}

	ctx := context.WithValue(r.Context(), matchKey{}, Match{Snapshot: snap, Route: route})
	rt.next.ServeHTTP(w, r.WithContext(ctx))
}

// isCleanPath reports whether p is absolute and has no ".", ".." or empty
// segments. One trailing slash is fine ("/v1/orders/").
func isCleanPath(p string) bool {
	if !strings.HasPrefix(p, "/") {
		return false
	}
	clean := path.Clean(p)
	return p == clean || p == clean+"/"
}

// Match is what the router found for a request. Later steps read it with
// FromContext: auth (3.6) needs the snapshot's keys and the route's
// auth_required, the proxy (3.4) needs the route's upstream and timeout.
type Match struct {
	Snapshot *config.Snapshot
	Route    *config.Route
}

// matchKey is the context key for Match. An unexported type means no other
// package can read or overwrite the value by accident.
type matchKey struct{}

// FromContext returns the Match the router stored for this request.
// ok is false if the request did not go through the router.
func FromContext(ctx context.Context) (m Match, ok bool) {
	m, ok = ctx.Value(matchKey{}).(Match)
	return m, ok
}
