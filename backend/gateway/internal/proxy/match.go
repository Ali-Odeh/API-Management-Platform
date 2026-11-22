// Package proxy decides where each API request goes (route matching) and,
// from step 3.4, forwards it there.
package proxy

import (
	"net/http"
	"slices"
	"strings"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
)

// match finds the route for a request.
//
// routes must be sorted longest path_prefix first (config.Snapshot does
// that), so the first route that fits is the most specific one. A route fits
// when its prefix fits the path (see prefixFits) and it allows the method.
//
// When no route fits:
//   - allowed lists the methods of the routes whose prefix fit the path, so
//     the caller can answer 405 with an Allow header ("right place, wrong
//     method");
//   - allowed is empty when no prefix fit at all (404, "wrong place").
//
// It is a plain scan of the list. That is fast enough for hundreds of
// routes, and the search can change later without touching any caller.
func match(routes []config.Route, path, method string) (route *config.Route, allowed []string) {
	for i := range routes {
		r := &routes[i]
		if !prefixFits(r.PathPrefix, path) {
			continue
		}
		if slices.Contains(r.Methods, method) {
			return r, nil
		}
		allowed = append(allowed, r.Methods...)
	}
	// Sorted and without repeats, so the Allow header is the same every time.
	slices.Sort(allowed)
	return nil, slices.Compact(allowed)
}

// prefixFits reports whether a route prefix covers a request path, on whole
// path segments: "/v1/orders" covers "/v1/orders", "/v1/orders/" and
// "/v1/orders/7", but not "/v1/orders-archive". The root "/" covers every
// path. Prefixes come from the control plane without a trailing slash.
// Paths are compared case-sensitively, as HTTP defines them.
func prefixFits(prefix, path string) bool {
	if prefix == "/" {
		return true
	}
	if !strings.HasPrefix(path, prefix) {
		return false
	}
	// Either the whole path, or the prefix ends exactly at a "/".
	return len(path) == len(prefix) || path[len(prefix)] == '/'
}

// matchPath finds the most specific route whose prefix fits the path,
// whatever its methods. A CORS preflight is matched this way (decision 15):
// it asks about another method, and its own (OPTIONS) is the browser's.
func matchPath(routes []config.Route, path string) *config.Route {
	for i := range routes {
		if prefixFits(routes[i].PathPrefix, path) {
			return &routes[i]
		}
	}
	return nil
}

// IsPreflight reports whether r is a browser's CORS preflight: OPTIONS with
// an Origin and the method it wants to use.
func IsPreflight(r *http.Request) bool {
	return r.Method == http.MethodOptions &&
		r.Header.Get("Origin") != "" &&
		r.Header.Get("Access-Control-Request-Method") != ""
}
