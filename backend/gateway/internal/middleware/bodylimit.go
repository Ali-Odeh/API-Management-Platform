package middleware

import (
	"errors"
	"io"
	"log/slog"
	"net/http"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/proxy"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/respond"
)

// BodyLimit keeps request bodies over the route's limit from reaching a
// backend (MT.11, decision 16). Matching never reads the body, so checking
// right after the router is still before anything is uploaded onwards:
//
//	Content-Length over the limit → 413 request_too_large at once, unread
//	no Content-Length (chunked)  → read through http.MaxBytesReader; going
//	                                over stops the upload and the forwarder
//	                                answers 413 (unless the answer had started)
//
// Either way the log line gets body_limit.
func BodyLimit(log *slog.Logger) Middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			m, ok := proxy.FromContext(r.Context())
			if !ok {
				// Only possible if main wires the limit before the router.
				log.Error("body limit called without a matched route", "path", r.URL.Path)
				respond.Error(w, http.StatusInternalServerError, "internal_error")
				return
			}
			limit := m.Route.MaxBodyBytes
			if limit <= 0 || r.Body == nil || r.Body == http.NoBody {
				next.ServeHTTP(w, r)
				return
			}
			info := reqctx.From(r.Context())
			if r.ContentLength > limit {
				info.BodyLimit.Store(limit)
				respond.Error(w, http.StatusRequestEntityTooLarge, "request_too_large")
				return
			}
			if r.ContentLength < 0 { // the length isn't announced
				r.Body = &limitedBody{
					ReadCloser: http.MaxBytesReader(w, r.Body, limit),
					info:       info,
					limit:      limit,
				}
			}
			next.ServeHTTP(w, r)
		})
	}
}

// limitedBody notes on the request's card when the body went over the
// limit, so the forwarder can answer 413 and the log can say why.
type limitedBody struct {
	io.ReadCloser
	info  *reqctx.Info
	limit int64
}

func (b *limitedBody) Read(p []byte) (int, error) {
	n, err := b.ReadCloser.Read(p)
	if _, over := errors.AsType[*http.MaxBytesError](err); over {
		b.info.BodyLimit.Store(b.limit)
	}
	return n, err
}
