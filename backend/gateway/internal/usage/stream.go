package usage

import (
	"context"
	"fmt"

	"github.com/redis/go-redis/v9"
)

// Stream names and limits (PLAN.md section 5, "Redis keys").
const (
	StreamKey = "usage:events"
	// StreamMaxLen caps the stream while the usage-ingest worker is away:
	// at about 0.5 KB an entry, 200,000 entries are some 100 MB of Redis
	// memory, about 11 minutes of traffic at 300 requests/s. Beyond it the
	// oldest unread entries are trimmed (lost). The worker deletes what it
	// has stored, so in normal operation the stream stays small.
	StreamMaxLen = 200_000
)

// Stream appends events to the usage stream in Redis.
type Stream struct {
	rdb *redis.Client
	key string // StreamKey; a test's own key in tests
}

// NewStream writes through rdb; give it a client of its own (redisconn.Open)
// so a batch never holds up a rate-limit check.
func NewStream(rdb *redis.Client) *Stream { return &Stream{rdb: rdb, key: StreamKey} }

// Append writes events with one pipelined round trip: an XADD per event,
// each trimming the stream to about StreamMaxLen ("~" lets Redis trim
// whole internal nodes, which is much cheaper than an exact cap).
func (s *Stream) Append(ctx context.Context, events []Event) error {
	_, err := s.rdb.Pipelined(ctx, func(p redis.Pipeliner) error {
		for i := range events {
			p.XAdd(ctx, &redis.XAddArgs{
				Stream: s.key,
				MaxLen: StreamMaxLen,
				Approx: true,
				Values: events[i].fields(),
			})
		}
		return nil
	})
	if err != nil {
		return fmt.Errorf("append %d usage events: %w", len(events), err)
	}
	return nil
}
