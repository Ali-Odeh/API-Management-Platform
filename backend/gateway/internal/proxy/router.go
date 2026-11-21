package proxy

import (
	"context"
	"net/http"
	"path"
	"strings"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/respond"
)

// Router matches every API request to a route of its workspace. It answers
// 400, 404 or 405 itself; a matched request goes on to next with the match
// in its context.
type Router struct {
	next http.Handler
}

// NewRouter builds a router that matches requests within the workspace
// tenant found for them, and passes matched requests to next.
func NewRouter(next http.Handler) *Router {
	return &Router{next: next}
}

func (rt *Router) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// An encoded "../" ("%2e%2e/", "..%2f") arrives decoded and uncleaned.
	// Matched as is, "/v1/orders/../admin" would fit the /v1/orders route
	// and could reach a path outside it on the backend.
	if !isCleanPath(r.URL.Path) {
		respond.Error(w, http.StatusBadRequest, "invalid_path")
		return
	}

	// The workspace tenant took from the one snapshot it read: matching now
	// and the key check later use only its routes and keys, even if a newer
	// config arrives while the request is running.
	info := reqctx.From(r.Context())
	ws := info.Workspace
	if ws == nil {
		// Only possible if main wires the router without tenant before it.
		respond.Error(w, http.StatusInternalServerError, "internal_error")
		return
	}

	route, allowed := match(ws.Routes, r.URL.Path, r.Method)
	if route == nil {
		if len(allowed) > 0 {
			w.Header().Set("Allow", strings.Join(allowed, ", "))
			respond.Error(w, http.StatusMethodNotAllowed, "method_not_allowed")
			return
		}
		respond.Error(w, http.StatusNotFound, "route_not_found")
		return
	}

	info.RouteID = route.ID // for the request log
	ctx := context.WithValue(r.Context(), matchKey{}, Match{Workspace: ws, Route: route})
	rt.next.ServeHTTP(w, r.WithContext(ctx))
}

// isCleanPath reports whether p is absolute and has no ".", ".." or empty
// segments. One trailing slash is fine ("/v1/orders/").
func isCleanPath(p string) bool {
	if !strings.HasPrefix(p, "/") {
		return false
	}
	clean := path.Clean(p)
	// "/" + "/" is "//": an empty segment, not a trailing slash (found by
	// FuzzIsCleanPath).
	return p == clean || (clean != "/" && p == clean+"/")
}

// Match is what the router found for a request. Later steps read it with
// FromContext: auth needs the workspace's keys and the route's
// auth_required, the proxy needs the route's upstream and timeout. Both come
// from the one snapshot the router read.
type Match struct {
	Workspace *config.Workspace
	Route     *config.Route
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
