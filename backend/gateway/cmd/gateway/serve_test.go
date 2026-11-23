package main

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/ratelimit"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/settings"
	"github.com/redis/go-redis/v9"
)

// freeAddr returns a local address nothing listens on right now.
func freeAddr(t *testing.T) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	addr := ln.Addr().String()
	ln.Close()
	return addr
}

// servedConsumer is the consumer behind testKey in fakeControlPlane's
// snapshot. Its own name, so its rate-limit bucket in the shared test Redis
// never collides with another package's tests.
const servedConsumer = "serve-test-consumer"

// servedWorkspace is the workspace of fakeControlPlane's snapshot, named for
// the same reason: its bucket (MT.8) is its own.
const servedWorkspace = "serve-test-workspace"

// fakeControlPlane answers the first /internal/config with a snapshot (one
// protected route to backendURL, one key whose consumer may make limit
// requests per minute), then holds every later request open like a real
// long poll. A wrong token gets 401.
func fakeControlPlane(t *testing.T, backendURL string, limit int) *httptest.Server {
	t.Helper()
	body := fmt.Sprintf(`{"seq": 1, "full": true, "deleted": [], "workspaces": [{
		"id": %q, "slug": "default", "status": "active", "version": 1, "hosts": ["default"],
		"limits": {"rate_limit_per_min": 6000, "max_concurrent": 200},
		"services": [{"id": "s1", "timeout_ms": 5000, "targets": [{"id": "s1", "url": %q, "weight": 100}]}],
		"routes": [{"id": "r1", "service_id": "s1", "path_prefix": "/v1/orders", "methods": ["GET"],
		            "strip_prefix": false, "auth_required": true}],
		"keys": [{"id": "k1", "key_hash": %q, "consumer_id": %q, "consumer_status": "active", "rate_limit_per_min": %d}]}]}`,
		servedWorkspace, backendURL, sha256Hex(testKey), servedConsumer, limit)
	served := make(chan struct{}, 1)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Internal-Token") != "s3cret" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		select {
		case served <- struct{}{}: // first request: the full config
			w.Header().Set("ETag", `"1"`)
			_, _ = io.WriteString(w, body)
		default: // later: nothing changes, hold until the gateway stops
			<-r.Context().Done()
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

// The real program, start to stop: settings → poller loads the config from
// the control plane → server answers through the whole chain → clean stop.
func TestServeWholeGateway(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, "order for "+r.Header.Get("X-Consumer-ID"))
	}))
	defer backend.Close()
	cp := fakeControlPlane(t, backend.URL, 60)

	cfg := settings.Settings{
		ListenAddr:      freeAddr(t),
		ShutdownTimeout: 2 * time.Second,
		ControlPlaneURL: cp.URL,
		InternalToken:   "s3cret", Egress: testBackends,
		ConfigWait: time.Second,
		// Nothing listens here: Redis is "down", so the rate limit fails open
		// and requests still get through.
		RedisURL: "redis://127.0.0.1:1/0",
	}
	ctx, cancel := context.WithCancel(t.Context())
	exit := make(chan int, 1)
	go func() { exit <- serve(ctx, cfg, slog.New(slog.DiscardHandler)) }()

	gateway := "http://" + cfg.ListenAddr
	call := func(key string) (int, string) {
		req, _ := http.NewRequest("GET", gateway+"/v1/orders/7", nil)
		if key != "" {
			req.Header.Set("X-API-Key", key)
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			return 0, err.Error()
		}
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		return resp.StatusCode, string(b)
	}

	// Up and healthy (Docker's check), then 503 until the config arrives,
	// then 200: retry briefly.
	var status int
	var body string
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
		if status, body = call(testKey); status == http.StatusOK {
			break
		}
	}
	if status != http.StatusOK || body != "order for "+servedConsumer {
		t.Fatalf("with a key: %d %q, want 200 from the backend for the served consumer", status, body)
	}
	if code := healthcheck(cfg); code != 0 {
		t.Errorf("healthcheck() = %d while running, want 0", code)
	}
	if status, _ := call(""); status != http.StatusUnauthorized {
		t.Errorf("without a key: %d, want 401", status)
	}

	// Go's client sometimes opens a spare connection that never carries a
	// request; Go's server counts such a connection as busy for up to 5 s
	// during shutdown, longer than this test's 2 s. Close our idle ones first,
	// as a client that is done would.
	http.DefaultClient.CloseIdleConnections()
	cancel() // like docker stop
	select {
	case code := <-exit:
		if code != 0 {
			t.Errorf("serve() = %d after a clean stop, want 0", code)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("serve() did not stop within 5 s (is the held long poll stopping it?)")
	}
	if code := healthcheck(cfg); code != 1 {
		t.Errorf("healthcheck() = %d after stop, want 1", code)
	}
}

// If the server can't start, serve fails fast and the poller stops too.
func TestServeFailsWhenThePortIsTaken(t *testing.T) {
	busy, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	defer busy.Close()
	cp := fakeControlPlane(t, "http://127.0.0.1:1", 60)

	cfg := settings.Settings{
		ListenAddr: busy.Addr().String(), ShutdownTimeout: time.Second,
		ControlPlaneURL: cp.URL, InternalToken: "s3cret", Egress: testBackends, ConfigWait: time.Second,
		RedisURL: "redis://127.0.0.1:1/0",
	}
	exit := make(chan int, 1)
	go func() { exit <- serve(t.Context(), cfg, slog.New(slog.DiscardHandler)) }()

	select {
	case code := <-exit:
		if code != 1 {
			t.Errorf("serve() = %d, want 1", code)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("serve() kept running although the port was taken")
	}
}

// clearBucket deletes a rate-limit key before a test. The client has the
// gateway's 50 ms timeouts, and on a busy machine the first connection to
// Docker's Redis can take longer, so setup retries for up to about 2 s
// instead of failing the test before it starts.
func clearBucket(t *testing.T, rdb *redis.Client, key string) {
	t.Helper()
	var err error
	for range 40 {
		if err = rdb.Del(t.Context(), key).Err(); err == nil {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("Redis not reachable: %v", err)
}

// The real program with a real Redis: a consumer allowed 3 requests per
// minute gets 3 answers, then 429. Skipped without REDIS_TEST_URL.
func TestServeRateLimitsWithRealRedis(t *testing.T) {
	redisURL := os.Getenv("REDIS_TEST_URL")
	if redisURL == "" {
		t.Skip("REDIS_TEST_URL not set")
	}
	rdb, err := ratelimit.NewClient(redisURL)
	if err != nil {
		t.Fatal(err)
	}
	defer rdb.Close()
	// A fresh bucket for this consumer only: other packages' tests may use
	// the same Redis database at the same time.
	clearBucket(t, rdb, "rl:"+servedConsumer)
	clearBucket(t, rdb, "rlws:"+servedWorkspace)

	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, "ok")
	}))
	defer backend.Close()
	cfg := settings.Settings{
		ListenAddr: freeAddr(t), ShutdownTimeout: 2 * time.Second,
		ControlPlaneURL: fakeControlPlane(t, backend.URL, 3).URL, InternalToken: "s3cret", Egress: testBackends,
		ConfigWait: time.Second, RedisURL: settings.Secret(redisURL),
	}
	ctx, cancel := context.WithCancel(t.Context())
	exit := make(chan int, 1)
	go func() { exit <- serve(ctx, cfg, slog.New(slog.DiscardHandler)) }()
	defer func() {
		http.DefaultClient.CloseIdleConnections()
		cancel()
		<-exit
	}()

	call := func() *http.Response {
		req, _ := http.NewRequest("GET", "http://"+cfg.ListenAddr+"/v1/orders/1", nil)
		req.Header.Set("X-API-Key", testKey)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			return nil
		}
		resp.Body.Close()
		return resp
	}
	// Wait for the config: 503 until it arrives.
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
		if resp := call(); resp != nil && resp.StatusCode != http.StatusServiceUnavailable {
			break
		}
	}

	// When Redis is slower than the 50 ms limit (other packages' tests may
	// hammer the same Redis right now), the gateway correctly fails open: a
	// 200 without X-RateLimit-* headers. Such a round proves nothing, so it
	// is tried again with a fresh bucket.
	for attempt := 1; attempt <= 3; attempt++ {
		if err := rdb.Del(t.Context(), "rl:"+servedConsumer, "rlws:"+servedWorkspace).Err(); err != nil {
			t.Fatalf("reset bucket: %v", err)
		}
		var statuses []int
		failedOpen := false
		for range 4 {
			resp := call()
			if resp == nil {
				t.Fatal("the gateway did not answer")
			}
			if resp.StatusCode == http.StatusOK && resp.Header.Get("X-RateLimit-Remaining") == "" {
				failedOpen = true
			}
			statuses = append(statuses, resp.StatusCode)
		}
		if failedOpen {
			t.Logf("attempt %d: Redis was slower than 50 ms, the gateway failed open; trying again", attempt)
			continue
		}
		if fmt.Sprint(statuses) != "[200 200 200 429]" {
			t.Errorf("statuses = %v, want [200 200 200 429]", statuses)
		}
		return
	}
	t.Fatal("Redis was slower than 50 ms in every attempt")
}

// The real program writes a usage event for each request to the Redis
// stream (V2.1), the last ones while it stops included. Skipped without
// REDIS_TEST_URL.
func TestServeWritesUsageEventsToRedis(t *testing.T) {
	redisURL := os.Getenv("REDIS_TEST_URL")
	if redisURL == "" {
		t.Skip("REDIS_TEST_URL not set")
	}
	rdb, err := ratelimit.NewClient(redisURL)
	if err != nil {
		t.Fatal(err)
	}
	defer rdb.Close()
	// The rate-limit test above drains this consumer's bucket.
	clearBucket(t, rdb, "rl:"+servedConsumer)
	clearBucket(t, rdb, "rlws:"+servedWorkspace)
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, "ok")
	}))
	defer backend.Close()
	cfg := settings.Settings{
		ListenAddr: freeAddr(t), ShutdownTimeout: 2 * time.Second,
		ControlPlaneURL: fakeControlPlane(t, backend.URL, 1000).URL, InternalToken: "s3cret", Egress: testBackends,
		ConfigWait: time.Second, RedisURL: settings.Secret(redisURL),
	}
	ctx, cancel := context.WithCancel(t.Context())
	exit := make(chan int, 1)
	go func() { exit <- serve(ctx, cfg, slog.New(slog.DiscardHandler)) }()

	// Its own request ids: other runs' entries may be in the shared stream.
	run := fmt.Sprintf("usage-test-%d", time.Now().UnixNano())
	call := func(i int) int {
		req, _ := http.NewRequest("GET", "http://"+cfg.ListenAddr+"/v1/orders/1", nil)
		req.Header.Set("X-API-Key", testKey)
		req.Header.Set("X-Request-ID", fmt.Sprintf("%s-%d", run, i))
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			return 0
		}
		resp.Body.Close()
		return resp.StatusCode
	}
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
		if status := call(0); status != 0 && status != http.StatusServiceUnavailable {
			break
		}
	}
	for i := 1; i <= 3; i++ {
		if status := call(i); status != http.StatusOK {
			t.Fatalf("request %d: %d", i, status)
		}
	}
	http.DefaultClient.CloseIdleConnections()
	cancel()
	if code := <-exit; code != 0 {
		t.Fatalf("exit code %d", code)
	}

	// Events are flushed every second; stopping writes what was queued, so
	// they are all there as soon as serve has returned.
	entries, err := rdb.XRevRangeN(context.Background(), "usage:events", "+", "-", 1000).Result()
	if err != nil {
		t.Fatal(err)
	}
	found := map[string]redis.XMessage{}
	for _, e := range entries {
		if id, _ := e.Values["request_id"].(string); len(id) > len(run) && id[:len(run)] == run {
			found[id] = e
		}
	}
	for i := 1; i <= 3; i++ {
		e, ok := found[fmt.Sprintf("%s-%d", run, i)]
		if !ok {
			t.Errorf("no usage event for request %d", i)
			continue
		}
		if e.Values["workspace_id"] != servedWorkspace || e.Values["consumer_id"] != servedConsumer ||
			e.Values["route_id"] != "r1" || e.Values["status"] != "200" {
			t.Errorf("request %d: event %v", i, e.Values)
		}
		rdb.XDel(context.Background(), "usage:events", e.ID)
	}
}

// The real program serves /metrics on its own port (V2.3), not on the API's.
func TestServeExposesMetricsOnTheirOwnPort(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, "ok")
	}))
	defer backend.Close()
	cfg := settings.Settings{
		ListenAddr: freeAddr(t), MetricsAddr: freeAddr(t), ShutdownTimeout: 2 * time.Second,
		ControlPlaneURL: fakeControlPlane(t, backend.URL, 1000).URL, InternalToken: "s3cret", Egress: testBackends,
		ConfigWait: time.Second, RedisURL: "redis://127.0.0.1:1/0", // Redis down: fail open
	}
	ctx, cancel := context.WithCancel(t.Context())
	exit := make(chan int, 1)
	go func() { exit <- serve(ctx, cfg, slog.New(slog.DiscardHandler)) }()
	defer func() {
		http.DefaultClient.CloseIdleConnections()
		cancel()
		<-exit
	}()

	get := func(url string, key bool) (int, string) {
		req, _ := http.NewRequest("GET", url, nil)
		if key {
			req.Header.Set("X-API-Key", testKey)
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			return 0, ""
		}
		defer resp.Body.Close()
		body, _ := io.ReadAll(resp.Body)
		return resp.StatusCode, string(body)
	}
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
		if status, _ := get("http://"+cfg.ListenAddr+"/v1/orders/1", true); status == http.StatusOK {
			break
		}
	}

	status, body := get("http://"+cfg.MetricsAddr+"/metrics", false)
	if status != http.StatusOK || !strings.Contains(body, `gateway_requests_total{route="r1",status="2xx",workspace="`+servedWorkspace+`"}`) ||
		!strings.Contains(body, "gateway_config_seq 1") {
		t.Errorf("metrics %d:\n%s", status, body)
	}
	if status, _ := get("http://"+cfg.ListenAddr+"/metrics", false); status == http.StatusOK {
		t.Error("/metrics answered on the public port")
	}
}
