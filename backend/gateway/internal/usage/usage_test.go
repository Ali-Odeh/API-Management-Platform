package usage

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"os"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

var uuidV7 = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)

func TestNewIDIsAUUIDv7OrderedByTime(t *testing.T) {
	early := time.UnixMilli(1_700_000_000_000)
	late := early.Add(time.Millisecond)
	seen := map[string]bool{}
	for range 1000 {
		id := NewID(early)
		if !uuidV7.MatchString(id) {
			t.Fatalf("%q is not a UUIDv7", id)
		}
		if seen[id] {
			t.Fatalf("%q made twice", id)
		}
		seen[id] = true
		if later := NewID(late); later <= id {
			t.Fatalf("an id made a millisecond later sorts first: %s <= %s", later, id)
		}
	}
	// The first 48 bits are the Unix milliseconds.
	if got := strings.ReplaceAll(NewID(early), "-", "")[:12]; got != "018bcfe56800" {
		t.Errorf("time bits %s, want 018bcfe56800", got)
	}
}

func TestFieldsLeaveOutEmptyValues(t *testing.T) {
	e := Event{ID: "id1", Time: time.UnixMicro(1_700_000_000_123_456), WorkspaceID: "ws1", Method: "GET",
		Path: "/v1/orders", Status: 404, LatencyMS: 0.042, Bytes: 31}
	got := e.fields()
	want := []any{"v", "1", "id", "id1", "ts", "1700000000123456", "workspace_id", "ws1", "method", "GET",
		"path", "/v1/orders", "status", "404", "latency_ms", "0.042", "upstream_ms", "0", "bytes", "31"}
	if len(got) != len(want) {
		t.Fatalf("fields %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("fields %v, want %v", got, want)
		}
	}
}

// fakeAppender keeps what it was given; fail makes Append fail.
type fakeAppender struct {
	mu      sync.Mutex
	batches [][]Event
	fail    error
	block   chan struct{} // if set, Append waits for it
}

func (f *fakeAppender) Append(_ context.Context, events []Event) error {
	if f.block != nil {
		<-f.block
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.fail != nil {
		return f.fail
	}
	f.batches = append(f.batches, append([]Event(nil), events...))
	return nil
}

func (f *fakeAppender) sizes() []int {
	f.mu.Lock()
	defer f.mu.Unlock()
	var n []int
	for _, b := range f.batches {
		n = append(n, len(b))
	}
	return n
}

func event(i int) Event { return Event{ID: string(rune('a' + i%26)), Status: 200} }

func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	for deadline := time.Now().Add(2 * time.Second); time.Now().Before(deadline); time.Sleep(5 * time.Millisecond) {
		if cond() {
			return
		}
	}
	t.Fatalf("timed out waiting for %s", what)
}

func start(t *testing.T, r *Recorder) (stop func()) {
	t.Helper()
	ctx, cancel := context.WithCancel(t.Context())
	done := make(chan struct{})
	go func() { r.Run(ctx); close(done) }()
	return func() { cancel(); <-done }
}

func TestAFullBatchIsWrittenAtOnce(t *testing.T) {
	out := &fakeAppender{}
	r := NewRecorder(out, slog.New(slog.DiscardHandler), Options{BatchSize: 3, FlushEvery: time.Hour})
	stop := start(t, r)
	defer stop()

	for i := range 7 {
		r.Record(event(i))
	}
	waitFor(t, "two full batches", func() bool { return len(out.sizes()) == 2 })
	if got := out.sizes(); got[0] != 3 || got[1] != 3 {
		t.Errorf("batches %v, want [3 3] before the flush interval", got)
	}
}

func TestEventsAreWrittenAfterTheFlushInterval(t *testing.T) {
	out := &fakeAppender{}
	r := NewRecorder(out, slog.New(slog.DiscardHandler), Options{BatchSize: 100, FlushEvery: 20 * time.Millisecond})
	stop := start(t, r)
	defer stop()

	r.Record(event(1))
	r.Record(event(2))
	waitFor(t, "the timed flush", func() bool { return len(out.sizes()) == 1 })
	if r.Written() != 2 || r.Dropped() != 0 {
		t.Errorf("written %d dropped %d, want 2 and 0", r.Written(), r.Dropped())
	}
}

func TestStoppingWritesEverythingStillQueued(t *testing.T) {
	out := &fakeAppender{}
	r := NewRecorder(out, slog.New(slog.DiscardHandler), Options{BatchSize: 4, FlushEvery: time.Hour})
	for i := range 10 {
		r.Record(event(i)) // queued before Run starts: still written
	}
	stop := start(t, r)
	stop()

	total := 0
	for _, n := range out.sizes() {
		total += n
	}
	if total != 10 || r.Written() != 10 {
		t.Errorf("wrote %d (%v), want all 10", total, out.sizes())
	}
}

func TestAFullBufferDropsAndNeverBlocks(t *testing.T) {
	out := &fakeAppender{block: make(chan struct{})}
	var logs bytes.Buffer
	r := NewRecorder(out, slog.New(slog.NewJSONHandler(&logs, nil)), Options{BufferSize: 5, BatchSize: 1, FlushEvery: time.Hour})
	stop := start(t, r)

	r.Record(event(0)) // taken by Run, which then waits in Append
	waitFor(t, "Run to take the first event", func() bool { return len(r.events) == 0 })
	began := time.Now()
	for i := range 20 {
		r.Record(event(i))
	}
	if took := time.Since(began); took > 50*time.Millisecond {
		t.Errorf("20 Records took %s with Redis stuck; they must never wait", took)
	}
	if r.Dropped() != 15 {
		t.Errorf("dropped %d, want 15 (20 minus the 5 the buffer holds)", r.Dropped())
	}
	close(out.block)
	stop()
	if r.Written() != 6 {
		t.Errorf("written %d, want 6", r.Written())
	}
	if !strings.Contains(logs.String(), `"dropped":15`) {
		t.Errorf("no warning with the 15 dropped events: %s", logs.String())
	}
}

func TestAFailedWriteDropsItsBatchAndWarnsOncePerInterval(t *testing.T) {
	out := &fakeAppender{fail: errors.New("redis: connection refused")}
	var logs bytes.Buffer
	r := NewRecorder(out, slog.New(slog.NewJSONHandler(&logs, nil)), Options{BatchSize: 2, FlushEvery: time.Hour})
	clock := time.Unix(0, 0)
	r.now = func() time.Time { return clock }

	batch := []Event{event(1), event(2)}
	r.flush(batch)
	r.flush([]Event{event(3), event(4)}) // within the interval: counted, not logged
	if got := strings.Count(logs.String(), "usage events dropped"); got != 1 {
		t.Fatalf("%d warnings, want 1: %s", got, logs.String())
	}
	if !strings.Contains(logs.String(), `"dropped":2`) || !strings.Contains(logs.String(), "connection refused") {
		t.Errorf("first warning: %s", logs.String())
	}

	clock = clock.Add(dropWarnEvery)
	logs.Reset()
	r.flush([]Event{event(5)})
	// The held-back batch is counted in the next warning.
	if !strings.Contains(logs.String(), `"dropped":3`) || !strings.Contains(logs.String(), `"dropped_total":5`) {
		t.Errorf("second warning: %s", logs.String())
	}
	if r.Dropped() != 5 || r.Written() != 0 {
		t.Errorf("dropped %d written %d", r.Dropped(), r.Written())
	}
	if len(batch) != 2 || batch[0].ID != "" {
		t.Errorf("flush must clear the batch it was given for reuse, got %v", batch)
	}
}

// testRedis connects to REDIS_TEST_URL (Docker's Redis, database 15); the
// test is skipped without it.
func testRedis(t *testing.T) *redis.Client {
	t.Helper()
	url := os.Getenv("REDIS_TEST_URL")
	if url == "" {
		t.Skip("REDIS_TEST_URL not set; start Docker's Redis and set it, e.g. redis://localhost:6379/15")
	}
	opts, err := redis.ParseURL(url)
	if err != nil {
		t.Fatalf("REDIS_TEST_URL: %v", err)
	}
	rdb := redis.NewClient(opts)
	t.Cleanup(func() { rdb.Close() })
	ctx, cancel := context.WithTimeout(t.Context(), 2*time.Second)
	defer cancel()
	if err := rdb.Ping(ctx).Err(); err != nil {
		t.Fatalf("Redis not reachable at REDIS_TEST_URL: %v", err)
	}
	return rdb
}

func TestStreamAppendsEveryEventInOrder(t *testing.T) {
	rdb := testRedis(t)
	// Its own key: other packages' tests share database 15 and run in parallel.
	s := &Stream{rdb: rdb, key: "usage:test:" + NewID(time.Now())}
	t.Cleanup(func() { rdb.Del(context.Background(), s.key) })

	now := time.Now()
	events := []Event{
		{ID: NewID(now), Time: now, WorkspaceID: "ws1", Method: "GET", Path: "/a", Status: 200, ConsumerID: "c1"},
		{ID: NewID(now), Time: now, WorkspaceID: "ws1", Method: "POST", Path: "/b", Status: 429, RateLimit: "limited"},
	}
	if err := s.Append(t.Context(), events); err != nil {
		t.Fatal(err)
	}

	entries, err := rdb.XRange(t.Context(), s.key, "-", "+").Result()
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 2 {
		t.Fatalf("%d entries, want 2", len(entries))
	}
	first, second := entries[0].Values, entries[1].Values
	if first["id"] != events[0].ID || first["consumer_id"] != "c1" || first["status"] != "200" || first["v"] != SchemaVersion {
		t.Errorf("first entry %v", first)
	}
	if second["path"] != "/b" || second["ratelimit"] != "limited" || second["consumer_id"] != nil {
		t.Errorf("second entry %v", second)
	}
}

func TestStreamAppendFailsWhenRedisIsDown(t *testing.T) {
	rdb := redis.NewClient(&redis.Options{Addr: "127.0.0.1:1", DialTimeout: 100 * time.Millisecond, MaxRetries: -1})
	defer rdb.Close()
	err := NewStream(rdb).Append(t.Context(), []Event{{ID: "x"}})
	if err == nil || !strings.Contains(err.Error(), "append 1 usage events") {
		t.Errorf("err = %v", err)
	}
}
