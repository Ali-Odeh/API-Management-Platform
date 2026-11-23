// Package ratelimit decides whether a request may go on, with limits kept in
// Redis (PLAN.md section 7): a token bucket per workspace on every request,
// per client IP on public routes and per consumer on protected ones; the
// consumer's monthly quota (V2.2); and a refusal for client IPs that sent
// too many wrong keys (V2.2).
//
// The counters live in Redis, not in the gateway's memory, so several
// gateways share each one. The whole check is one Lua script, which Redis
// runs as a single atomic step, in one round trip.
package ratelimit

import (
	"context"
	_ "embed" // for the //go:embed lines below
	"errors"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

// The scripts are real .lua files, built into the binary at compile time.
//
//go:embed limits.lua
var limitsLua string

//go:embed failures.lua
var failuresLua string

// redis.NewScript sends only the script's SHA-1 (EVALSHA) and falls back to
// sending the whole script (EVAL) if Redis does not know it yet.
var (
	limits   = redis.NewScript(limitsLua)
	failures = redis.NewScript(failuresLua)
)

const (
	workspaceKeyPrefix = "rlws:"
	consumerKeyPrefix  = "rl:"
	ipKeyPrefix        = "rlip:"
	quotaKeyPrefix     = "quota:"
	authFailKeyPrefix  = "authfail:"
	// The check must be quick. A Redis slower than this counts as down, and
	// the caller decides what to do (the middleware lets the request through).
	DefaultTimeout = 50 * time.Millisecond
	// AuthFailWindow is how long failed key attempts from one IP are
	// counted: at the limit the IP is refused until the window ends.
	AuthFailWindow = 5 * time.Minute
)

// Check names one of a request's limits; a refusal names the one that
// held the request back.
type Check string

const (
	BucketWorkspace Check = "workspace"
	BucketIP        Check = "ip"
	BucketConsumer  Check = "consumer"
	Quota           Check = "quota"
	AuthFail        Check = "auth_fail"
)

// Request is what one request is checked against.
type Request struct {
	WorkspaceID    string
	WorkspaceLimit int    // the workspace's requests per minute
	ConsumerID     string // "" on public routes
	ConsumerLimit  int    // the consumer's (its plan's) requests per minute
	MonthlyQuota   int64  // the consumer's requests per calendar month; 0: none
	ClientIP       string // "": no per-IP checks
	IPLimit        int    // per client IP, per minute, on public routes; 0: none
	AuthFailLimit  int    // failed key attempts per IP before it is refused; 0: none
	Now            time.Time
}

// Result is the answer for one request. Limit and Remaining describe one
// bucket: when allowed, the consumer's on a protected route, and on a public
// route the one with fewer tokens left (the workspace's or the client IP's);
// when refused, the check that refused it.
type Result struct {
	Allowed    bool
	LimitedBy  Check         // "" when allowed
	Limit      int           // that check's limit (requests per minute, per month, or failures)
	Remaining  int           // what it has left after this request
	RetryAfter time.Duration // when refused: time until every check would pass; 0 when allowed
	// The consumer's monthly quota and what is left of it after this
	// request; QuotaLimit 0 means the request had no quota.
	QuotaLimit     int64
	QuotaRemaining int64
}

// Limiter checks requests against the limits in Redis.
type Limiter struct {
	rdb     redis.Scripter
	timeout time.Duration
}

// New builds a limiter on a Redis client (*redis.Client fits Scripter).
func New(rdb redis.Scripter) *Limiter {
	return &Limiter{rdb: rdb, timeout: DefaultTimeout}
}

// Allow checks every limit of the request and, only if all of them pass,
// spends one token of each bucket and one request of the quota. An error
// means Redis could not answer in time (or the request was malformed); the
// result is then empty.
func (l *Limiter) Allow(ctx context.Context, req Request) (Result, error) {
	checks, err := req.checks()
	if err != nil {
		return Result{}, err
	}
	keys := make([]string, len(checks))
	args := make([]any, 0, 2*len(checks)+1)
	for i, c := range checks {
		keys[i] = c.key
		args = append(args, string(c.kind), c.limit)
	}
	args = append(args, untilNextMonth(req.now()).Milliseconds())

	ctx, cancel := context.WithTimeout(ctx, l.timeout)
	defer cancel()
	reply, err := limits.Run(ctx, l.rdb, keys, args...).Slice()
	if err != nil {
		return Result{}, fmt.Errorf("rate limit for workspace %s: %w", req.WorkspaceID, err)
	}
	res, err := req.result(reply, checks)
	if err != nil {
		return Result{}, fmt.Errorf("rate limit for workspace %s: %w", req.WorkspaceID, err)
	}
	return res, nil
}

// Failure is one wrong key sent from a client IP to a workspace.
type Failure struct {
	WorkspaceID string
	ClientIP    string
	Limit       int // failures per AuthFailWindow before the IP is refused
}

// FailureResult says whether the IP is now over its limit.
type FailureResult struct {
	Blocked    bool          // the count went over the limit: refuse the IP
	RetryAfter time.Duration // until the window ends
}

// CountFailure counts a failed key attempt (one round trip, only for
// requests that already failed). The limits script refuses the IP while
// the count is at the limit; this answer lets the failing request itself be
// refused with 429 once the limit is passed.
func (l *Limiter) CountFailure(ctx context.Context, f Failure) (FailureResult, error) {
	if f.WorkspaceID == "" || f.ClientIP == "" || f.Limit < 1 {
		return FailureResult{}, errors.New("a failed attempt needs a workspace, a client IP and a limit")
	}
	ctx, cancel := context.WithTimeout(ctx, l.timeout)
	defer cancel()
	key := authFailKey(f.WorkspaceID, f.ClientIP)
	reply, err := failures.Run(ctx, l.rdb, []string{key}, AuthFailWindow.Milliseconds()).Int64Slice()
	if err != nil {
		return FailureResult{}, fmt.Errorf("count failed attempt in workspace %s: %w", f.WorkspaceID, err)
	}
	if len(reply) != 2 {
		return FailureResult{}, fmt.Errorf("failures script returned %d values, want 2", len(reply))
	}
	return FailureResult{
		Blocked:    reply[0] > int64(f.Limit),
		RetryAfter: time.Duration(max(reply[1], 1)) * time.Millisecond,
	}, nil
}

// check is one key of the limits script with its kind and limit.
type check struct {
	kind  Check
	key   string
	limit int64
}

// checks lists the request's limits in the script's order: the failed-key
// refusal, the workspace's bucket, the IP's or the consumer's bucket, then
// the quota.
func (r Request) checks() ([]check, error) {
	if r.WorkspaceID == "" {
		return nil, errors.New("rate limit without a workspace")
	}
	// The control plane guarantees at least 1 (CHECKs in the database); the
	// config parser drops a workspace without valid limits.
	if r.WorkspaceLimit < 1 {
		return nil, fmt.Errorf("rate limit %d for workspace %s: must be at least 1", r.WorkspaceLimit, r.WorkspaceID)
	}
	var cs []check
	if r.ConsumerID != "" && r.ClientIP != "" && r.AuthFailLimit > 0 {
		cs = append(cs, check{AuthFail, authFailKey(r.WorkspaceID, r.ClientIP), int64(r.AuthFailLimit)})
	}
	cs = append(cs, check{BucketWorkspace, workspaceKeyPrefix + r.WorkspaceID, int64(r.WorkspaceLimit)})
	if r.ConsumerID == "" {
		if r.ClientIP != "" && r.IPLimit > 0 {
			cs = append(cs, check{BucketIP, ipKeyPrefix + r.WorkspaceID + ":" + r.ClientIP, int64(r.IPLimit)})
		}
		return cs, nil
	}
	if r.ConsumerLimit < 1 {
		return nil, fmt.Errorf("rate limit %d for consumer %s: must be at least 1", r.ConsumerLimit, r.ConsumerID)
	}
	cs = append(cs, check{BucketConsumer, consumerKeyPrefix + r.ConsumerID, int64(r.ConsumerLimit)})
	if r.MonthlyQuota > 0 {
		cs = append(cs, check{Quota, quotaKey(r.ConsumerID, r.now()), r.MonthlyQuota})
	}
	return cs, nil
}

// result reads the script's { allowed, remaining, retry_after_ms, limited_by, quota_remaining }.
func (r Request) result(reply []any, checks []check) (Result, error) {
	if len(reply) != 5 {
		return Result{}, fmt.Errorf("script returned %d values, want 5", len(reply))
	}
	allowed, okA := reply[0].(int64)
	remaining, okR := reply[1].(int64)
	retryMS, okW := reply[2].(int64)
	limitedBy, okL := reply[3].(string)
	quotaLeft, okQ := reply[4].(int64)
	if !okA || !okR || !okW || !okL || !okQ {
		return Result{}, fmt.Errorf("script returned %v, want three integers, a string and an integer", reply)
	}

	res := Result{
		Allowed:    allowed == 1,
		LimitedBy:  Check(limitedBy),
		Remaining:  int(remaining),
		RetryAfter: time.Duration(retryMS) * time.Millisecond,
	}
	if res.Allowed {
		res.LimitedBy = "" // the script named the bucket it describes
	}
	found := false
	for _, c := range checks {
		if string(c.kind) == limitedBy {
			res.Limit, found = int(c.limit), true
		}
		if c.kind == Quota {
			res.QuotaLimit, res.QuotaRemaining = c.limit, quotaLeft
		}
	}
	if !found {
		return Result{}, fmt.Errorf("script answered naming check %q, which this request doesn't have", limitedBy)
	}
	return res, nil
}

func (r Request) now() time.Time {
	if r.Now.IsZero() {
		return time.Now()
	}
	return r.Now
}

func authFailKey(workspaceID, ip string) string {
	return authFailKeyPrefix + workspaceID + ":" + ip
}

// quotaKey is the consumer's counter for the calendar month (UTC) of now:
// quota:{consumer_id}:{yyyymm}. A new month starts a new counter.
func quotaKey(consumerID string, now time.Time) string {
	return quotaKeyPrefix + consumerID + ":" + now.UTC().Format("200601")
}

// untilNextMonth is how long until the next calendar month (UTC) starts,
// when a used-up quota is full again.
func untilNextMonth(now time.Time) time.Duration {
	now = now.UTC()
	next := time.Date(now.Year(), now.Month()+1, 1, 0, 0, 0, 0, time.UTC)
	return next.Sub(now)
}
