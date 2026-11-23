package middleware

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"testing"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/proxy"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/ratelimit"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/tenant"
)

// V2.2: plans' quotas, per-IP limits and wrong-key refusals in the chain.

// recordingLimiter remembers the last request it was asked about.
type recordingLimiter struct {
	res  ratelimit.Result
	last ratelimit.Request
}

func (f *recordingLimiter) Allow(_ context.Context, req ratelimit.Request) (ratelimit.Result, error) {
	f.last = req
	return f.res, nil
}

// fakeFailures answers every counted failure the same way.
type fakeFailures struct {
	res    ratelimit.FailureResult
	err    error
	counts []ratelimit.Failure
}

func (f *fakeFailures) CountFailure(_ context.Context, fail ratelimit.Failure) (ratelimit.FailureResult, error) {
	f.counts = append(f.counts, fail)
	return f.res, f.err
}

func protectedGateway(limiter Allower, failures FailureCounter) http.Handler {
	log := slog.New(slog.DiscardHandler)
	snap := snapshotOf(&config.Workspace{
		Version: 1,
		Limits:  config.Limits{RateLimitPerMin: 6000, MaxConcurrent: 200, IPRateLimitPerMin: 30, AuthFailLimit: 5},
		Routes: []config.Route{
			{ID: "private", PathPrefix: "/private", Methods: []string{"GET"}, AuthRequired: true},
			{ID: "public", PathPrefix: "/public", Methods: []string{"GET"}, AuthRequired: false},
		},
		Keys: map[string]config.Key{
			hashAPIKey(activeKey): {ID: "k1", ConsumerID: "c1", ConsumerStatus: config.ConsumerActive,
				PlanID: "p1", RateLimitPerMin: 42, MonthlyQuota: 1000},
		},
	})
	backend := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	chain := Auth(log, failures)(RateLimit(limiter, log)(backend))
	return tenant.Middleware(staticSource{snap}, "localhost")(proxy.NewRouter(chain))
}

func TestTheLimiterGetsThePlansQuotaAndTheIPLimits(t *testing.T) {
	limiter := &recordingLimiter{res: ratelimit.Result{Allowed: true, Limit: 42, Remaining: 41, QuotaLimit: 1000, QuotaRemaining: 999}}
	gw := protectedGateway(limiter, nil)

	rec, _ := send(gw, "/private/x", activeKey)

	got := limiter.last
	if got.ConsumerLimit != 42 || got.MonthlyQuota != 1000 || got.ClientIP != "192.0.2.1" ||
		got.IPLimit != 30 || got.AuthFailLimit != 5 {
		t.Errorf("limiter asked %+v", got)
	}
	if rec.Header().Get(QuotaLimitHeader) != "1000" || rec.Header().Get(QuotaRemainingHeader) != "999" {
		t.Errorf("quota headers %v", rec.Header())
	}
}

func TestEachRefusalHasItsCodeAndLogValue(t *testing.T) {
	tests := []struct {
		by       ratelimit.Check
		code     string
		log      string
		bucketHd bool // X-RateLimit-* describe the refusing bucket
	}{
		{ratelimit.BucketIP, "rate_limit_exceeded", RateLimitLimitedIP, true},
		{ratelimit.Quota, "quota_exceeded", RateLimitLimitedQuota, false},
		{ratelimit.AuthFail, "too_many_failed_attempts", RateLimitBlockedIP, false},
	}
	for _, tc := range tests {
		limiter := &recordingLimiter{res: ratelimit.Result{LimitedBy: tc.by, Limit: 30, RetryAfter: 90 * time.Minute, QuotaLimit: 1000}}
		rec, info := send(protectedGateway(limiter, nil), "/private/x", activeKey)

		if rec.Code != http.StatusTooManyRequests || rec.Body.String() != `{"error":"`+tc.code+`"}`+"\n" {
			t.Errorf("%s: %d %q", tc.by, rec.Code, rec.Body.String())
		}
		if info.RateLimit != tc.log || rec.Header().Get("Retry-After") != "5400" {
			t.Errorf("%s: log %q, Retry-After %q", tc.by, info.RateLimit, rec.Header().Get("Retry-After"))
		}
		if (rec.Header().Get(LimitHeader) != "") != tc.bucketHd {
			t.Errorf("%s: X-RateLimit-Limit %q", tc.by, rec.Header().Get(LimitHeader))
		}
	}
}

func TestAWrongKeyIsCountedAndPastTheLimitRefusedWith429(t *testing.T) {
	limiter := &recordingLimiter{res: ratelimit.Result{Allowed: true}}
	failures := &fakeFailures{}
	gw := protectedGateway(limiter, failures)

	rec, _ := send(gw, "/private/x", "ak_live_wrong")
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("under the limit: %d, want 401", rec.Code)
	}
	if len(failures.counts) != 1 || failures.counts[0] != (ratelimit.Failure{WorkspaceID: "ws-default", ClientIP: "192.0.2.1", Limit: 5}) {
		t.Errorf("counted %+v", failures.counts)
	}

	failures.res = ratelimit.FailureResult{Blocked: true, RetryAfter: 61 * time.Second}
	rec, info := send(gw, "/private/x", "ak_live_wrong")
	if rec.Code != http.StatusTooManyRequests || rec.Body.String() != `{"error":"too_many_failed_attempts"}`+"\n" ||
		rec.Header().Get("Retry-After") != "61" || info.RateLimit != RateLimitBlockedIP {
		t.Errorf("past the limit: %d %q %v %q", rec.Code, rec.Body.String(), rec.Header(), info.RateLimit)
	}
}

func TestOnlyWrongKeysAreCountedAndRedisDownStays401(t *testing.T) {
	failures := &fakeFailures{err: errors.New("redis down")}
	gw := protectedGateway(&recordingLimiter{res: ratelimit.Result{Allowed: true}}, failures)

	if rec, _ := send(gw, "/private/x", "ak_live_wrong"); rec.Code != http.StatusUnauthorized {
		t.Errorf("Redis down: %d, want 401 (fail open)", rec.Code)
	}
	failures.counts = nil
	send(gw, "/private/x", "")        // no key: not an attempt at guessing
	send(gw, "/private/x", activeKey) // a good key
	if len(failures.counts) != 0 {
		t.Errorf("counted %+v", failures.counts)
	}
}
