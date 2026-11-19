package proxy

import (
	"context"
	"errors"
	"log/slog"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/respond"
)

// Connection pool to the backends. Every phase of a connection has a limit,
// so a broken backend can never hold a request forever.
const (
	dialTimeout         = 5 * time.Second  // the backend must accept the connection in time
	tlsHandshakeTimeout = 5 * time.Second  // and finish the HTTPS handshake in time
	maxIdleConns        = 1000             // connections kept ready, all backends together
	maxIdleConnsPerHost = 100              // kept ready per backend (Go's default is only 2)
	idleConnTimeout     = 90 * time.Second // an unused connection is closed after this
)

// StatusClientClosedRequest marks a request whose client hung up before the
// answer was ready. Nobody receives it; it is there for the request log
// (step 3.5). The number is nginx's convention, not an official HTTP status.
const StatusClientClosedRequest = 499

// Forwarder sends a matched request to its route's backend and copies the
// answer back to the client. One Forwarder (and one connection pool) serves
// every route; the target is picked per request from the route.
type Forwarder struct {
	proxy *httputil.ReverseProxy
	log   *slog.Logger
}

// NewForwarder builds the forwarder with its connection pool.
func NewForwarder(log *slog.Logger) *Forwarder {
	f := &Forwarder{log: log}
	f.proxy = &httputil.ReverseProxy{
		Rewrite:      rewrite,
		Transport:    timedTransport{next: newTransport()},
		ErrorHandler: f.handleError,
	}
	return f
}

// timedTransport measures how long the backend takes to start answering
// (until its response headers arrive) and notes it for the request log as
// upstream_ms. The proxy calls RoundTrip in the request's own goroutine.
type timedTransport struct {
	next http.RoundTripper
}

func (t timedTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	start := time.Now()
	resp, err := t.next.RoundTrip(req)
	reqctx.From(req.Context()).Upstream = time.Since(start)
	return resp, err
}

func newTransport() *http.Transport {
	dialer := &net.Dialer{Timeout: dialTimeout, KeepAlive: 30 * time.Second}
	return &http.Transport{
		// Proxy is left nil on purpose: backend calls never go through an
		// HTTP_PROXY from the environment.
		DialContext:           dialer.DialContext,
		TLSHandshakeTimeout:   tlsHandshakeTimeout,
		MaxIdleConns:          maxIdleConns,
		MaxIdleConnsPerHost:   maxIdleConnsPerHost,
		IdleConnTimeout:       idleConnTimeout,
		ExpectContinueTimeout: time.Second,
		ForceAttemptHTTP2:     true,
	}
}

// ServeHTTP forwards the request within the route's timeout.
func (f *Forwarder) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	m, ok := FromContext(r.Context())
	if !ok {
		// Only possible if the wiring in main is wrong: the router always
		// runs first.
		f.log.Error("forwarder called without a matched route", "path", r.URL.Path)
		respond.Error(w, http.StatusInternalServerError, "internal_error")
		return
	}

	// The timeout covers the whole answer, start to last byte. The context
	// also ends the backend call at once if the client hangs up.
	ctx, cancel := context.WithTimeout(r.Context(), m.Route.Timeout)
	defer cancel()
	f.proxy.ServeHTTP(w, r.WithContext(ctx))
}

// rewrite builds the backend request from the client's request. Before it
// runs, ReverseProxy has already removed hop-by-hop headers (Connection,
// Keep-Alive, ...) and any Forwarded / X-Forwarded-* header the client sent,
// because a client could fake them.
func rewrite(pr *httputil.ProxyRequest) {
	m, _ := FromContext(pr.In.Context()) // ServeHTTP checked it already
	if m.Route.StripPrefix {
		stripPrefix(pr.Out.URL, m.Route.PathPrefix)
	}
	// Scheme and host from the upstream, its base path joined in front
	// ("http://svc/api" + "/7" -> "/api/7"), query kept. The Host header
	// becomes the backend's own.
	pr.SetURL(m.Route.Upstream)
	// Fresh X-Forwarded-For (the real client IP), -Host and -Proto.
	pr.SetXForwarded()
}

// stripPrefix removes a route prefix from a request path:
// "/v1/orders/7" -> "/7", and the bare prefix "/v1/orders" -> "/".
//
// Both forms of the path are cut: Path (decoded) and RawPath (the encoded
// form, set only when it differs, e.g. "a%2Fb"), so an encoded "/" inside a
// segment reaches the backend unchanged. The router guarantees that Path
// starts with prefix on a segment boundary.
func stripPrefix(u *url.URL, prefix string) {
	if prefix == "/" {
		return
	}
	u.Path = orRoot(strings.TrimPrefix(u.Path, prefix))
	if u.RawPath == "" {
		return
	}
	if raw, ok := strings.CutPrefix(u.RawPath, prefix); ok {
		u.RawPath = orRoot(raw)
	} else {
		// The client encoded the prefix itself (e.g. "%6Frders"): fall back
		// to the decoded path, which is still correct, only re-encoded.
		u.RawPath = ""
	}
}

func orRoot(p string) string {
	if p == "" {
		return "/"
	}
	return p
}

// handleError answers when the backend call fails before an answer started.
func (f *Forwarder) handleError(w http.ResponseWriter, r *http.Request, err error) {
	status, code := classify(r.Context().Err(), err)
	m, _ := FromContext(r.Context())

	if status == StatusClientClosedRequest {
		// Nobody is listening any more; record the status for the log.
		w.WriteHeader(status)
		return
	}
	f.log.Warn("backend call failed",
		"route_id", m.Route.ID, "upstream", m.Route.Upstream.Host,
		"status", status, "error", err.Error())
	respond.Error(w, status, code)
}

// classify decides who caused a failed backend call. ctxErr is the request
// context's error: our route timeout (DeadlineExceeded) or the client
// leaving (Canceled).
func classify(ctxErr, err error) (status int, code string) {
	var netErr net.Error
	switch {
	case errors.Is(ctxErr, context.DeadlineExceeded):
		return http.StatusGatewayTimeout, "gateway_timeout"
	case errors.Is(ctxErr, context.Canceled):
		return StatusClientClosedRequest, "client_closed_request"
	case errors.As(err, &netErr) && netErr.Timeout():
		// The connect or TLS timeout ran out: the backend is too slow to
		// even accept us.
		return http.StatusGatewayTimeout, "gateway_timeout"
	default:
		// Refused, reset, unknown host, broken answer: the backend is
		// unreachable or broken.
		return http.StatusBadGateway, "bad_gateway"
	}
}
