// Package redisconn opens the gateway's Redis clients. The rate limiter and
// the usage events each get their own client, with timeouts that fit their
// job: a limit check must answer within milliseconds, a batch of usage
// events may take longer, and one must never wait for the other.
package redisconn

import (
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
	"github.com/redis/go-redis/v9/maintnotifications"
)

// Open connects lazily to the Redis at url: nothing is dialed until the
// first command, so a Redis that is down at startup doesn't stop the
// gateway. Reads, writes and waiting for a pooled connection each give up
// after timeout, and there are no retries: a slow Redis counts as down.
func Open(url string, timeout time.Duration) (*redis.Client, error) {
	opts, err := redis.ParseURL(url)
	if err != nil {
		// Not wrapped: go-redis errors can repeat the URL, which may hold a
		// password.
		return nil, fmt.Errorf("REDIS_URL is not a valid Redis URL")
	}
	opts.DialTimeout = time.Second
	opts.ReadTimeout = timeout
	opts.WriteTimeout = timeout
	opts.PoolTimeout = timeout // wait at most this long for a free connection
	opts.MaxRetries = -1       // -1 means none in go-redis (0 means its default of 3)
	// "Maintenance notifications" are for Redis Cloud/Enterprise. Left on,
	// go-redis looks up the hostname while building the client, and when it
	// can't be resolved (Redis's container is down) the gateway would start
	// 2-4 s late.
	opts.MaintNotificationsConfig = &maintnotifications.Config{Mode: maintnotifications.ModeDisabled}
	return redis.NewClient(opts), nil
}
