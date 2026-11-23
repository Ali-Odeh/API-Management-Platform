package metrics

import (
	"io"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/usage"
)

type fakeState struct{}

func (fakeState) Each(fn func(string, int)) { fn("ws1", 3) }
func (fakeState) EachTarget(fn func(string, string, string, bool)) {
	fn("ws1", "s1", "t1", true)
	fn("ws1", "s1", "t2", false)
}
func (fakeState) Seq() int64          { return 42 }
func (fakeState) LastSync() time.Time { return time.Unix(1_700_000_000, 0) }
func (fakeState) Written() int64      { return 10 }
func (fakeState) Dropped() int64      { return 2 }

type neverSynced struct{ fakeState }

func (neverSynced) LastSync() time.Time { return time.Time{} }

func scrape(t *testing.T, m *Metrics) string {
	t.Helper()
	rec := httptest.NewRecorder()
	m.Handler().ServeHTTP(rec, httptest.NewRequest("GET", "/metrics", nil))
	body, _ := io.ReadAll(rec.Body)
	return string(body)
}

func TestRequestsAndStateAreExposed(t *testing.T) {
	s := fakeState{}
	m := New(Sources{InFlight: s, Targets: s, Config: s, Usage: s})
	m.Record(usage.Event{WorkspaceID: "ws1", RouteID: "r1", TargetID: "t1", Status: 200, LatencyMS: 2, UpstreamMS: 1})
	m.Record(usage.Event{WorkspaceID: "ws1", Status: 404, LatencyMS: 0.1}) // no route matched
	m.Record(usage.Event{WorkspaceID: "ws1", RouteID: "r1", Status: 429, RateLimit: "limited_quota"})
	m.Record(usage.Event{WorkspaceID: "ws1", RouteID: "r1", Status: 429, RateLimit: "blocked_ip"})
	m.Record(usage.Event{WorkspaceID: "ws1", RouteID: "r1", Status: 200, RateLimit: "ok"}) // not a refusal
	m.Record(usage.Event{WorkspaceID: "ws1", RouteID: "r1", Status: 0})

	out := scrape(t, m)
	for _, want := range []string{
		`gateway_requests_total{route="r1",status="2xx",workspace="ws1"} 2`,
		`gateway_requests_total{route="none",status="4xx",workspace="ws1"} 1`,
		`gateway_requests_total{route="r1",status="other",workspace="ws1"} 1`,
		`gateway_ratelimit_refusals_total{reason="limited_quota",workspace="ws1"} 1`,
		`gateway_ratelimit_refusals_total{reason="blocked_ip",workspace="ws1"} 1`,
		`gateway_request_duration_seconds_count{workspace="ws1"} 6`,
		`gateway_upstream_duration_seconds_count{workspace="ws1"} 1`,
		`gateway_requests_in_flight{workspace="ws1"} 3`,
		`gateway_target_up{service="s1",target="t1",workspace="ws1"} 1`,
		`gateway_target_up{service="s1",target="t2",workspace="ws1"} 0`,
		`gateway_config_seq 42`,
		`gateway_config_last_sync_timestamp_seconds 1.7e+09`,
		`gateway_usage_events_written_total 10`,
		`gateway_usage_events_dropped_total 2`,
		`go_goroutines`,
	} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %s", want)
		}
	}
	if strings.Contains(out, `reason="ok"`) {
		t.Error("an allowed request counted as a refusal")
	}
}

func TestNoSyncYetIsZero(t *testing.T) {
	s := fakeState{}
	m := New(Sources{InFlight: s, Targets: s, Config: neverSynced{}, Usage: s})
	if out := scrape(t, m); !strings.Contains(out, "gateway_config_last_sync_timestamp_seconds 0") {
		t.Errorf("want 0 before the first sync:\n%s", out)
	}
}
