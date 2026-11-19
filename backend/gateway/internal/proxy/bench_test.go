package proxy

// Benchmarks: how long route matching takes as the number of routes grows.
// They are run by hand, not in CI (CI machines are too noisy for timing):
//
//	go test -run=^$ -bench=. -benchmem ./internal/proxy

import (
	"fmt"
	"testing"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
)

// benchRoutes builds n routes like /svc0000/api ... all the same length, so
// the scan has no shortcut.
func benchRoutes(n int) []config.Route {
	routes := make([]config.Route, n)
	for i := range routes {
		routes[i] = route(fmt.Sprintf("r%d", i), fmt.Sprintf("/svc%04d/api", i), "GET", "POST")
	}
	return routes
}

func BenchmarkMatch(b *testing.B) {
	for _, n := range []int{10, 100, 1000} {
		routes := benchRoutes(n)
		last := fmt.Sprintf("/svc%04d/api/orders/7", n-1)

		// Worst case for a match: the right route is the last one checked.
		b.Run(fmt.Sprintf("routes=%d/last_route", n), func(b *testing.B) {
			for b.Loop() {
				match(routes, last, "GET")
			}
		})
		// Worst case overall: every route is checked and none fits (404).
		b.Run(fmt.Sprintf("routes=%d/no_route", n), func(b *testing.B) {
			for b.Loop() {
				match(routes, "/unknown/path", "GET")
			}
		})
	}
}
