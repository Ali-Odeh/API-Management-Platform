package middleware

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/proxy"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/ratelimit"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
)

// fakeLimiter answers with a fixed result or error and records what it was
// asked.
type fakeLimiter struct {
	res   ratelimit.Result
	err   error
	calls []string // "consumer/limit"
}

func (f *fakeLimiter) Allow(_ context.Context, consumerID string, limit int) (ratelimit.Result, error) {
	f.calls = append(f.calls, consumerID+"/"+strconv.Itoa(limit))
	return f.res, f.err
}

// newLimitedGateway is router → Auth → RateLimit → a backend that answers
// 200, with a private route (consumer limit 42) and a public one.
func newLimitedGateway(t *testing.T, limiter Allower, log *slog.Logger) (http.Handler, *bool) {
	t.Helper()
	snap := snapshotOf(&config.Workspace{
		Version: 1,
		Routes: []config.Route{
			{ID: "private", PathPrefix: "/private", Methods: []string{"GET"}, AuthRequired: true},
			{ID: "public", PathPrefix: "/public", Methods: []string{"GET"}, AuthRequired: false},
		},
		Keys: map[string]config.Key{
			hashAPIKey(activeKey):  {ID: "k1", ConsumerID: "c1", ConsumerStatus: config.ConsumerActive, RateLimitPerMin: 42},
			hashAPIKey(blockedKey): {ID: "k2", ConsumerID: "c2", ConsumerStatus: config.ConsumerBlocked, RateLimitPerMin: 42},
		},
	})
	called := new(bool)
	backend := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		*called = true
		w.WriteHeader(http.StatusOK)
	})
	chain := Auth(log)(RateLimit(limiter, log)(backend))
	return proxy.NewRouter(staticSource{snap}, chain), called
}

func send(h http.Handler, path, key string) (*httptest.ResponseRecorder, *reqctx.Info) {
	req := httptest.NewRequest("GET", path, nil)
	if key != "" {
		req.Header.Set(APIKeyHeader, key)
	}
	info := &reqctx.Info{}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req.WithContext(reqctx.With(req.Context(), info)))
	return rec, info
}

func TestRateLimitAllowed(t *testing.T) {
	limiter := &fakeLimiter{res: ratelimit.Result{Allowed: true, Limit: 42, Remaining: 41}}
	gw, called := newLimitedGateway(t, limiter, slog.New(slog.DiscardHandler))

	rec, info := send(gw, "/private/x", activeKey)

	if rec.Code != 200 || !*called {
		t.Fatalf("status %d, backend called %v; want 200 from the backend", rec.Code, *called)
	}
	if rec.Header().Get(LimitHeader) != "42" || rec.Header().Get(RemainingHeader) != "41" {
		t.Errorf("headers = %v", rec.Header())
	}
	if rec.Header().Get("Retry-After") != "" {
		t.Error("Retry-After set on an allowed request")
	}
	// The consumer's own limit, from the snapshot, by consumer (not key).
	if len(limiter.calls) != 1 || limiter.calls[0] != "c1/42" {
		t.Errorf("limiter asked %v, want [c1/42]", limiter.calls)
	}
	if info.RateLimit != RateLimitOK {
		t.Errorf("log field ratelimit = %q, want ok", info.RateLimit)
	}
}

func TestRateLimitExceededIs429(t *testing.T) {
	limiter := &fakeLimiter{res: ratelimit.Result{Allowed: false, Limit: 42, Remaining: 0, RetryAfter: 1200 * time.Millisecond}}
	gw, called := newLimitedGateway(t, limiter, slog.New(slog.DiscardHandler))

	rec, info := send(gw, "/private/x", activeKey)

	if rec.Code != http.StatusTooManyRequests || *called {
		t.Fatalf("status %d, backend called %v; want 429 without calling the backend", rec.Code, *called)
	}
	if rec.Body.String() != `{"error":"rate_limit_exceeded"}`+"\n" {
		t.Errorf("body = %q", rec.Body.String())
	}
	if rec.Header().Get("Retry-After") != "2" { // 1.2 s rounded up
		t.Errorf("Retry-After = %q, want 2", rec.Header().Get("Retry-After"))
	}
	if rec.Header().Get(LimitHeader) != "42" || rec.Header().Get(RemainingHeader) != "0" {
		t.Errorf("headers = %v", rec.Header())
	}
	if info.RateLimit != RateLimitLimited {
		t.Errorf("log field ratelimit = %q, want limited", info.RateLimit)
	}
}

func TestRateLimitFailsOpen(t *testing.T) {
	var logs jsonLogger
	limiter := &fakeLimiter{err: errors.New("dial tcp redis:6379: connection refused")}
	gw, called := newLimitedGateway(t, limiter, logs.logger())

	rec, info := send(gw, "/private/x", activeKey)

	if rec.Code != 200 || !*called {
		t.Fatalf("status %d, backend called %v; want the request let through", rec.Code, *called)
	}
	if rec.Header().Get(LimitHeader) != "" {
		t.Error("rate-limit headers set without an answer from Redis")
	}
	if info.RateLimit != RateLimitUnavailable {
		t.Errorf("log field ratelimit = %q, want unavailable", info.RateLimit)
	}
	if !strings.Contains(logs.buf.String(), "fail open") || !strings.Contains(logs.buf.String(), "connection refused") {
		t.Errorf("no warning with the cause: %s", logs.buf.String())
	}
}

func TestRateLimitSkipsPublicRoutesAndRefusedKeys(t *testing.T) {
	limiter := &fakeLimiter{res: ratelimit.Result{Allowed: true, Limit: 42, Remaining: 41}}
	gw, _ := newLimitedGateway(t, limiter, slog.New(slog.DiscardHandler))

	cases := []struct {
		path, key  string
		wantStatus int
	}{
		{"/public/x", "", 200},          // nobody to count
		{"/private/x", "", 401},         // no key: refused before the limiter
		{"/private/x", blockedKey, 403}, // blocked: refused before the limiter
	}
	for _, c := range cases {
		rec, info := send(gw, c.path, c.key)
		if rec.Code != c.wantStatus || rec.Header().Get(LimitHeader) != "" || info.RateLimit != "" {
			t.Errorf("%s key %q: status %d, headers %v, ratelimit %q", c.path, c.key, rec.Code, rec.Header(), info.RateLimit)
		}
	}
	if len(limiter.calls) != 0 {
		t.Errorf("limiter was asked %v; refused and public requests must cost no budget", limiter.calls)
	}
}

func TestRetryAfterSeconds(t *testing.T) {
	tests := map[time.Duration]int{
		0:                        1, // never "retry in 0 seconds"
		300 * time.Millisecond:   1,
		time.Second:              1,
		1001 * time.Millisecond:  2,
		12 * time.Second:         12,
		59900 * time.Millisecond: 60,
	}
	for d, want := range tests {
		if got := retryAfterSeconds(d); got != want {
			t.Errorf("retryAfterSeconds(%s) = %d, want %d", d, got, want)
		}
	}
}

func TestThrottledWarnerLogsOncePerInterval(t *testing.T) {
	var logs jsonLogger
	clock := time.Date(2026, 11, 19, 10, 0, 0, 0, time.UTC)
	w := &throttledWarner{log: logs.logger(), every: 10 * time.Second, now: func() time.Time { return clock }}
	boom := errors.New("redis down")

	w.warn(boom) // logged
	for range 99 {
		w.warn(boom) // held back
	}
	clock = clock.Add(10 * time.Second)
	w.warn(boom) // logged, reporting the 99 held back

	lines := logs.lines(t)
	if len(lines) != 2 {
		t.Fatalf("%d warnings, want 2: %v", len(lines), lines)
	}
	if lines[0]["skipped_warnings"] != 0.0 || lines[1]["skipped_warnings"] != 99.0 {
		t.Errorf("skipped counts = %v, %v; want 0 and 99", lines[0]["skipped_warnings"], lines[1]["skipped_warnings"])
	}
}

// Many request goroutines hit the warner at once during an outage. Under
// -race this proves it is safe, and only one of them logs.
func TestThrottledWarnerConcurrent(t *testing.T) {
	var buf safeBuffer
	w := &throttledWarner{log: slog.New(slog.NewJSONHandler(&buf, nil)), every: time.Hour, now: time.Now}

	var wg sync.WaitGroup
	for range 50 {
		wg.Go(func() { w.warn(errors.New("down")) })
	}
	wg.Wait()

	if n := strings.Count(buf.String(), "\n"); n != 1 {
		t.Errorf("%d warnings from 50 goroutines, want 1", n)
	}
	if got := w.skipped.Load(); got != 49 {
		t.Errorf("skipped = %d, want 49", got)
	}
}

// safeBuffer is a strings.Builder that many goroutines may write to.
type safeBuffer struct {
	mu sync.Mutex
	b  strings.Builder
}

func (s *safeBuffer) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.Write(p)
}

func (s *safeBuffer) String() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.String()
}

func TestLoggerShowsRateLimit(t *testing.T) {
	var logs jsonLogger
	h := Logger(logs.logger())(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		reqctx.From(r.Context()).RateLimit = RateLimitLimited
	}))

	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/x", nil))

	if got := logs.lines(t)[0]["ratelimit"]; got != "limited" {
		t.Errorf("ratelimit = %v, want limited", got)
	}
}
