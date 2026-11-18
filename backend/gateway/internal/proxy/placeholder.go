package proxy

import (
	"net/http"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/respond"
)

// NotImplemented stands where the reverse proxy will be. It answers 501 and
// names the matched route, so route matching can be tried live before
// forwarding exists.
//
// TEMPORARY: step 3.4 replaces it with the real reverse proxy.
func NotImplemented() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body := map[string]string{"error": "not_implemented"}
		if m, ok := FromContext(r.Context()); ok {
			body["route_id"] = m.Route.ID
			body["path_prefix"] = m.Route.PathPrefix
		}
		respond.JSON(w, http.StatusNotImplemented, body)
	})
}
