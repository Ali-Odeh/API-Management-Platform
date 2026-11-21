package middleware

import (
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/inflight"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
)

// inWorkspace sends a request as tenant would hand it on: with the
// workspace on its notes card.
func inWorkspace(h http.Handler, ws *config.Workspace) *httptest.ResponseRecorder {
	req := httptest.NewRequest("GET", "/x", nil)
	ctx := reqctx.With(req.Context(), &reqctx.Info{Workspace: ws})
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req.WithContext(ctx))
	return rec
}

func limitedWorkspace(id string, maxConcurrent int) *config.Workspace {
	return &config.Workspace{ID: id, Limits: config.Limits{RateLimitPerMin: 60, MaxConcurrent: maxConcurrent}}
}

func TestInFlightHoldsASlotWhileTheBackendAnswers(t *testing.T) {
	var slots inflight.Limiter
	ws := limitedWorkspace("ws1", 2)
	var during int
	h := InFlight(&slots, slog.New(slog.DiscardHandler))(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		during = slots.InFlight("ws1")
		w.WriteHeader(http.StatusOK)
	}))

	rec := inWorkspace(h, ws)

	if rec.Code != 200 || during != 1 {
		t.Errorf("status %d, %d in flight during the call; want 200 and 1", rec.Code, during)
	}
	if got := slots.InFlight("ws1"); got != 0 {
		t.Errorf("%d in flight after the answer, want 0", got)
	}
}

func TestInFlightFullWorkspaceIs503(t *testing.T) {
	var slots inflight.Limiter
	ws := limitedWorkspace("ws1", 1)
	held, _ := slots.Acquire("ws1", 1) // a slow request is running
	defer held.Release()
	h := InFlight(&slots, slog.New(slog.DiscardHandler))(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		t.Error("the backend was called for a full workspace")
	}))

	rec := inWorkspace(h, ws)

	if rec.Code != http.StatusServiceUnavailable || rec.Body.String() != `{"error":"workspace_overloaded"}`+"\n" {
		t.Errorf("status %d, body %q; want 503 workspace_overloaded", rec.Code, rec.Body.String())
	}
	if rec.Header().Get("Retry-After") != "1" {
		t.Errorf("Retry-After = %q, want 1", rec.Header().Get("Retry-After"))
	}
	if got := slots.InFlight("ws1"); got != 1 {
		t.Errorf("%d in flight, want 1: a refused request takes no slot", got)
	}
	// Another workspace is not affected.
	if rec := inWorkspace(InFlight(&slots, slog.New(slog.DiscardHandler))(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {})),
		limitedWorkspace("ws2", 1)); rec.Code != 200 {
		t.Errorf("another workspace got %d, want 200", rec.Code)
	}
}

// A panic further in still gives the slot back (Recover, outside, turns
// it into a 500); otherwise every panic would shrink the workspace's share.
func TestInFlightReleasesTheSlotOnPanic(t *testing.T) {
	var slots inflight.Limiter
	h := InFlight(&slots, slog.New(slog.DiscardHandler))(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		panic("boom")
	}))

	func() {
		defer func() { _ = recover() }()
		inWorkspace(h, limitedWorkspace("ws1", 1))
	}()

	if got := slots.InFlight("ws1"); got != 0 {
		t.Errorf("%d in flight after a panic, want 0", got)
	}
}

func TestInFlightWithoutAWorkspaceIs500(t *testing.T) {
	var logs jsonLogger
	var slots inflight.Limiter
	h := InFlight(&slots, logs.logger())(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		t.Error("request let through without a workspace")
	}))

	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest("GET", "/x", nil))

	if rec.Code != http.StatusInternalServerError || !strings.Contains(logs.buf.String(), "without a workspace") {
		t.Errorf("status %d, log %s; want 500 and an error line", rec.Code, logs.buf.String())
	}
}
