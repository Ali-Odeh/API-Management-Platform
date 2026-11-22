package middleware

import (
	"log/slog"
	"net/http"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/proxy"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/respond"
)

// CORS response headers.
const (
	allowOrigin      = "Access-Control-Allow-Origin"
	allowCredentials = "Access-Control-Allow-Credentials"
	allowMethods     = "Access-Control-Allow-Methods"
	allowHeaders     = "Access-Control-Allow-Headers"
	maxAge           = "Access-Control-Max-Age"
)

// CORS lets browser pages call a route from the origins it allows (MT.11,
// decision 15). It runs right after the router, before auth:
//
//	preflight (OPTIONS + Origin + Access-Control-Request-Method)
//	    → answered here with 204, never forwarded, no key, no rate limit;
//	      allow headers only for an allowed origin and method, so the
//	      browser blocks anything else
//	request with an allowed Origin
//	    → on, and the answer gets Access-Control-Allow-Origin (and -Credentials)
//
// On a route with CORS on, the gateway owns these headers: a backend's own
// Access-Control-Allow-Origin and -Credentials are replaced, so a browser
// never sees two (which it rejects). The answer varies with Origin, so it
// says so for caches.
func CORS(log *slog.Logger) Middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			m, ok := proxy.FromContext(r.Context())
			if !ok {
				// Only possible if main wires CORS before the router.
				log.Error("cors called without a matched route", "path", r.URL.Path)
				respond.Error(w, http.StatusInternalServerError, "internal_error")
				return
			}
			c := m.Route.CORS
			origin := r.Header.Get("Origin")

			if proxy.IsPreflight(r) {
				answerPreflight(w, c, origin, r.Header.Get("Access-Control-Request-Method"))
				return
			}
			if c == nil {
				next.ServeHTTP(w, r)
				return
			}
			next.ServeHTTP(&corsWriter{ResponseWriter: w, cors: c, origin: origin}, r)
		})
	}
}

func answerPreflight(w http.ResponseWriter, c *config.CORS, origin, method string) {
	h := w.Header()
	h.Add("Vary", "Origin, Access-Control-Request-Method, Access-Control-Request-Headers")
	if c != nil && c.AllowsOrigin(origin) && c.AllowsMethod(method) {
		h.Set(allowOrigin, originValue(c, origin))
		h.Set(allowMethods, c.Methods)
		if c.Headers != "" {
			h.Set(allowHeaders, c.Headers)
		}
		if c.Credentials {
			h.Set(allowCredentials, "true")
		}
		h.Set(maxAge, c.MaxAge)
	}
	w.WriteHeader(http.StatusNoContent)
}

// originValue is what Access-Control-Allow-Origin says: "*" for any origin
// (only ever without credentials), else the caller's own origin.
func originValue(c *config.CORS, origin string) string {
	if c.AnyOrigin {
		return "*"
	}
	return origin
}

// corsWriter sets the CORS headers when the answer starts, after the
// backend's headers are in, so the gateway's win.
type corsWriter struct {
	http.ResponseWriter
	cors    *config.CORS
	origin  string
	started bool
}

func (cw *corsWriter) setHeaders() {
	if cw.started {
		return
	}
	cw.started = true
	h := cw.ResponseWriter.Header()
	h.Del(allowOrigin)
	h.Del(allowCredentials)
	h.Add("Vary", "Origin")
	if !cw.cors.AllowsOrigin(cw.origin) {
		return
	}
	h.Set(allowOrigin, originValue(cw.cors, cw.origin))
	if cw.cors.Credentials {
		h.Set(allowCredentials, "true")
	}
}

func (cw *corsWriter) WriteHeader(code int) {
	if code >= 200 { // not for 1xx (100 Continue): the real answer comes later
		cw.setHeaders()
	}
	cw.ResponseWriter.WriteHeader(code)
}

func (cw *corsWriter) Write(b []byte) (int, error) {
	cw.setHeaders()
	return cw.ResponseWriter.Write(b)
}

// Flush passes flushes through, so streamed answers still stream.
func (cw *corsWriter) Flush() {
	cw.setHeaders()
	_ = http.NewResponseController(cw.ResponseWriter).Flush()
}

// Unwrap lets http.ResponseController reach the original writer.
func (cw *corsWriter) Unwrap() http.ResponseWriter {
	return cw.ResponseWriter
}
