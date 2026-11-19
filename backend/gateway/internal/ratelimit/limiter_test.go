package ratelimit

// Most of these tests need a real Redis: the atomic Lua script is the whole
// point, and a fake Redis would prove nothing. They use the URL in
// REDIS_TEST_URL (check.ps1 and CI set it; locally that is Docker's Redis,
// database 15, so real counters are never touched). Without it they skip.

import (
	"context"
	"fmt"
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
// may use it at the same time, so every test uses its own consumer IDs.
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

// consumer gives each test its own bucket names, so tests never share a
// bucket, whatever else runs against the same Redis.
func consumer(t testing.TB, name string) string {
	return fmt.Sprintf("test/%s/%s/%d", t.Name(), name, time.Now().UnixNano())
}

func allow(t *testing.T, l *Limiter, consumer string, limit int) Result {
	t.Helper()
	res, err := l.Allow(t.Context(), consumer, limit)
	if err != nil {
		t.Fatalf("Allow(%s, %d): %v", consumer, limit, err)
	}
	return res
}

func TestBurstUpToTheLimitThenDenied(t *testing.T) {
	c1 := consumer(t, "c1")
	l, _ := newTestLimiter(t)

	for want := 4; want >= 0; want-- { // a full bucket of 5
		res := allow(t, l, c1, 5)
		if !res.Allowed || res.Remaining != want || res.Limit != 5 || res.RetryAfter != 0 {
			t.Fatalf("request %d: %+v, want allowed with %d left", 5-want, res, want)
		}
	}

	res := allow(t, l, c1, 5)
	// 5 per minute = one token every 12 s.
	if res.Allowed || res.Remaining != 0 || res.RetryAfter <= 11*time.Second || res.RetryAfter > 12*time.Second {
		t.Errorf("6th request: %+v, want denied, retry in about 12s", res)
	}
}

func TestOneRequestPerMinute(t *testing.T) {
	c1 := consumer(t, "c1")
	l, _ := newTestLimiter(t)

	if res := allow(t, l, c1, 1); !res.Allowed || res.Remaining != 0 {
		t.Fatalf("first: %+v", res)
	}
	res := allow(t, l, c1, 1)
	if res.Allowed || res.RetryAfter < 59*time.Second || res.RetryAfter > time.Minute {
		t.Errorf("second: %+v, want denied, retry in about 60s", res)
	}
}

func TestBucketRefills(t *testing.T) {
	c1 := consumer(t, "c1")
	l, _ := newTestLimiter(t)
	const limit = 600 // 10 per second: one token every 100 ms

	// Empty the bucket (it refills a little while we drain it).
	for i := 0; allow(t, l, c1, limit).Allowed; i++ {
		if i > 2*limit {
			t.Fatal("the bucket never ran empty")
		}
	}
	res := allow(t, l, c1, limit)
	if res.Allowed || res.RetryAfter > 100*time.Millisecond {
		t.Fatalf("right after draining: %+v, want denied within 100ms", res)
	}

	time.Sleep(res.RetryAfter + 30*time.Millisecond)

	if res := allow(t, l, c1, limit); !res.Allowed {
		t.Errorf("after waiting RetryAfter: %+v, want allowed", res)
	}
}

func TestConsumersHaveSeparateBuckets(t *testing.T) {
	greedy, polite := consumer(t, "greedy"), consumer(t, "polite")
	l, _ := newTestLimiter(t)

	allow(t, l, greedy, 1) // empties greedy's bucket
	if res := allow(t, l, greedy, 1); res.Allowed {
		t.Fatal("greedy should be limited")
	}
	if res := allow(t, l, polite, 1); !res.Allowed {
		t.Error("another consumer was limited by greedy's bucket")
	}
}

func TestLoweredLimitAppliesAtOnce(t *testing.T) {
	c1 := consumer(t, "c1")
	l, _ := newTestLimiter(t)

	allow(t, l, c1, 60) // 59 left at the old limit
	if res := allow(t, l, c1, 10); !res.Allowed || res.Remaining != 9 || res.Limit != 10 {
		t.Errorf("after lowering to 10: %+v, want 9 left", res)
	}
}

func TestIdleBucketsExpire(t *testing.T) {
	c1 := consumer(t, "c1")
	l, rdb := newTestLimiter(t)

	allow(t, l, c1, 60)

	ttl, err := rdb.PTTL(t.Context(), "rl:"+c1).Result()
	if err != nil || ttl <= 110*time.Second || ttl > 120*time.Second {
		t.Errorf("TTL of the bucket = %s (err %v), want about 120s", ttl, err)
	}
}

// After a Redis restart the script cache is empty; go-redis must load it again.
func TestWorksAfterRedisForgetsTheScript(t *testing.T) {
	c1 := consumer(t, "c1")
	l, rdb := newTestLimiter(t)
	allow(t, l, c1, 60)

	if err := rdb.ScriptFlush(t.Context()).Err(); err != nil {
		t.Fatalf("SCRIPT FLUSH: %v", err)
	}

	if res := allow(t, l, c1, 60); !res.Allowed || res.Remaining != 58 {
		t.Errorf("after SCRIPT FLUSH: %+v, want allowed with 58 left", res)
	}
}

// The point of the Lua script: many requests at the same moment can never
// take more tokens than the bucket has. Run with -race in CI.
func TestConcurrentRequestsNeverOverspend(t *testing.T) {
	c1 := consumer(t, "c1")
	l, _ := newTestLimiter(t)
	const limit, requests = 50, 100

	var allowed atomic.Int64
	var wg sync.WaitGroup
	start := make(chan struct{})
	for range requests {
		wg.Go(func() {
			<-start // all goroutines fire together
			res, err := l.Allow(context.Background(), c1, limit)
			if err != nil {
				t.Errorf("Allow: %v", err)
				return
			}
			if res.Allowed {
				allowed.Add(1)
			}
		})
	}
	began := time.Now()
	close(start)
	wg.Wait()

	// 50 per minute refills one token every 1.2 s; a slower run may earn one.
	want := int64(limit)
	if time.Since(began) > 1200*time.Millisecond {
		want++
	}
	if got := allowed.Load(); got < limit || got > want {
		t.Errorf("%d of %d concurrent requests allowed, want exactly %d", got, requests, limit)
	}
}

func TestLimitBelowOneIsAnError(t *testing.T) {
	l := New(redis.NewClient(&redis.Options{Addr: "127.0.0.1:1"})) // never called
	if _, err := l.Allow(t.Context(), "c1", 0); err == nil {
		t.Error("Allow with limit 0 = nil error")
	}
}

func TestRedisDownIsAnErrorWithinTheTimeout(t *testing.T) {
	rdb := redis.NewClient(&redis.Options{Addr: "127.0.0.1:1", MaxRetries: -1})
	defer rdb.Close()
	l := New(rdb)

	start := time.Now()
	_, err := l.Allow(t.Context(), "c1", 60)

	if err == nil {
		t.Fatal("Allow without Redis = nil error")
	}
	if took := time.Since(start); took > time.Second {
		t.Errorf("took %s, want the 50ms timeout (Redis must never slow requests down)", took)
	}
}

// Time per Allow call (one round trip to Redis). Run by hand with the stack up:
//
//	$env:REDIS_TEST_URL="redis://localhost:6379/15"; go test -run=^$ -bench=Allow ./internal/ratelimit
func BenchmarkAllow(b *testing.B) {
	l, _ := newTestLimiter(b)
	i := 0
	for b.Loop() {
		// Many consumers, so the benchmark measures the round trip, not denials.
		if _, err := l.Allow(context.Background(), fmt.Sprintf("c%d", i%1000), 1_000_000); err != nil {
			b.Fatal(err)
		}
		i++
	}
}
