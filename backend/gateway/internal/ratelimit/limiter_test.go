package ratelimit

// Most of these tests need a real Redis: the atomic Lua script is the whole
// point, and a fake Redis would prove nothing. They use the URL in
// REDIS_TEST_URL (check.ps1 and CI set it; locally that is Docker's Redis,
// database 15, so real counters are never touched). Without it they skip.

import (
	"context"
	"fmt"
	"math"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// testRedis connects to REDIS_TEST_URL. It skips the test when the variable
// is unset, and fails when it is set but Redis can't be reached (so CI never
// skips silently). It never empties the database: tests of other packages
// may use it at the same time, so every test uses its own bucket names.
func testRedis(t testing.TB) *redis.Client {
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

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := rdb.Ping(ctx).Err(); err != nil {
		t.Fatalf("Redis at %s not reachable: %v", url, err)
	}
	return rdb
}

// newTestLimiter allows a generous timeout: Docker on Windows adds latency,
// and these tests are about correctness, not speed.
func newTestLimiter(t testing.TB) (*Limiter, *redis.Client) {
	rdb := testRedis(t)
	l := New(rdb)
	l.timeout = 2 * time.Second
	return l, rdb
}

// unique gives each test its own bucket names, so tests never share a
// bucket, whatever else runs against the same Redis.
func unique(t testing.TB, name string) string {
	return fmt.Sprintf("test/%s/%s/%d", t.Name(), name, time.Now().UnixNano())
}

// roomy is a limit no test comes near.
const roomy = 1_000_000

// consumerReq is a protected route's request in a workspace of its own with
// a roomy limit, so only the consumer's bucket can refuse it.
func consumerReq(t testing.TB, consumer string, limit int) Request {
	return Request{WorkspaceID: unique(t, "ws"), WorkspaceLimit: roomy, ConsumerID: consumer, ConsumerLimit: limit}
}

func allow(t *testing.T, l *Limiter, req Request) Result {
	t.Helper()
	res, err := l.Allow(t.Context(), req)
	if err != nil {
		t.Fatalf("Allow(%+v): %v", req, err)
	}
	return res
}

// tokens reads a bucket's stored token count (a float: it refills continuously).
func tokens(t *testing.T, rdb *redis.Client, key string) float64 {
	t.Helper()
	n, err := rdb.HGet(t.Context(), key, "tokens").Float64()
	if err != nil {
		t.Fatalf("HGET %s tokens: %v", key, err)
	}
	return n
}

func TestBurstUpToTheLimitThenDenied(t *testing.T) {
	req := consumerReq(t, unique(t, "c1"), 5)
	l, _ := newTestLimiter(t)

	for want := 4; want >= 0; want-- { // a full bucket of 5
		res := allow(t, l, req)
		if !res.Allowed || res.Remaining != want || res.Limit != 5 || res.RetryAfter != 0 || res.LimitedBy != "" {
			t.Fatalf("request %d: %+v, want allowed with %d left", 5-want, res, want)
		}
	}

	res := allow(t, l, req)
	// 5 per minute = one token every 12 s.
	if res.Allowed || res.LimitedBy != BucketConsumer || res.Remaining != 0 || res.Limit != 5 ||
		res.RetryAfter <= 11*time.Second || res.RetryAfter > 12*time.Second {
		t.Errorf("6th request: %+v, want refused by the consumer, retry in about 12s", res)
	}
}

func TestOneRequestPerMinute(t *testing.T) {
	req := consumerReq(t, unique(t, "c1"), 1)
	l, _ := newTestLimiter(t)

	if res := allow(t, l, req); !res.Allowed || res.Remaining != 0 {
		t.Fatalf("first: %+v", res)
	}
	res := allow(t, l, req)
	if res.Allowed || res.RetryAfter < 59*time.Second || res.RetryAfter > time.Minute {
		t.Errorf("second: %+v, want denied, retry in about 60s", res)
	}
}

func TestBucketRefills(t *testing.T) {
	req := consumerReq(t, unique(t, "c1"), 600) // 10 per second: one token every 100 ms
	l, _ := newTestLimiter(t)

	// Empty the bucket (it refills a little while we drain it). The denial
	// that ends the loop is the one to check: another call made after it
	// could rightly be allowed, because the bucket refills continuously and
	// the next token may arrive during that call's Redis round trip.
	var res Result
	for i := 0; ; i++ {
		if res = allow(t, l, req); !res.Allowed {
			break
		}
		if i > 2*req.ConsumerLimit {
			t.Fatal("the bucket never ran empty")
		}
	}
	if res.RetryAfter <= 0 || res.RetryAfter > 100*time.Millisecond {
		t.Fatalf("right after draining: %+v, want a RetryAfter in (0, 100ms]", res)
	}

	time.Sleep(res.RetryAfter + 30*time.Millisecond)

	if res := allow(t, l, req); !res.Allowed {
		t.Errorf("after waiting RetryAfter: %+v, want allowed", res)
	}
}

func TestConsumersHaveSeparateBuckets(t *testing.T) {
	greedy := Request{WorkspaceID: unique(t, "ws"), WorkspaceLimit: roomy, ConsumerID: unique(t, "greedy"), ConsumerLimit: 1}
	polite := greedy
	polite.ConsumerID = unique(t, "polite")
	l, _ := newTestLimiter(t)

	allow(t, l, greedy) // empties greedy's bucket
	if res := allow(t, l, greedy); res.Allowed {
		t.Fatal("greedy should be limited")
	}
	if res := allow(t, l, polite); !res.Allowed {
		t.Error("another consumer was limited by greedy's bucket")
	}
}

func TestLoweredLimitAppliesAtOnce(t *testing.T) {
	req := consumerReq(t, unique(t, "c1"), 60)
	l, _ := newTestLimiter(t)

	allow(t, l, req) // 59 left at the old limit
	req.ConsumerLimit = 10
	if res := allow(t, l, req); !res.Allowed || res.Remaining != 9 || res.Limit != 10 {
		t.Errorf("after lowering to 10: %+v, want 9 left", res)
	}
}

// ---- The workspace's bucket (MT.8) ----

// A public route has no consumer: only the workspace's bucket counts, and
// the answer describes it.
func TestPublicRouteUsesTheWorkspaceBucketOnly(t *testing.T) {
	req := Request{WorkspaceID: unique(t, "ws"), WorkspaceLimit: 3}
	l, _ := newTestLimiter(t)

	for want := 2; want >= 0; want-- {
		if res := allow(t, l, req); !res.Allowed || res.Remaining != want || res.Limit != 3 {
			t.Fatalf("request %d: %+v, want allowed with %d left", 3-want, res, want)
		}
	}
	res := allow(t, l, req)
	if res.Allowed || res.LimitedBy != BucketWorkspace || res.Limit != 3 || res.Remaining != 0 ||
		res.RetryAfter <= 19*time.Second || res.RetryAfter > 20*time.Second {
		t.Errorf("4th request: %+v, want refused by the workspace, retry in about 20s", res)
	}
}

// Every consumer of a workspace draws from the workspace's one bucket.
func TestConsumersShareTheirWorkspacesBucket(t *testing.T) {
	ws := unique(t, "ws")
	l, _ := newTestLimiter(t)

	for i := range 3 {
		req := Request{WorkspaceID: ws, WorkspaceLimit: 3, ConsumerID: unique(t, fmt.Sprint("c", i)), ConsumerLimit: 100}
		if res := allow(t, l, req); !res.Allowed || res.Limit != 100 || res.Remaining != 99 {
			t.Fatalf("consumer %d: %+v, want allowed, describing its own bucket", i, res)
		}
	}
	fresh := Request{WorkspaceID: ws, WorkspaceLimit: 3, ConsumerID: unique(t, "c3"), ConsumerLimit: 100}
	res := allow(t, l, fresh)
	if res.Allowed || res.LimitedBy != BucketWorkspace || res.Limit != 3 || res.Remaining != 0 {
		t.Errorf("a 4th consumer with a full bucket: %+v, want refused by the workspace", res)
	}
}

func TestWorkspacesHaveSeparateBuckets(t *testing.T) {
	noisy := Request{WorkspaceID: unique(t, "noisy"), WorkspaceLimit: 1}
	quiet := Request{WorkspaceID: unique(t, "quiet"), WorkspaceLimit: 1}
	l, _ := newTestLimiter(t)

	allow(t, l, noisy)
	if res := allow(t, l, noisy); res.Allowed {
		t.Fatal("noisy should be limited")
	}
	if res := allow(t, l, quiet); !res.Allowed {
		t.Error("another workspace was limited by noisy's bucket")
	}
}

// Refused by the workspace: the consumer's bucket keeps its tokens.
func TestRefusedByTheWorkspaceCostsTheConsumerNothing(t *testing.T) {
	req := Request{WorkspaceID: unique(t, "ws"), WorkspaceLimit: 1, ConsumerID: unique(t, "c1"), ConsumerLimit: 3}
	l, rdb := newTestLimiter(t)

	allow(t, l, req) // workspace 0 left, consumer 2
	for range 5 {
		if res := allow(t, l, req); res.Allowed || res.LimitedBy != BucketWorkspace || res.Limit != 1 {
			t.Fatalf("%+v, want refused by the workspace", res)
		}
	}
	// 3 per minute refills 0.05 tokens a second: still 2 whole tokens.
	if got := tokens(t, rdb, consumerKeyPrefix+req.ConsumerID); math.Floor(got) != 2 {
		t.Errorf("the consumer has %.3f tokens, want 2: refused requests must not spend them", got)
	}
}

// Refused by the consumer: the workspace's bucket keeps its tokens.
func TestRefusedByTheConsumerCostsTheWorkspaceNothing(t *testing.T) {
	req := Request{WorkspaceID: unique(t, "ws"), WorkspaceLimit: 3, ConsumerID: unique(t, "c1"), ConsumerLimit: 1}
	l, rdb := newTestLimiter(t)

	allow(t, l, req) // workspace 2 left, consumer 0
	for range 5 {
		if res := allow(t, l, req); res.Allowed || res.LimitedBy != BucketConsumer || res.Limit != 1 {
			t.Fatalf("%+v, want refused by the consumer", res)
		}
	}
	if got := tokens(t, rdb, workspaceKeyPrefix+req.WorkspaceID); math.Floor(got) != 2 {
		t.Errorf("the workspace has %.3f tokens, want 2: refused requests must not spend them", got)
	}
}

// Both buckets empty: the one with the longer wait is named, and its wait is
// the retry time, since the request needs a token from both.
func TestBothEmptyNamesTheLongerWait(t *testing.T) {
	l, _ := newTestLimiter(t)

	// The consumer is emptied through other workspaces (buckets are
	// independent): at 2 per minute its next token is ~30 s away. The
	// workspace's, at 1 per minute, is ~60 s away.
	consumer := unique(t, "c1")
	allow(t, l, consumerReq(t, consumer, 2))
	allow(t, l, consumerReq(t, consumer, 2))
	ws := Request{WorkspaceID: unique(t, "ws"), WorkspaceLimit: 1}
	allow(t, l, ws)

	both := Request{WorkspaceID: ws.WorkspaceID, WorkspaceLimit: 1, ConsumerID: consumer, ConsumerLimit: 2}
	res := allow(t, l, both)
	if res.Allowed || res.LimitedBy != BucketWorkspace || res.Limit != 1 || res.RetryAfter < 59*time.Second {
		t.Errorf("%+v, want refused by the workspace, retry in about 60s", res)
	}

	// The other way round: the consumer's wait is the longer one.
	consumer2 := unique(t, "c2")
	allow(t, l, consumerReq(t, consumer2, 1))
	ws2 := Request{WorkspaceID: unique(t, "ws2"), WorkspaceLimit: 2}
	allow(t, l, ws2)
	allow(t, l, ws2)

	both = Request{WorkspaceID: ws2.WorkspaceID, WorkspaceLimit: 2, ConsumerID: consumer2, ConsumerLimit: 1}
	res = allow(t, l, both)
	if res.Allowed || res.LimitedBy != BucketConsumer || res.Limit != 1 || res.RetryAfter < 59*time.Second {
		t.Errorf("%+v, want refused by the consumer, retry in about 60s", res)
	}
}

func TestIdleBucketsExpire(t *testing.T) {
	req := Request{WorkspaceID: unique(t, "ws"), WorkspaceLimit: 60, ConsumerID: unique(t, "c1"), ConsumerLimit: 60}
	l, rdb := newTestLimiter(t)

	allow(t, l, req)

	for _, key := range []string{workspaceKeyPrefix + req.WorkspaceID, consumerKeyPrefix + req.ConsumerID} {
		ttl, err := rdb.PTTL(t.Context(), key).Result()
		if err != nil || ttl <= 110*time.Second || ttl > 120*time.Second {
			t.Errorf("TTL of %s = %s (err %v), want about 120s", key, ttl, err)
		}
	}
}

// After a Redis restart the script cache is empty; go-redis must load it again.
func TestWorksAfterRedisForgetsTheScript(t *testing.T) {
	req := consumerReq(t, unique(t, "c1"), 60)
	l, rdb := newTestLimiter(t)
	allow(t, l, req)

	if err := rdb.ScriptFlush(t.Context()).Err(); err != nil {
		t.Fatalf("SCRIPT FLUSH: %v", err)
	}

	if res := allow(t, l, req); !res.Allowed || res.Remaining != 58 {
		t.Errorf("after SCRIPT FLUSH: %+v, want allowed with 58 left", res)
	}
}

// The point of the Lua script: many requests at the same moment can never
// take more tokens than a bucket has. Run with -race in CI.
func TestConcurrentRequestsNeverOverspend(t *testing.T) {
	req := consumerReq(t, unique(t, "c1"), 50)
	l, _ := newTestLimiter(t)

	allowed, elapsed := fire(t, l, 100, func(int) Request { return req })

	// 50 per minute refills one token every 1.2 s; a slow run earns one per 1.2 s.
	want := int64(50) + int64(elapsed/(1200*time.Millisecond))
	if got := allowed[req.ConsumerID]; got < 50 || got > want {
		t.Errorf("%d of 100 concurrent requests allowed, want exactly 50", got)
	}
}

// 100 requests at once from 5 consumers (limit 15 each, 75 together) in one
// workspace (limit 50): the workspace caps them at 50, and every consumer's
// bucket lost exactly the tokens of its allowed requests, none for the ones
// the workspace refused. Both buckets move together or not at all.
func TestConcurrentRequestsSpendBothBucketsAtomically(t *testing.T) {
	ws := unique(t, "ws")
	consumers := make([]string, 5)
	for i := range consumers {
		consumers[i] = unique(t, fmt.Sprint("c", i))
	}
	l, rdb := newTestLimiter(t)
	const wsLimit, consumerLimit = 50, 15

	allowed, elapsed := fire(t, l, 100, func(i int) Request {
		return Request{WorkspaceID: ws, WorkspaceLimit: wsLimit, ConsumerID: consumers[i%len(consumers)], ConsumerLimit: consumerLimit}
	})

	var total int64
	for _, n := range allowed {
		total += n
	}
	// The workspace refills one token every 1.2 s.
	if most := wsLimit + int64(elapsed/(1200*time.Millisecond)); total < wsLimit || total > most {
		t.Errorf("%d of 100 allowed, want %d (the workspace's limit)", total, wsLimit)
	}
	refill := elapsed.Minutes() * consumerLimit
	for _, c := range consumers {
		spent := float64(consumerLimit) - tokens(t, rdb, consumerKeyPrefix+c)
		// spent = allowed - refill since the start, so allowed-refill <= spent <= allowed.
		if n := float64(allowed[c]); spent > n+1e-9 || spent < n-refill-1e-9 {
			t.Errorf("consumer %s: %d allowed but %.3f tokens spent", c, allowed[c], spent)
		}
	}
}

// fire sends n requests at the same moment and counts the allowed ones by
// consumer ("" for requests without one).
func fire(t *testing.T, l *Limiter, n int, req func(i int) Request) (map[string]int64, time.Duration) {
	t.Helper()
	counts := make(map[string]*atomic.Int64)
	reqs := make([]Request, n)
	for i := range reqs {
		reqs[i] = req(i)
		if counts[reqs[i].ConsumerID] == nil {
			counts[reqs[i].ConsumerID] = new(atomic.Int64)
		}
	}

	var wg sync.WaitGroup
	start := make(chan struct{})
	for _, r := range reqs {
		wg.Go(func() {
			<-start // all goroutines fire together
			res, err := l.Allow(context.Background(), r)
			if err != nil {
				t.Errorf("Allow: %v", err)
				return
			}
			if res.Allowed {
				counts[r.ConsumerID].Add(1)
			}
		})
	}
	began := time.Now()
	close(start)
	wg.Wait()
	elapsed := time.Since(began)

	allowed := make(map[string]int64, len(counts))
	for id, c := range counts {
		allowed[id] = c.Load()
	}
	return allowed, elapsed
}

// ---- Malformed requests and answers ----

func TestMalformedRequestsAreErrors(t *testing.T) {
	l := New(redis.NewClient(&redis.Options{Addr: "127.0.0.1:1"})) // never called
	tests := map[string]Request{
		"no workspace":            {WorkspaceLimit: 60},
		"workspace limit 0":       {WorkspaceID: "w", WorkspaceLimit: 0},
		"consumer limit 0":        {WorkspaceID: "w", WorkspaceLimit: 60, ConsumerID: "c", ConsumerLimit: 0},
		"negative consumer limit": {WorkspaceID: "w", WorkspaceLimit: 60, ConsumerID: "c", ConsumerLimit: -1},
	}
	for name, req := range tests {
		if _, err := l.Allow(t.Context(), req); err == nil {
			t.Errorf("%s: Allow = nil error", name)
		}
	}
}

func TestResultReadsTheScriptsAnswer(t *testing.T) {
	public := Request{WorkspaceID: "w", WorkspaceLimit: 7}
	publicIP := Request{WorkspaceID: "w", WorkspaceLimit: 7, ClientIP: "1.2.3.4", IPLimit: 4}
	protected := Request{WorkspaceID: "w", WorkspaceLimit: 7, ConsumerID: "c", ConsumerLimit: 3}
	quota := Request{WorkspaceID: "w", WorkspaceLimit: 7, ConsumerID: "c", ConsumerLimit: 3, MonthlyQuota: 100}
	tests := []struct {
		name  string
		req   Request
		reply []any
		want  Result
		bad   bool
	}{
		{"public allowed", public, []any{int64(1), int64(6), int64(0), "workspace", int64(-1)}, Result{Allowed: true, Limit: 7, Remaining: 6}, false},
		{"public allowed, the IP's bucket the tighter", publicIP, []any{int64(1), int64(3), int64(0), "ip", int64(-1)}, Result{Allowed: true, Limit: 4, Remaining: 3}, false},
		{"protected allowed", protected, []any{int64(1), int64(2), int64(0), "consumer", int64(-1)}, Result{Allowed: true, Limit: 3, Remaining: 2}, false},
		{"allowed with a quota", quota, []any{int64(1), int64(2), int64(0), "consumer", int64(41)},
			Result{Allowed: true, Limit: 3, Remaining: 2, QuotaLimit: 100, QuotaRemaining: 41}, false},
		{"refused by the workspace", protected, []any{int64(0), int64(0), int64(800), "workspace", int64(-1)},
			Result{LimitedBy: BucketWorkspace, Limit: 7, RetryAfter: 800 * time.Millisecond}, false},
		{"refused by the consumer", protected, []any{int64(0), int64(0), int64(900), "consumer", int64(-1)},
			Result{LimitedBy: BucketConsumer, Limit: 3, RetryAfter: 900 * time.Millisecond}, false},
		{"refused by the quota", quota, []any{int64(0), int64(0), int64(5000), "quota", int64(0)},
			Result{LimitedBy: Quota, Limit: 100, RetryAfter: 5 * time.Second, QuotaLimit: 100}, false},
		{"too few values", public, []any{int64(1), int64(6), int64(0), ""}, Result{}, true},
		{"wrong types", public, []any{"1", int64(6), int64(0), "", int64(-1)}, Result{}, true},
		{"refused naming no check", public, []any{int64(0), int64(0), int64(5), "", int64(-1)}, Result{}, true},
		{"refused naming a check it doesn't have", public, []any{int64(0), int64(0), int64(5), "quota", int64(-1)}, Result{}, true},
	}
	for _, tc := range tests {
		checks, err := tc.req.checks()
		if err != nil {
			t.Fatalf("%s: %v", tc.name, err)
		}
		got, err := tc.req.result(tc.reply, checks)
		if (err != nil) != tc.bad || got != tc.want {
			t.Errorf("%s: result = %+v, %v; want %+v, error %v", tc.name, got, err, tc.want, tc.bad)
		}
	}
}

// The script guards itself against a caller passing wrong arguments.
func TestScriptRefusesMalformedArguments(t *testing.T) {
	_, rdb := newTestLimiter(t)
	ws := unique(t, "ws")
	for name, run := range map[string]func() error{
		"no keys":            func() error { return limits.Run(t.Context(), rdb, nil).Err() },
		"a key without kind": func() error { return limits.Run(t.Context(), rdb, []string{ws}).Err() },
		"an unknown kind": func() error {
			return limits.Run(t.Context(), rdb, []string{ws}, "planet", 1).Err()
		},
	} {
		if err := run(); err == nil {
			t.Errorf("%s: no error", name)
		}
	}
}

func TestRedisDownIsAnErrorWithinTheTimeout(t *testing.T) {
	rdb := redis.NewClient(&redis.Options{Addr: "127.0.0.1:1", MaxRetries: -1})
	defer rdb.Close()
	l := New(rdb)

	start := time.Now()
	_, err := l.Allow(t.Context(), Request{WorkspaceID: "w", WorkspaceLimit: 60, ConsumerID: "c1", ConsumerLimit: 60})

	if err == nil {
		t.Fatal("Allow without Redis = nil error")
	}
	if took := time.Since(start); took > time.Second {
		t.Errorf("took %s, want the 50ms timeout (Redis must never slow requests down)", took)
	}
}

// Time per Allow call (one round trip to Redis, both buckets). Run by hand
// with the stack up:
//
//	$env:REDIS_TEST_URL="redis://localhost:6379/15"; go test -run=^$ -bench=Allow ./internal/ratelimit
func BenchmarkAllow(b *testing.B) {
	l, _ := newTestLimiter(b)
	i := 0
	for b.Loop() {
		// Many consumers and workspaces, so the benchmark measures the round
		// trip, not denials.
		req := Request{
			WorkspaceID: fmt.Sprintf("bench-w%d", i%100), WorkspaceLimit: roomy,
			ConsumerID: fmt.Sprintf("bench-c%d", i%1000), ConsumerLimit: roomy,
		}
		if _, err := l.Allow(context.Background(), req); err != nil {
			b.Fatal(err)
		}
		i++
	}
}
