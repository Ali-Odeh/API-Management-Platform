package middleware

import (
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/usage"
)

type recordedEvents []usage.Event

func (r *recordedEvents) Record(e usage.Event) { *r = append(*r, e) }

func TestTheLoggerRecordsAUsageEventWithTheLogsFacts(t *testing.T) {
	var logs jsonLogger
	var events recordedEvents
	h := Logger(logs.logger(), &events)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		info := reqctx.From(r.Context())
		info.Workspace = &config.Workspace{ID: "ws1"}
		info.RouteID, info.TargetID, info.ConsumerID, info.KeyID = "r1", "t1", "c1", "k1"
		info.RateLimit, info.Upstream = RateLimitOK, 2*time.Millisecond
		w.WriteHeader(http.StatusCreated)
		_, _ = io.WriteString(w, "made")
	}))
	req := httptest.NewRequest("POST", "/v1/orders?secret=1", nil)
	req.RemoteAddr = "203.0.113.7:5555"
	req = req.WithContext(reqctx.With(req.Context(), &reqctx.Info{RequestID: "req-1"}))
	before := time.Now()

	h.ServeHTTP(httptest.NewRecorder(), req)

	if len(events) != 1 {
		t.Fatalf("%d events, want 1", len(events))
	}
	e := events[0]
	if e.WorkspaceID != "ws1" || e.RequestID != "req-1" || e.Method != "POST" || e.Path != "/v1/orders" ||
		e.RouteID != "r1" || e.TargetID != "t1" || e.ConsumerID != "c1" || e.KeyID != "k1" ||
		e.Status != http.StatusCreated || e.Bytes != 4 || e.ClientIP != "203.0.113.7" || e.RateLimit != RateLimitOK {
		t.Errorf("event %+v", e)
	}
	if e.UpstreamMS != 2 || e.Time.Before(before.Add(-time.Second)) || e.ID == "" {
		t.Errorf("times or id: %+v", e)
	}
	// The log line and the event describe the same request.
	if line := logs.lines(t)[0]; line["request_id"] != "req-1" || line["status"] != float64(201) {
		t.Errorf("log line %v", line)
	}
}

func TestARequestWithoutAWorkspaceIsOnlyLogged(t *testing.T) {
	var logs jsonLogger
	var events recordedEvents
	h := Logger(logs.logger(), &events)(http.NotFoundHandler()) // tenant never found one

	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/x", nil))

	if len(events) != 0 {
		t.Errorf("recorded %+v for a request without a workspace", events)
	}
	if line := logs.lines(t)[0]; line["status"] != float64(404) {
		t.Errorf("log line %v", line)
	}
}
