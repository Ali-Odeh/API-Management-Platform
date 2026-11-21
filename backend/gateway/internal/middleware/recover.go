package middleware

import (
	"fmt"
	"log/slog"
	"net/http"
	"runtime/debug"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/respond"
)

// Recover turns a panic (a crash from a bug) inside one request into a 500
// for that request only. The gateway keeps serving everyone else, and the
// panic is logged with its stack trace and the request ID.
//
// It sits inside Logger, so the request's log line shows the 500.
func Recover(log *slog.Logger) Middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			sw := &statusWriter{ResponseWriter: w}
			defer func() {
				p := recover()
				if p == nil {
					return
				}
				// The reverse proxy panics with ErrAbortHandler on purpose to
				// cut the client's connection when a backend fails in the
				// middle of its answer. That is not a bug: let it through.
				if p == http.ErrAbortHandler {
					panic(p)
				}

				log.Error("panic while serving a request",
					"request_id", reqctx.From(r.Context()).RequestID,
					"workspace_id", reqctx.From(r.Context()).WorkspaceID(),
					"panic", fmt.Sprint(p),
					"stack", string(debug.Stack()))

				if sw.started() {
					// Part of the answer is already sent; a 500 can't follow.
					// Cut the connection so the client sees a broken answer,
					// not a complete-looking one.
					panic(http.ErrAbortHandler)
				}
				respond.Error(w, http.StatusInternalServerError, "internal_error")
			}()
			next.ServeHTTP(sw, r)
		})
	}
}
