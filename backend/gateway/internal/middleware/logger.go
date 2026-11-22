package middleware

import (
	"log/slog"
	"math"
	"net"
	"net/http"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
)

// Logger writes one JSON line per request once the answer is done
// (PLAN.md section 7):
//
//	{"msg":"request","request_id":"...","workspace_id":"ws1","method":"GET","path":"/v1/orders/7",
//	 "route_id":"r1","consumer_id":"c1","key_id":"k1","status":200,"latency_ms":12.4,
//	 "upstream_ms":9.1,"gateway_ms":3.3,"client_ip":"172.18.0.1","bytes":245}
//
// latency_ms is the whole request; upstream_ms is the wait for the backend;
// gateway_ms is the difference: the time the gateway itself added. The query
// string is never logged (it can hold tokens or personal data), and neither
// is the API key.
func Logger(log *slog.Logger) Middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			if !reqctx.Has(r.Context()) {
				r = r.WithContext(reqctx.With(r.Context(), &reqctx.Info{}))
			}
			info := reqctx.From(r.Context())
			sw := &statusWriter{ResponseWriter: w}

			// Deferred, so the line is written even when the reverse proxy
			// aborts a broken answer (http.ErrAbortHandler).
			defer func() {
				p := recover()
				logRequest(log, r, info, sw, time.Since(start), p != nil)
				if p != nil {
					panic(p) // let net/http close the connection
				}
			}()
			next.ServeHTTP(sw, r)
		})
	}
}

func logRequest(log *slog.Logger, r *http.Request, info *reqctx.Info, sw *statusWriter, latency time.Duration, aborted bool) {
	attrs := []slog.Attr{
		slog.String("request_id", info.RequestID),
		slog.String("workspace_id", info.WorkspaceID()),
		slog.String("method", r.Method),
		slog.String("path", r.URL.Path),
		slog.String("route_id", info.RouteID),
		slog.String("target_id", info.TargetID),
		slog.String("consumer_id", info.ConsumerID),
		slog.String("key_id", info.KeyID),
		slog.String("ratelimit", info.RateLimit),
		slog.Int("status", sw.finalStatus()),
		slog.Float64("latency_ms", millis(latency)),
		slog.Float64("upstream_ms", millis(info.Upstream)),
		slog.Float64("gateway_ms", millis(latency-info.Upstream)),
		slog.String("client_ip", clientIP(r)),
		slog.Int64("bytes", sw.bytes),
	}
	if limit := info.BodyLimit.Load(); limit > 0 {
		attrs = append(attrs, slog.Int64("body_limit", limit)) // refused for its size
	}
	if aborted {
		// The backend failed in the middle of its answer; the client got a
		// cut-off response.
		attrs = append(attrs, slog.Bool("aborted", true))
	}
	log.LogAttrs(r.Context(), slog.LevelInfo, "request", attrs...)
}

// millis turns a duration into milliseconds with microsecond precision
// (0.042), because the gateway's own time is often below 1 ms.
func millis(d time.Duration) float64 {
	return math.Round(float64(d)/float64(time.Microsecond)) / 1000
}

// clientIP is the address of whoever connected to the gateway. Behind a load
// balancer this would be the balancer; trusted proxies come when we deploy
// behind one.
func clientIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}
