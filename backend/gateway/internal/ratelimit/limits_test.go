package ratelimit

import (
	"testing"
	"time"
)

// V2.2: the per-IP bucket, the monthly quota and the failed-key refusal,
// against a real Redis (skipped without REDIS_TEST_URL).

func TestPublicRoutesLimitEachClientIP(t *testing.T) {
	l, _ := newTestLimiter(t)
	ws := unique(t, "ws")
	req := func(ip string) Request {
		return Request{WorkspaceID: ws, WorkspaceLimit: roomy, ClientIP: ip, IPLimit: 2}
	}

	for range 2 {
		if res := allow(t, l, req("10.0.0.1")); !res.Allowed || res.Limit != 2 {
			t.Fatalf("%+v, want allowed, the IP's limit in the headers", res)
		}
	}
	res := allow(t, l, req("10.0.0.1"))
	if res.Allowed || res.LimitedBy != BucketIP || res.Limit != 2 || res.RetryAfter < 29*time.Second {
		t.Errorf("third from one IP: %+v, want refused by its bucket", res)
	}
	if res := allow(t, l, req("10.0.0.2")); !res.Allowed {
		t.Errorf("another IP: %+v, want allowed", res)
	}
}

func TestProtectedRoutesHaveNoIPBucket(t *testing.T) {
	l, rdb := newTestLimiter(t)
	req := consumerReq(t, unique(t, "c"), roomy)
	req.ClientIP, req.IPLimit = "10.0.0.9", 1

	for range 3 {
		if res := allow(t, l, req); !res.Allowed {
			t.Fatalf("%+v: a consumer is limited by its plan, not by its IP", res)
		}
	}
	if n, _ := rdb.Exists(t.Context(), ipKeyPrefix+req.WorkspaceID+":10.0.0.9").Result(); n != 0 {
		t.Error("an IP bucket was made for a protected route")
	}
}

func TestTheQuotaRefusesUntilTheMonthEnds(t *testing.T) {
	l, rdb := newTestLimiter(t)
	now := time.Date(2026, 11, 30, 23, 0, 0, 0, time.UTC)
	req := consumerReq(t, unique(t, "c"), roomy)
	req.MonthlyQuota, req.Now = 3, now

	for want := int64(2); want >= 0; want-- {
		res := allow(t, l, req)
		if !res.Allowed || res.QuotaLimit != 3 || res.QuotaRemaining != want {
			t.Fatalf("%+v, want allowed with %d left this month", res, want)
		}
	}
	res := allow(t, l, req)
	if res.Allowed || res.LimitedBy != Quota || res.Limit != 3 || res.QuotaRemaining != 0 {
		t.Fatalf("fourth: %+v, want refused by the quota", res)
	}
	if res.RetryAfter != time.Hour { // until December 1st, 00:00 UTC
		t.Errorf("retry after %s, want 1h (the month's end)", res.RetryAfter)
	}
	key := quotaKey(req.ConsumerID, now)
	if ttl := rdb.PTTL(t.Context(), key).Val(); ttl < 34*24*time.Hour || ttl > 35*24*time.Hour {
		t.Errorf("counter expires in %s, want about 35 days", ttl)
	}

	// A new month, a new counter.
	req.Now = now.Add(2 * time.Hour)
	if res := allow(t, l, req); !res.Allowed || res.QuotaRemaining != 2 {
		t.Errorf("in December: %+v, want allowed with 2 left", res)
	}
}

func TestARefusedRequestSpendsNoQuota(t *testing.T) {
	l, rdb := newTestLimiter(t)
	consumer := unique(t, "c")
	req := Request{WorkspaceID: unique(t, "ws"), WorkspaceLimit: 1, ConsumerID: consumer, ConsumerLimit: roomy, MonthlyQuota: 10}

	allow(t, l, req)
	if res := allow(t, l, req); res.Allowed || res.LimitedBy != BucketWorkspace || res.QuotaRemaining != 9 {
		t.Fatalf("%+v, want refused by the workspace with 9 of the quota left", res)
	}
	if used := rdb.Get(t.Context(), quotaKey(consumer, time.Now())).Val(); used != "1" {
		t.Errorf("quota counter %s, want 1: a refused request costs nothing", used)
	}
}

// 100 requests at the same moment against a quota of 50: exactly 50 pass,
// from any number of gateways (one atomic script each).
func TestTheQuotaIsExactUnderParallelRequests(t *testing.T) {
	l, rdb := newTestLimiter(t)
	consumer := unique(t, "c")
	ws := unique(t, "ws")

	allowed, _ := fire(t, l, 100, func(int) Request {
		return Request{WorkspaceID: ws, WorkspaceLimit: roomy, ConsumerID: consumer, ConsumerLimit: roomy, MonthlyQuota: 50}
	})

	if allowed[consumer] != 50 {
		t.Errorf("%d allowed, want exactly the quota's 50", allowed[consumer])
	}
	if used := rdb.Get(t.Context(), quotaKey(consumer, time.Now())).Val(); used != "50" {
		t.Errorf("quota counter %s, want 50", used)
	}
}

func TestAnIPWithTooManyWrongKeysIsRefused(t *testing.T) {
	l, _ := newTestLimiter(t)
	ws := unique(t, "ws")
	fail := Failure{WorkspaceID: ws, ClientIP: "10.0.0.7", Limit: 3}

	for i := 1; i <= 3; i++ {
		got, err := l.CountFailure(t.Context(), fail)
		if err != nil || got.Blocked {
			t.Fatalf("failure %d: %+v, %v; want counted, not blocked", i, got, err)
		}
	}
	got, err := l.CountFailure(t.Context(), fail)
	if err != nil || !got.Blocked || got.RetryAfter > AuthFailWindow || got.RetryAfter < AuthFailWindow-time.Minute {
		t.Fatalf("fourth failure: %+v, %v; want blocked until the window ends", got, err)
	}

	// Even a valid key from that IP is refused now; other IPs are not.
	valid := Request{WorkspaceID: ws, WorkspaceLimit: roomy, ConsumerID: unique(t, "c"), ConsumerLimit: roomy, ClientIP: "10.0.0.7", AuthFailLimit: 3}
	res := allow(t, l, valid)
	if res.Allowed || res.LimitedBy != AuthFail || res.Limit != 3 || res.RetryAfter < AuthFailWindow-time.Minute {
		t.Errorf("valid key from the blocked IP: %+v, want refused until the window ends", res)
	}
	valid.ClientIP = "10.0.0.8"
	if res := allow(t, l, valid); !res.Allowed {
		t.Errorf("another IP: %+v, want allowed", res)
	}
}

func TestCountFailureNeedsAWorkspaceAnIPAndALimit(t *testing.T) {
	l := New(nil) // never reached
	for _, f := range []Failure{
		{ClientIP: "1.2.3.4", Limit: 1},
		{WorkspaceID: "w", Limit: 1},
		{WorkspaceID: "w", ClientIP: "1.2.3.4"},
	} {
		if _, err := l.CountFailure(t.Context(), f); err == nil {
			t.Errorf("%+v: no error", f)
		}
	}
}

func TestMonthsAreUTC(t *testing.T) {
	// 01:30 on December 1st in UTC+3 is still November in UTC.
	plus3 := time.FixedZone("UTC+3", 3*3600)
	at := time.Date(2026, 12, 1, 1, 30, 0, 0, plus3)
	if got := quotaKey("c1", at); got != "quota:c1:202611" {
		t.Errorf("quotaKey = %s, want November's", got)
	}
	if got := untilNextMonth(at); got != 90*time.Minute {
		t.Errorf("untilNextMonth = %s, want 1h30m", got)
	}
}
