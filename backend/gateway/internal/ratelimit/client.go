package ratelimit

import (
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/redis/go-redis/v9/maintnotifications"
)

// NewClient connects lazily to the Redis at url: nothing is dialed until the
// first request, so a Redis that is down at startup doesn't stop the gateway
// (the rate limit fails open until it is back).
//
// Every timeout is short and there are no retries: one limit check must stay
// within a few milliseconds, and a slow Redis counts as down.
func NewClient(url string) (*redis.Client, error) {
	opts, err := redis.ParseURL(url)
	if err != nil {
		// Not wrapped: go-redis errors can repeat the URL, which may hold a
		// password.
		return nil, fmt.Errorf("REDIS_URL is not a valid Redis URL")
	}
	opts.DialTimeout = time.Second
	opts.ReadTimeout = DefaultTimeout
	opts.WriteTimeout = DefaultTimeout
	opts.PoolTimeout = DefaultTimeout // wait at most this long for a free connection
	opts.MaxRetries = -1              // -1 means none in go-redis (0 means its default of 3)
	// "Maintenance notifications" are for Redis Cloud/Enterprise. Left on,
	// go-redis looks up the hostname while building the client, and when it
	// can't be resolved (Redis's container is down) the gateway would start
	// 2-4 s late.
	opts.MaintNotificationsConfig = &maintnotifications.Config{Mode: maintnotifications.ModeDisabled}
	return redis.NewClient(opts), nil
}
