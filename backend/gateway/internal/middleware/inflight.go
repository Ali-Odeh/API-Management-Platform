package middleware

import (
	"log/slog"
	"net/http"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/inflight"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/respond"
)

// InFlight runs after the rate limit, right before the proxy, and holds one
// of the workspace's in-flight slots (its max_concurrent) while the backend
// answers:
//
//	a slot free     → on to the backend; the slot is given back when the
//	                  answer is done, the client hangs up or a panic unwinds
//	workspace full  → 503 workspace_overloaded, with Retry-After: 1
//
// The limit is per gateway instance: a slow backend in one workspace can
// hold at most its share of this instance, so the other workspaces keep
// answering. Requests refused earlier (404, 401, 429) never take a slot.
func InFlight(slots *inflight.Limiter, log *slog.Logger) Middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			ws := reqctx.From(r.Context()).Workspace
			if ws == nil {
				// Only possible if main wires it without tenant before it.
				log.Error("in-flight limit called without a workspace", "path", r.URL.Path)
				respond.Error(w, http.StatusInternalServerError, "internal_error")
				return
			}
			slot, ok := slots.Acquire(ws.ID, ws.Limits.MaxConcurrent)
			if !ok {
				// A slot frees as soon as one of the running requests ends.
				w.Header().Set("Retry-After", "1")
				respond.Error(w, http.StatusServiceUnavailable, "workspace_overloaded")
				return
			}
			defer slot.Release()
			next.ServeHTTP(w, r)
		})
	}
}
