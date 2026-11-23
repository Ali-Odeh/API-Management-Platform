package ratelimit

import (
	"github.com/redis/go-redis/v9"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/redisconn"
)

// NewClient is the rate limiter's Redis client: every timeout is
// DefaultTimeout and there are no retries, because one limit check must stay
// within a few milliseconds. See redisconn.Open.
func NewClient(url string) (*redis.Client, error) {
	return redisconn.Open(url, DefaultTimeout)
}
