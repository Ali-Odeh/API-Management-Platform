// Package ratelimit decides whether a request may go on, with token buckets
// kept in Redis (PLAN.md section 7): one per workspace, checked on every
// request, and one per consumer, checked on protected routes.
//
// The counters live in Redis, not in the gateway's memory, so several
// gateways share each bucket. The whole check is one Lua script, which Redis
// runs as a single atomic step, in one round trip.
package ratelimit

import (
	"context"
	_ "embed" // for the //go:embed line below
	"errors"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

// The script is a real .lua file, built into the binary at compile time.
//
//go:embed buckets.lua
var bucketsLua string

// redis.NewScript sends only the script's SHA-1 (EVALSHA) and falls back to
// sending the whole script (EVAL) if Redis does not know it yet.
var buckets = redis.NewScript(bucketsLua)

const (
	workspaceKeyPrefix = "rlws:"
	consumerKeyPrefix  = "rl:"
	// The check must be quick. A Redis slower than this counts as down, and
	// the caller decides what to do (the middleware lets the request through).
	DefaultTimeout = 50 * time.Millisecond
)

// Bucket names which bucket refused a request.
type Bucket string

const (
	BucketWorkspace Bucket = "workspace"
	BucketConsumer  Bucket = "consumer"
)

// Request is what one request is checked against.
type Request struct {
	WorkspaceID    string
	WorkspaceLimit int    // the workspace's requests per minute
	ConsumerID     string // "" on public routes: only the workspace's bucket is checked
	ConsumerLimit  int    // the consumer's requests per minute
}

// Result is the answer for one request. Limit and Remaining describe one
// bucket: the consumer's (or, on a public route, the workspace's) when the
// request is allowed, the one that refused it otherwise.
type Result struct {
	Allowed    bool
	LimitedBy  Bucket        // "" when allowed
	Limit      int           // that bucket's requests per minute
	Remaining  int           // whole tokens left in it after this request
	RetryAfter time.Duration // when refused: time until every bucket has a token; 0 when allowed
}

// Limiter checks requests against token buckets in Redis.
type Limiter struct {
	rdb     redis.Scripter
	timeout time.Duration
}

// New builds a limiter on a Redis client (*redis.Client fits Scripter).
func New(rdb redis.Scripter) *Limiter {
	return &Limiter{rdb: rdb, timeout: DefaultTimeout}
}

// Allow takes one token from the workspace's bucket and, for a consumer,
// from the consumer's bucket, only if both have one. Each limit is the
// bucket's size and its refill per minute. An error means Redis could not
// answer in time (or the request was malformed); the result is then empty.
func (l *Limiter) Allow(ctx context.Context, req Request) (Result, error) {
	keys, args, err := req.script()
	if err != nil {
		return Result{}, err
	}

	ctx, cancel := context.WithTimeout(ctx, l.timeout)
	defer cancel()

	reply, err := buckets.Run(ctx, l.rdb, keys, args...).Slice()
	if err != nil {
		return Result{}, fmt.Errorf("rate limit for workspace %s: %w", req.WorkspaceID, err)
	}
	res, err := req.result(reply)
	if err != nil {
		return Result{}, fmt.Errorf("rate limit for workspace %s: %w", req.WorkspaceID, err)
	}
	return res, nil
}

// script gives the script's keys and capacities: the workspace's first, then
// the consumer's if there is one.
func (r Request) script() (keys []string, args []any, err error) {
	if r.WorkspaceID == "" {
		return nil, nil, errors.New("rate limit without a workspace")
	}
	// The control plane guarantees at least 1 (CHECKs in the database); the
	// config parser drops a workspace without valid limits.
	if r.WorkspaceLimit < 1 {
		return nil, nil, fmt.Errorf("rate limit %d for workspace %s: must be at least 1", r.WorkspaceLimit, r.WorkspaceID)
	}
	keys = []string{workspaceKeyPrefix + r.WorkspaceID}
	args = []any{r.WorkspaceLimit}
	if r.ConsumerID == "" {
		return keys, args, nil
	}
	if r.ConsumerLimit < 1 {
		return nil, nil, fmt.Errorf("rate limit %d for consumer %s: must be at least 1", r.ConsumerLimit, r.ConsumerID)
	}
	return append(keys, consumerKeyPrefix+r.ConsumerID), append(args, r.ConsumerLimit), nil
}

// result reads the script's { allowed, remaining, retry_after_ms, limited_by }.
func (r Request) result(reply []any) (Result, error) {
	if len(reply) != 4 {
		return Result{}, fmt.Errorf("script returned %d values, want 4", len(reply))
	}
	allowed, okA := reply[0].(int64)
	remaining, okR := reply[1].(int64)
	retryMS, okW := reply[2].(int64)
	limitedBy, okL := reply[3].(string)
	if !okA || !okR || !okW || !okL {
		return Result{}, fmt.Errorf("script returned %v, want three integers and a string", reply)
	}

	res := Result{
		Allowed:    allowed == 1,
		LimitedBy:  Bucket(limitedBy),
		Remaining:  int(remaining),
		RetryAfter: time.Duration(retryMS) * time.Millisecond,
	}
	switch {
	case res.LimitedBy == BucketWorkspace, res.Allowed && r.ConsumerID == "":
		res.Limit = r.WorkspaceLimit
	case res.LimitedBy == BucketConsumer, res.Allowed:
		res.Limit = r.ConsumerLimit
	default:
		return Result{}, fmt.Errorf("script refused the request naming bucket %q", limitedBy)
	}
	return res, nil
}
