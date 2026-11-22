// Package usage records every request the gateway answers for a workspace
// as a usage event, for the request log and the analytics (PLAN.md sections
// 6 and 7, V2.1).
//
// Events go to a Redis Stream; the control plane's usage-ingest worker
// reads them into Postgres. Writing them never delays a request: Record only
// puts the event into a bounded buffer, and a background goroutine writes
// the buffer to Redis in pipelined batches. When the buffer is full or Redis
// is down, events are dropped and counted, like the rate limit fails open.
package usage

import (
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"strconv"
	"time"
)

// SchemaVersion is the "v" field of every stream entry, so the worker can
// tell entries apart if their fields ever change.
const SchemaVersion = "1"

// Event is one answered request: the request log line's fields plus an id.
type Event struct {
	ID          string // UUIDv7, made by the gateway; the worker inserts each id once
	Time        time.Time
	WorkspaceID string
	RequestID   string
	Method      string
	Path        string // without the query string, as in the log
	RouteID     string // "" when no route matched
	TargetID    string // "" when the request wasn't forwarded
	ConsumerID  string // "" on public routes and refused keys
	KeyID       string
	Status      int
	LatencyMS   float64 // the whole request
	UpstreamMS  float64 // waiting for the backend; 0 if not called
	Bytes       int64   // response body bytes
	ClientIP    string
	RateLimit   string // the log's "ratelimit" field
}

// fields is the event as a stream entry's field-value pairs. Empty values
// are left out: the worker reads a missing field as empty.
func (e *Event) fields() []any {
	f := make([]any, 0, 32)
	add := func(name, value string) {
		if value != "" {
			f = append(f, name, value)
		}
	}
	add("v", SchemaVersion)
	add("id", e.ID)
	add("ts", strconv.FormatInt(e.Time.UnixMicro(), 10))
	add("workspace_id", e.WorkspaceID)
	add("request_id", e.RequestID)
	add("method", e.Method)
	add("path", e.Path)
	add("route_id", e.RouteID)
	add("target_id", e.TargetID)
	add("consumer_id", e.ConsumerID)
	add("key_id", e.KeyID)
	add("status", strconv.Itoa(e.Status))
	add("latency_ms", strconv.FormatFloat(e.LatencyMS, 'f', -1, 64))
	add("upstream_ms", strconv.FormatFloat(e.UpstreamMS, 'f', -1, 64))
	add("bytes", strconv.FormatInt(e.Bytes, 10))
	add("client_ip", e.ClientIP)
	add("ratelimit", e.RateLimit)
	return f
}

// NewID makes a UUIDv7 (RFC 9562): 48 bits of Unix milliseconds, then
// random bits. Ids made later sort later, so the request log's index grows
// at its end instead of being written all over.
func NewID(now time.Time) string {
	var b [16]byte
	var ms [8]byte
	binary.BigEndian.PutUint64(ms[:], uint64(now.UnixMilli()))
	copy(b[:6], ms[2:])
	_, _ = rand.Read(b[6:]) // never fails (crypto/rand panics instead)
	b[6] = b[6]&0x0f | 0x70 // version 7
	b[8] = b[8]&0x3f | 0x80 // RFC 9562 variant

	var s [36]byte
	hex.Encode(s[0:8], b[0:4])
	s[8] = '-'
	hex.Encode(s[9:13], b[4:6])
	s[13] = '-'
	hex.Encode(s[14:18], b[6:8])
	s[18] = '-'
	hex.Encode(s[19:23], b[8:10])
	s[23] = '-'
	hex.Encode(s[24:], b[10:])
	return string(s[:])
}
