package middleware

import (
	"context"
	"log/slog"
	"math"
	"net/http"
	"strconv"
	"sync/atomic"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/ratelimit"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/respond"
)

// Values of the request log's "ratelimit" field.
const (
	RateLimitOK               = "ok"
	RateLimitLimited          = "limited"           // refused by the consumer's bucket
	RateLimitLimitedWorkspace = "limited_workspace" // refused by the workspace's bucket
	RateLimitUnavailable      = "unavailable"       // Redis did not answer; the request went through
	RateLimitLimitedIP        = "limited_ip"        // refused by the client IP's bucket (public route, V2.2)
	RateLimitLimitedQuota     = "limited_quota"     // the plan's monthly quota is used up (V2.2)
	RateLimitBlockedIP        = "blocked_ip"        // too many wrong keys from this IP (V2.2)
)

// Rate-limit headers (the X- names GitHub and Kong use).
const (
	LimitHeader     = "X-RateLimit-Limit"
	RemainingHeader = "X-RateLimit-Remaining"
	// The consumer's monthly quota and what is left of it (V2.2).
	QuotaLimitHeader     = "X-Quota-Limit"
	QuotaRemainingHeader = "X-Quota-Remaining"
)

// refusals: for each check that can refuse, the log value and the error code.
var refusals = map[ratelimit.Check]struct{ log, code string }{
	ratelimit.BucketWorkspace: {RateLimitLimitedWorkspace, "rate_limit_exceeded"},
	ratelimit.BucketConsumer:  {RateLimitLimited, "rate_limit_exceeded"},
	ratelimit.BucketIP:        {RateLimitLimitedIP, "rate_limit_exceeded"},
	ratelimit.Quota:           {RateLimitLimitedQuota, "quota_exceeded"},
	ratelimit.AuthFail:        {RateLimitBlockedIP, "too_many_failed_attempts"},
}

// At most one "Redis unavailable" warning per this interval: an outage at
// 1,000 requests/s must not write 1,000 log lines per second.
const unavailableWarnEvery = 10 * time.Second

// Allower is what the rate-limit layer needs. *ratelimit.Limiter fits it;
// tests pass a fake, so they need no Redis.
type Allower interface {
	Allow(ctx context.Context, req ratelimit.Request) (ratelimit.Result, error)
}

// RateLimit runs after Auth and checks every limit of the request in one
// Redis call, spending nothing unless all pass:
//
//	all pass               → on to the backend, with X-RateLimit-Limit / -Remaining
//	                         (and X-Quota-Limit / -Remaining with a quota)
//	a bucket is empty      → 429 rate_limit_exceeded, with Retry-After (seconds)
//	the quota is used up   → 429 quota_exceeded, Retry-After until the month ends
//	the IP sent too many
//	wrong keys             → 429 too_many_failed_attempts, until its window ends
//	Redis down             → on to the backend anyway (fail open), warning in the log
//
// Public routes have no consumer: the workspace's bucket and the client IP's
// limit them. Protected routes: the workspace's and the consumer's (its
// plan's limit), its monthly quota, and the IP's failed-key refusal.
// Requests refused by Auth (401/403) never get here, so they cost no budget.
// The client gets the same 429 whichever bucket refused; the log tells them
// apart (limited / limited_workspace).
func RateLimit(limiter Allower, log *slog.Logger) Middleware {
	warner := &throttledWarner{log: log, every: unavailableWarnEvery, now: time.Now}

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			info := reqctx.From(r.Context())
			ws := info.Workspace
			if ws == nil {
				// Only possible if main wires the rate limit without tenant before it.
				log.Error("rate limit called without a workspace", "path", r.URL.Path)
				respond.Error(w, http.StatusInternalServerError, "internal_error")
				return
			}
			req := ratelimit.Request{
				WorkspaceID:    ws.ID,
				WorkspaceLimit: ws.Limits.RateLimitPerMin,
				ClientIP:       clientIP(r),
				IPLimit:        ws.Limits.IPRateLimitPerMin,
				AuthFailLimit:  ws.Limits.AuthFailLimit,
			}
			if key, ok := consumerFrom(r.Context()); ok {
				req.ConsumerID, req.ConsumerLimit = key.ConsumerID, key.RateLimitPerMin
				req.MonthlyQuota = key.MonthlyQuota
			}

			res, err := limiter.Allow(r.Context(), req)
			if err != nil {
				// A broken limiter must not take the whole API down.
				info.RateLimit = RateLimitUnavailable
				warner.warn(err)
				next.ServeHTTP(w, r)
				return
			}

			h := w.Header()
			if res.QuotaLimit > 0 {
				h.Set(QuotaLimitHeader, strconv.FormatInt(res.QuotaLimit, 10))
				h.Set(QuotaRemainingHeader, strconv.FormatInt(res.QuotaRemaining, 10))
			}
			if !res.Allowed {
				refusal := refusals[res.LimitedBy]
				info.RateLimit = refusal.log
				if res.LimitedBy != ratelimit.Quota && res.LimitedBy != ratelimit.AuthFail {
					h.Set(LimitHeader, strconv.Itoa(res.Limit))
					h.Set(RemainingHeader, strconv.Itoa(res.Remaining))
				}
				h.Set("Retry-After", strconv.Itoa(retryAfterSeconds(res.RetryAfter)))
				respond.Error(w, http.StatusTooManyRequests, refusal.code)
				return
			}
			h.Set(LimitHeader, strconv.Itoa(res.Limit))
			h.Set(RemainingHeader, strconv.Itoa(res.Remaining))
			info.RateLimit = RateLimitOK
			next.ServeHTTP(w, r)
		})
	}
}

// retryAfterSeconds turns the wait into whole seconds for Retry-After,
// rounded up (retrying early would only be refused again), at least 1.
func retryAfterSeconds(d time.Duration) int {
	return max(1, int(math.Ceil(d.Seconds())))
}

// throttledWarner logs a warning at most once per interval and counts the
// ones it held back. Many request goroutines call it at once, so it uses
// atomics instead of a lock.
type throttledWarner struct {
	log     *slog.Logger
	every   time.Duration
	now     func() time.Time // replaced in tests
	last    atomic.Int64     // time of the last warning, Unix nanoseconds
	skipped atomic.Int64     // warnings held back since then
}

func (t *throttledWarner) warn(err error) {
	now := t.now().UnixNano()
	last := t.last.Load()
	// Too soon, or another goroutine is logging this round: just count it.
	if now-last < int64(t.every) || !t.last.CompareAndSwap(last, now) {
		t.skipped.Add(1)
		return
	}
	t.log.Warn("rate limiter unavailable, letting requests through (fail open)",
		"error", err.Error(), "skipped_warnings", t.skipped.Swap(0))
}
