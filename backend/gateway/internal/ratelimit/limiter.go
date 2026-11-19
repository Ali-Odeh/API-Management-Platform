// Package ratelimit decides whether a consumer may make one more request,
// with a token bucket kept in Redis (PLAN.md section 7).
//
// The counter lives in Redis, not in the gateway's memory, so several
// gateways share one bucket per consumer. The whole check is one Lua script,
// which Redis runs as a single atomic step.
package ratelimit

import (
	"context"
	_ "embed" // for the //go:embed line below
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

// The script is a real .lua file, built into the binary at compile time.
//
//go:embed tokenbucket.lua
var tokenBucketLua string

// redis.NewScript sends only the script's SHA-1 (EVALSHA) and falls back to
// sending the whole script (EVAL) if Redis does not know it yet.
var tokenBucket = redis.NewScript(tokenBucketLua)

const (
	keyPrefix = "rl:"
	// The check must be quick. A Redis slower than this counts as down, and
	// the caller decides what to do (the middleware lets the request through).
	DefaultTimeout = 50 * time.Millisecond
)

// Result is the answer for one request.
type Result struct {
	Allowed    bool
	Limit      int           // the consumer's requests per minute
	Remaining  int           // whole tokens left after this request
	RetryAfter time.Duration // when denied: time until the next token; 0 when allowed
}

// Limiter checks requests against per-consumer token buckets in Redis.
type Limiter struct {
	rdb     redis.Scripter
	timeout time.Duration
}

// New builds a limiter on a Redis client (*redis.Client fits Scripter).
func New(rdb redis.Scripter) *Limiter {
	return &Limiter{rdb: rdb, timeout: DefaultTimeout}
}

// Allow takes one token from the consumer's bucket if there is one.
// limitPerMin is the bucket's size and its refill per minute. An error means
// Redis could not answer in time; the result is then empty.
func (l *Limiter) Allow(ctx context.Context, consumerID string, limitPerMin int) (Result, error) {
	if limitPerMin < 1 {
		// The control plane guarantees at least 1 (a CHECK in the database).
		return Result{}, fmt.Errorf("rate limit %d for %s: must be at least 1", limitPerMin, consumerID)
	}

	ctx, cancel := context.WithTimeout(ctx, l.timeout)
	defer cancel()

	vals, err := tokenBucket.Run(ctx, l.rdb, []string{keyPrefix + consumerID}, limitPerMin).Int64Slice()
	if err != nil {
		return Result{}, fmt.Errorf("rate limit for %s: %w", consumerID, err)
	}
	if len(vals) != 3 {
		return Result{}, fmt.Errorf("rate limit for %s: script returned %d values, want 3", consumerID, len(vals))
	}
	return Result{
		Allowed:    vals[0] == 1,
		Limit:      limitPerMin,
		Remaining:  int(vals[1]),
		RetryAfter: time.Duration(vals[2]) * time.Millisecond,
	}, nil
}
