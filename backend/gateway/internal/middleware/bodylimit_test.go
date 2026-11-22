package middleware

import (
	"bytes"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/proxy"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/tenant"
)

// limitedGateway is tenant → router → BodyLimit → a backend that reads the
// whole body (or answers 500 if reading it failed).
func limitedGateway(t *testing.T, limit int64) (http.Handler, *int) {
	t.Helper()
	snap := snapshotOf(&config.Workspace{Version: 1, Routes: []config.Route{{
		ID: "r", PathPrefix: "/v1", Methods: []string{"POST"}, MaxBodyBytes: limit,
	}}})
	calls := new(int)
	backend := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		*calls++
		if _, err := io.Copy(io.Discard, r.Body); err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		_, _ = io.WriteString(w, "ok")
	})
	h := tenant.Middleware(staticSource{snap}, "localhost")(proxy.NewRouter(BodyLimit(slog.New(slog.DiscardHandler))(backend)))
	return h, calls
}

func post(h http.Handler, body io.Reader, length int64) (*httptest.ResponseRecorder, *reqctx.Info) {
	req := httptest.NewRequest(http.MethodPost, "/v1/upload", body)
	req.ContentLength = length // -1: not announced (chunked)
	info := &reqctx.Info{}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req.WithContext(reqctx.With(req.Context(), info)))
	return rec, info
}

func TestAnAnnouncedBodyOverTheLimitIs413AtOnce(t *testing.T) {
	gw, calls := limitedGateway(t, 1024)
	body := &countingReader{r: bytes.NewReader(make([]byte, 2048))}

	rec, info := post(gw, body, 2048)

	if rec.Code != http.StatusRequestEntityTooLarge || rec.Body.String() != `{"error":"request_too_large"}`+"\n" {
		t.Fatalf("got %d %q, want 413 request_too_large", rec.Code, rec.Body.String())
	}
	if *calls != 0 || body.read != 0 {
		t.Errorf("backend called %d times, %d body bytes read; want none", *calls, body.read)
	}
	if info.BodyLimit.Load() != 1024 {
		t.Errorf("log body_limit = %d, want 1024", info.BodyLimit.Load())
	}
}

func TestBodiesWithinTheLimitPass(t *testing.T) {
	gw, calls := limitedGateway(t, 1024)
	for _, tc := range []struct {
		size, length int64
	}{
		{1024, 1024}, // exactly the limit, announced
		{1024, -1},   // exactly the limit, chunked
		{10, 10},
		{0, 0},
	} {
		rec, info := post(gw, bytes.NewReader(make([]byte, tc.size)), tc.length)
		if rec.Code != 200 || info.BodyLimit.Load() != 0 {
			t.Errorf("%d bytes (length %d): status %d, body_limit %d; want 200", tc.size, tc.length, rec.Code, info.BodyLimit.Load())
		}
	}
	if *calls != 4 {
		t.Errorf("backend called %d times, want 4", *calls)
	}
}

// Without a length the body is cut at the limit while it is read: the
// reader fails, and the request is marked for a 413 (the forwarder answers
// it; here the test backend just sees the failed read).
func TestAnUnannouncedBodyIsCutAtTheLimit(t *testing.T) {
	gw, _ := limitedGateway(t, 1024)

	rec, info := post(gw, bytes.NewReader(make([]byte, 4096)), -1)

	if rec.Code != http.StatusInternalServerError {
		t.Errorf("status %d, want the backend's failed read (500)", rec.Code)
	}
	if info.BodyLimit.Load() != 1024 {
		t.Errorf("body_limit = %d, want 1024", info.BodyLimit.Load())
	}
}

func TestNoLimitMeansNoLimit(t *testing.T) {
	gw, _ := limitedGateway(t, 0) // a control plane before MT.11
	if rec, _ := post(gw, bytes.NewReader(make([]byte, 1<<20)), 1<<20); rec.Code != 200 {
		t.Errorf("status %d, want 200", rec.Code)
	}
}

func TestBodyLimitWithoutARouteIs500(t *testing.T) {
	var logs jsonLogger
	h := BodyLimit(logs.logger())(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		t.Error("reached next without a route")
	}))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("POST", "/x", strings.NewReader("x")))
	if rec.Code != http.StatusInternalServerError || !strings.Contains(logs.buf.String(), "without a matched route") {
		t.Errorf("status %d, log %s", rec.Code, logs.buf.String())
	}
}

func TestLoggerShowsTheBodyLimitWhenHit(t *testing.T) {
	var logs jsonLogger
	h := Logger(logs.logger())(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reqctx.From(r.Context()).BodyLimit.Store(1024)
		w.WriteHeader(http.StatusRequestEntityTooLarge)
	}))
	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("POST", "/x", nil))
	if got := logs.lines(t)[0]["body_limit"]; got != 1024.0 {
		t.Errorf("body_limit = %v, want 1024", got)
	}

	logs.buf.Reset()
	Logger(logs.logger())(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {})).
		ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/x", nil))
	if _, ok := logs.lines(t)[0]["body_limit"]; ok {
		t.Error("body_limit logged for a request that wasn't refused")
	}
}

// countingReader counts the bytes read from it.
type countingReader struct {
	r    io.Reader
	read int
}

func (c *countingReader) Read(p []byte) (int, error) {
	n, err := c.r.Read(p)
	c.read += n
	return n, err
}
