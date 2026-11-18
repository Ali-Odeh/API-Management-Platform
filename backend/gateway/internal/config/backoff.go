package config

import (
	"math/rand/v2"
	"time"
)

// backoff spaces out retries after failures: 1 s, 2 s, 4 s ... up to max.
// Each wait is picked at random between half and all of that value
// ("jitter"), so many gateways that failed together don't all retry at the
// same moment and hit the control plane at once.
type backoff struct {
	min, max time.Duration
	attempt  int
}

// next returns how long to wait before the next retry.
func (b *backoff) next() time.Duration {
	d := b.max
	// Shifting by a large attempt count would overflow, so stop doubling
	// once max is reached.
	if b.attempt < 62 {
		if doubled := b.min << b.attempt; doubled > 0 && doubled < b.max {
			d = doubled
			b.attempt++
		}
	}
	half := d / 2
	return half + rand.N(d-half+1)
}

// reset starts again from min, after a success.
func (b *backoff) reset() {
	b.attempt = 0
}
