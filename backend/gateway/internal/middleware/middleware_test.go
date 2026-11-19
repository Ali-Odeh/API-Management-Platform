package middleware

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
)

// jsonLogger collects log lines; lines() decodes them.
type jsonLogger struct{ buf strings.Builder }

func (l *jsonLogger) logger() *slog.Logger {
	return slog.New(slog.NewJSONHandler(&l.buf, nil))
}

func (l *jsonLogger) lines(t *testing.T) []map[string]any {
	t.Helper()
	var out []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(l.buf.String()), "\n") {
		if line == "" {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Fatalf("log line is not JSON: %q", line)
		}
		out = append(out, m)
	}
	return out
}

// ---- Chain ----

func TestChainOrder(t *testing.T) {
	var order []string
	mark := func(name string) Middleware {
		return func(next http.Handler) http.Handler {
			return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				order = append(order, name+" in")
				next.ServeHTTP(w, r)
				order = append(order, name+" out")
			})
		}
	}
	h := Chain(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		order = append(order, "handler")
	}), mark("A"), mark("B"))

	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/", nil))

	if got := strings.Join(order, ", "); got != "A in, B in, handler, B out, A out" {
		t.Errorf("order = %s", got)
	}
}

// ---- RequestID ----

func TestRequestID(t *testing.T) {
	tests := []struct {
		name     string
		sent     string
		wantKept bool
	}{
		{name: "none sent", sent: ""},
		{name: "safe ID kept", sent: "abc-123_X.y", wantKept: true},
		{name: "longest allowed kept", sent: strings.Repeat("a", 128), wantKept: true},
		{name: "too long replaced", sent: strings.Repeat("a", 129)},
		{name: "unsafe characters replaced", sent: "<script>"},
		{name: "spaces replaced", sent: "a b"},
		{name: "newline replaced (log injection)", sent: "a\nb"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var seenHeader, seenCtx string
			h := RequestID(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
				seenHeader = r.Header.Get(RequestIDHeader) // what the proxy forwards
				seenCtx = reqctx.From(r.Context()).RequestID
			}))
			req := httptest.NewRequest("GET", "/", nil)
			if tt.sent != "" {
				req.Header.Set(RequestIDHeader, tt.sent)
			}
			rec := httptest.NewRecorder()

			h.ServeHTTP(rec, req)

			id := rec.Header().Get(RequestIDHeader)
			if tt.wantKept && id != tt.sent {
				t.Errorf("ID = %q, want the client's %q kept", id, tt.sent)
			}
			if !tt.wantKept && (id == tt.sent || len(id) != 26 || !validRequestID(id)) {
				t.Errorf("ID = %q, want a new 26-character ID", id)
			}
			if seenHeader != id || seenCtx != id {
				t.Errorf("request header %q, context %q, response %q: want all the same", seenHeader, seenCtx, id)
			}
		})
	}
}

func TestGeneratedRequestIDsAreUnique(t *testing.T) {
	seen := map[string]bool{}
	h := RequestID(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	for range 1000 {
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, httptest.NewRequest("GET", "/", nil))
		id := rec.Header().Get(RequestIDHeader)
		if seen[id] {
			t.Fatalf("ID %q generated twice", id)
		}
		seen[id] = true
	}
}

// ---- Logger ----

func TestLoggerWritesOneLinePerRequest(t *testing.T) {
	var logs jsonLogger
	h := Chain(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		info := reqctx.From(r.Context())
		info.RouteID = "r1" // what the router and forwarder report
		info.Upstream = 30 * time.Millisecond
		time.Sleep(40 * time.Millisecond)
		w.WriteHeader(http.StatusCreated)
		_, _ = io.WriteString(w, "hello")
	}), RequestID, Logger(logs.logger()))

	req := httptest.NewRequest("POST", "/v1/orders?token=secret", nil)
	req.Header.Set(RequestIDHeader, "abc-123")
	req.Header.Set("X-API-Key", "ak_live_supersecret")
	req.RemoteAddr = "203.0.113.7:5000"
	h.ServeHTTP(httptest.NewRecorder(), req)

	lines := logs.lines(t)
	if len(lines) != 1 {
		t.Fatalf("got %d log lines, want 1: %v", len(lines), lines)
	}
	l := lines[0]
	want := map[string]any{
		"level": "INFO", "msg": "request", "request_id": "abc-123", "method": "POST",
		"path": "/v1/orders", "route_id": "r1", "consumer_id": "", "status": 201.0,
		"upstream_ms": 30.0, "client_ip": "203.0.113.7", "bytes": 5.0,
	}
	for k, v := range want {
		if l[k] != v {
			t.Errorf("%s = %v, want %v", k, l[k], v)
		}
	}
	latency, gateway := l["latency_ms"].(float64), l["gateway_ms"].(float64)
	if latency < 40 || gateway < 10 || gateway > latency-29.9 {
		t.Errorf("latency_ms %v, gateway_ms %v: want latency >= 40 and gateway = latency - 30", latency, gateway)
	}
	if out := logs.buf.String(); strings.Contains(out, "secret") || strings.Contains(out, "token=") {
		t.Errorf("query or API key leaked into the log: %s", out)
	}
}

func TestLoggerRecordsTheStatus(t *testing.T) {
	tests := []struct {
		name    string
		handler func(http.ResponseWriter)
		want    float64
	}{
		{"nothing written means 200", func(http.ResponseWriter) {}, 200},
		{"body only means 200", func(w http.ResponseWriter) { _, _ = io.WriteString(w, "x") }, 200},
		{"explicit 404", func(w http.ResponseWriter) { w.WriteHeader(404) }, 404},
		{"client gone (499)", func(w http.ResponseWriter) { w.WriteHeader(499) }, 499},
		{"first status wins", func(w http.ResponseWriter) { w.WriteHeader(504); w.WriteHeader(200) }, 504},
		{"1xx is not the final status", func(w http.ResponseWriter) { w.WriteHeader(103); w.WriteHeader(202) }, 202},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var logs jsonLogger
			h := Logger(logs.logger())(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { tt.handler(w) }))

			h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/x", nil))

			if got := logs.lines(t)[0]["status"]; got != tt.want {
				t.Errorf("status = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestLoggerWithoutRequestIDStillWorks(t *testing.T) {
	var logs jsonLogger
	var sawInfo bool
	h := Logger(logs.logger())(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		sawInfo = reqctx.Has(r.Context())
	}))

	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/x", nil))

	if !sawInfo {
		t.Error("Logger did not give the handler an Info to fill in")
	}
	if len(logs.lines(t)) != 1 {
		t.Error("no log line")
	}
}

// The reverse proxy aborts a broken answer with ErrAbortHandler: the line is
// still written, marked aborted, and the panic goes on to net/http.
func TestLoggerLogsAbortedAnswers(t *testing.T) {
	var logs jsonLogger
	h := Logger(logs.logger())(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, "half an ans")
		panic(http.ErrAbortHandler)
	}))

	defer func() {
		if p := recover(); p != http.ErrAbortHandler {
			t.Errorf("panic = %v, want ErrAbortHandler passed on", p)
		}
		l := logs.lines(t)[0]
		if l["aborted"] != true || l["status"] != 200.0 {
			t.Errorf("log line = %v, want aborted with status 200", l)
		}
	}()
	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/x", nil))
}

func TestStatusWriterKeepsFlushAndUnwrap(t *testing.T) {
	rec := httptest.NewRecorder()
	sw := &statusWriter{ResponseWriter: rec}

	if err := http.NewResponseController(sw).Flush(); err != nil {
		t.Fatalf("Flush through the wrapper failed: %v", err)
	}
	if !rec.Flushed {
		t.Error("the flush did not reach the real writer")
	}
	sw.Flush()
	if sw.Unwrap() != rec {
		t.Error("Unwrap does not return the real writer")
	}
}

func TestClientIP(t *testing.T) {
	r := httptest.NewRequest("GET", "/", nil)
	r.RemoteAddr = "[2001:db8::1]:443"
	if got := clientIP(r); got != "2001:db8::1" {
		t.Errorf("IPv6: %q", got)
	}
	r.RemoteAddr = "no-port"
	if got := clientIP(r); got != "no-port" {
		t.Errorf("without port: %q", got)
	}
}

func TestMillis(t *testing.T) {
	if got := millis(1234567 * time.Nanosecond); got != 1.235 {
		t.Errorf("millis(1.234567ms) = %v, want 1.235", got)
	}
}

// ---- Recover ----

func TestRecoverTurnsAPanicInto500(t *testing.T) {
	var logs jsonLogger
	h := Chain(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		var m map[string]int
		m["boom"] = 1 // a real bug: writing to a nil map panics
	}), RequestID, Logger(logs.logger()), Recover(logs.logger()))
	req := httptest.NewRequest("GET", "/v1/x", nil)
	req.Header.Set(RequestIDHeader, "req-42")
	rec := httptest.NewRecorder()

	h.ServeHTTP(rec, req) // must not panic

	if rec.Code != 500 || rec.Body.String() != `{"error":"internal_error"}`+"\n" {
		t.Errorf("client got %d %q, want 500 internal_error", rec.Code, rec.Body.String())
	}
	lines := logs.lines(t)
	if len(lines) != 2 {
		t.Fatalf("got %d log lines, want the panic and the request: %v", len(lines), lines)
	}
	p, line := lines[0], lines[1]
	if p["level"] != "ERROR" || p["request_id"] != "req-42" || !strings.Contains(p["panic"].(string), "nil map") ||
		!strings.Contains(p["stack"].(string), "middleware_test.go") {
		t.Errorf("panic log = %v", p)
	}
	if line["msg"] != "request" || line["status"] != 500.0 {
		t.Errorf("request log = %v, want status 500", line)
	}
}

func TestRecoverAfterTheAnswerStartedCutsTheConnection(t *testing.T) {
	var logs jsonLogger
	h := Recover(logs.logger())(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(200)
		_, _ = io.WriteString(w, "partial")
		panic("bug after writing")
	}))
	rec := httptest.NewRecorder()

	defer func() {
		if p := recover(); p != http.ErrAbortHandler {
			t.Errorf("panic = %v, want ErrAbortHandler to cut the connection", p)
		}
		if rec.Body.String() != "partial" {
			t.Errorf("body = %q, want no 500 appended", rec.Body.String())
		}
		if !strings.Contains(logs.buf.String(), "bug after writing") {
			t.Error("the panic was not logged")
		}
	}()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "/x", nil))
}

func TestRecoverLetsTheProxyAbortThrough(t *testing.T) {
	var logs jsonLogger
	h := Recover(logs.logger())(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		panic(http.ErrAbortHandler)
	}))

	defer func() {
		if p := recover(); p != http.ErrAbortHandler {
			t.Errorf("panic = %v, want ErrAbortHandler untouched", p)
		}
		if logs.buf.Len() != 0 {
			t.Errorf("a deliberate abort was logged as a bug: %s", logs.buf.String())
		}
	}()
	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/x", nil))
}

func TestRecoverWithoutPanicChangesNothing(t *testing.T) {
	var logs jsonLogger
	h := Recover(logs.logger())(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusTeapot)
	}))
	rec := httptest.NewRecorder()

	h.ServeHTTP(rec, httptest.NewRequest("GET", "/x", nil))

	if rec.Code != http.StatusTeapot || logs.buf.Len() != 0 {
		t.Errorf("status %d, logs %q", rec.Code, logs.buf.String())
	}
}
