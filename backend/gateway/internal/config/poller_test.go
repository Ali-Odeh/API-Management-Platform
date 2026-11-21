package config

import (
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/settings"
)

// fakeControlPlane plays /internal/config. Every request is reported on
// requests, then held open (like a real long poll) until the test sends an
// answer with respond, or the gateway gives up on it.
type fakeControlPlane struct {
	server   *httptest.Server
	requests chan *http.Request
	answers  chan func(http.ResponseWriter)
}

func newFakeControlPlane(t *testing.T) *fakeControlPlane {
	t.Helper()
	cp := &fakeControlPlane{
		requests: make(chan *http.Request, 100),
		answers:  make(chan func(http.ResponseWriter)),
	}
	cp.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		cp.requests <- r.Clone(context.Background())
		select {
		case answer := <-cp.answers:
			answer(w)
		case <-r.Context().Done(): // the gateway cancelled (shutdown)
		}
	}))
	t.Cleanup(cp.server.Close)
	return cp
}

// respond answers the request that is currently held.
func (cp *fakeControlPlane) respond(status int, body string) {
	cp.answers <- func(w http.ResponseWriter) {
		w.WriteHeader(status)
		_, _ = io.WriteString(w, body)
	}
}

// dropConnection closes the held request without any answer.
func (cp *fakeControlPlane) dropConnection() {
	cp.answers <- func(http.ResponseWriter) { panic(http.ErrAbortHandler) }
}

// next waits for the gateway's next request.
func (cp *fakeControlPlane) next(t *testing.T) *http.Request {
	t.Helper()
	select {
	case r := <-cp.requests:
		return r
	case <-time.After(5 * time.Second):
		t.Fatal("the gateway sent no request within 5 s")
		return nil
	}
}

// startPoller runs a fast poller (tiny backoff, no minimum interval) against
// cp and stops it when the test ends. Run's return is checked there too.
func startPoller(t *testing.T, cp *fakeControlPlane) (*Store, context.CancelFunc) {
	t.Helper()
	cfg := settings.Settings{
		ControlPlaneURL: cp.server.URL,
		InternalToken:   "s3cret",
		ConfigWait:      7 * time.Second,
	}
	store := &Store{}
	p := NewPoller(cfg, store, slog.New(slog.DiscardHandler))
	p.backoff = backoff{min: time.Millisecond, max: 4 * time.Millisecond}
	p.minInterval = 0

	ctx, cancel := context.WithCancel(t.Context())
	done := make(chan struct{})
	go func() {
		p.Run(ctx)
		close(done)
	}()
	t.Cleanup(func() {
		cancel()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			t.Error("Run did not return within 5 s of cancel")
		}
	})
	return store, cancel
}

// fullBody is validBody as a full answer (every workspace) at seq.
func fullBody(seq string) string {
	body := strings.Replace(validBody, `"full": false`, `"full": true`, 1)
	return strings.Replace(body, `"seq": 43`, `"seq": `+seq, 1)
}

func since(r *http.Request) string { return r.URL.Query().Get("since") }

func TestPollerLoadsConfigThenAsksWhatChanged(t *testing.T) {
	cp := newFakeControlPlane(t)
	store, _ := startPoller(t, cp)

	first := cp.next(t)
	if first.URL.Path != "/internal/config" || first.URL.Query().Get("wait") != "7" || since(first) != "0" {
		t.Errorf("first request = %s, want /internal/config?since=0&wait=7", first.URL)
	}
	if got := first.Header.Get("X-Internal-Token"); got != "s3cret" {
		t.Errorf("X-Internal-Token = %q, want the configured token", got)
	}
	if store.Current() != nil {
		t.Fatal("store has a snapshot before any answer")
	}

	cp.respond(http.StatusOK, fullBody("43"))

	second := cp.next(t)
	if since(second) != "43" {
		t.Errorf("since = %q, want 43", since(second))
	}
	if seq := store.Current().Seq; seq != 43 {
		t.Errorf("store seq = %d, want 43", seq)
	}

	cp.respond(http.StatusNotModified, "")

	if third := cp.next(t); since(third) != "43" {
		t.Errorf("after 304, since = %q, want 43", since(third))
	}
}

// A partial answer is merged: the unchanged workspace is the same pointer.
func TestPollerMergesPartialAnswers(t *testing.T) {
	cp := newFakeControlPlane(t)
	store, _ := startPoller(t, cp)

	cp.next(t)
	cp.respond(http.StatusOK, fullBody("43"))
	cp.next(t)
	acme := store.Current().Workspaces["ws1"]

	cp.respond(http.StatusOK, `{"seq": 44, "full": false, "workspaces": [
	  {"id": "ws2", "slug": "globex", "status": "active", "version": 1, "hosts": ["globex"]}], "deleted": []}`)
	cp.next(t)

	snap := store.Current()
	if snap.Seq != 44 || len(snap.Workspaces) != 2 || snap.ByHost["globex"] == nil {
		t.Fatalf("snapshot = %+v, want acme and globex at seq 44", snap)
	}
	if snap.Workspaces["ws1"] != acme {
		t.Error("the unchanged workspace was rebuilt instead of reused")
	}
}

// An answer that doesn't fit what the gateway holds is not guessed at: the
// config stays, and the next request asks for everything.
func TestPollerAsksForEverythingAfterAProtocolError(t *testing.T) {
	cp := newFakeControlPlane(t)
	store, _ := startPoller(t, cp)

	cp.next(t)
	cp.respond(http.StatusOK, fullBody("43"))
	cp.next(t)
	cp.respond(http.StatusOK, `{"seq": 40, "full": false, "workspaces": [], "deleted": []}`)

	if r := cp.next(t); since(r) != "0" {
		t.Fatalf("after a partial answer behind ours, since = %q, want 0", since(r))
	}
	if seq := store.Current().Seq; seq != 43 {
		t.Errorf("store seq = %d, want 43 (kept)", seq)
	}
	cp.respond(http.StatusOK, fullBody("40"))
	if r := cp.next(t); since(r) != "40" {
		t.Errorf("after the full answer, since = %q, want 40", since(r))
	}
}

func TestPollerRefusesAPartialAnswerFirst(t *testing.T) {
	cp := newFakeControlPlane(t)
	store, _ := startPoller(t, cp)

	cp.next(t)
	cp.respond(http.StatusOK, validBody) // partial, but the gateway holds nothing yet

	if r := cp.next(t); since(r) != "0" {
		t.Errorf("since = %q, want 0", since(r))
	}
	if store.Current() != nil {
		t.Error("a partial answer was stored as the whole config")
	}
}

// Whatever goes wrong, the last good snapshot stays in use, and the poller
// keeps trying until a new one arrives.
func TestPollerKeepsConfigOnFailures(t *testing.T) {
	cp := newFakeControlPlane(t)
	store, _ := startPoller(t, cp)

	cp.next(t)
	cp.respond(http.StatusOK, fullBody("43"))

	failures := []struct {
		name   string
		answer func()
	}{
		{"server error", func() { cp.respond(http.StatusInternalServerError, "boom") }},
		{"wrong token", func() { cp.respond(http.StatusUnauthorized, `{"detail":"bad token"}`) }},
		{"broken JSON", func() { cp.respond(http.StatusOK, "<html>proxy error</html>") }},
		{"no seq", func() { cp.respond(http.StatusOK, `{"workspaces": []}`) }},
		{"dropped connection", cp.dropConnection},
	}
	for _, f := range failures {
		cp.next(t)
		f.answer()
		if seq := store.Current().Seq; seq != 43 {
			t.Fatalf("after %s: store seq = %d, want 43 (kept)", f.name, seq)
		}
	}

	// Still polling from the same sequence, and a new change is applied.
	r := cp.next(t)
	if since(r) != "43" {
		t.Errorf("since = %q, want 43", since(r))
	}
	cp.respond(http.StatusOK, strings.Replace(validBody, `"seq": 43`, `"seq": 44`, 1))
	cp.next(t)
	if seq := store.Current().Seq; seq != 44 {
		t.Errorf("store seq = %d, want 44", seq)
	}
}

// The control plane is the source of truth: after a database reset the
// sequence can go down in a full answer, and the gateway must follow.
func TestPollerAcceptsALowerSeqInAFullAnswer(t *testing.T) {
	cp := newFakeControlPlane(t)
	store, _ := startPoller(t, cp)

	cp.next(t)
	cp.respond(http.StatusOK, fullBody("43"))
	cp.next(t)
	cp.respond(http.StatusOK, `{"seq": 1, "full": true, "workspaces": [], "deleted": []}`)
	cp.next(t)

	if snap := store.Current(); snap.Seq != 1 || len(snap.Workspaces) != 0 {
		t.Errorf("snapshot = %+v, want an empty config at seq 1", snap)
	}
}

// Shutdown must not wait for a held request to time out (up to 60 s).
func TestPollerStopsDuringAHeldRequest(t *testing.T) {
	cp := newFakeControlPlane(t)
	_, cancel := startPoller(t, cp)

	cp.next(t) // the request is now held by the fake control plane
	cancel()   // the cleanup in startPoller fails the test if Run doesn't return
}

func TestPollerStopsDuringBackoff(t *testing.T) {
	cp := newFakeControlPlane(t)
	cfg := settings.Settings{ControlPlaneURL: cp.server.URL, InternalToken: "t", ConfigWait: time.Second}
	p := NewPoller(cfg, &Store{}, slog.New(slog.DiscardHandler))
	p.backoff = backoff{min: time.Hour, max: time.Hour} // the retry would be an hour away

	ctx, cancel := context.WithCancel(t.Context())
	done := make(chan struct{})
	go func() {
		p.Run(ctx)
		close(done)
	}()

	cp.next(t)
	cp.respond(http.StatusInternalServerError, "")
	time.Sleep(20 * time.Millisecond) // let it reach the backoff wait
	cancel()

	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("Run did not return during the backoff wait")
	}
}

// A control plane that answers instantly every time must not cause a busy loop.
func TestPollerRespectsMinimumInterval(t *testing.T) {
	var calls atomic.Int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusNotModified)
	}))
	defer srv.Close()

	cfg := settings.Settings{ControlPlaneURL: srv.URL, InternalToken: "t", ConfigWait: time.Second}
	p := NewPoller(cfg, &Store{}, slog.New(slog.DiscardHandler))
	p.minInterval = 50 * time.Millisecond

	ctx, cancel := context.WithTimeout(t.Context(), 300*time.Millisecond)
	defer cancel()
	p.Run(ctx)

	// 300 ms / 50 ms = about 6; without the floor it would be thousands.
	if n := calls.Load(); n < 2 || n > 10 {
		t.Errorf("requests in 300 ms = %d, want about 6", n)
	}
}

// A skipped entry is logged with its reason, and the rest is applied.
func TestApplyLogsSkippedEntries(t *testing.T) {
	var logs strings.Builder
	store := &Store{}
	cfg := settings.Settings{ControlPlaneURL: "http://cp", InternalToken: "t", ConfigWait: time.Second}
	p := NewPoller(cfg, store, slog.New(slog.NewJSONHandler(&logs, nil)))

	body := `{"seq": 5, "full": true, "workspaces": [{"id": "w", "slug": "default", "status": "active",
		"services": [], "routes": [{"id": "bad", "service_id": "nope", "path_prefix": "/x", "methods": ["GET"]}]}]}`
	if err := p.apply(strings.NewReader(body)); err != nil {
		t.Fatalf("apply() error: %v", err)
	}

	out := logs.String()
	for _, want := range []string{`"msg":"config entry skipped"`, "route bad", `"msg":"config updated"`, `"skipped":1`} {
		if !strings.Contains(out, want) {
			t.Errorf("logs do not contain %s:\n%s", want, out)
		}
	}
	if store.Current().Seq != 5 {
		t.Errorf("store seq = %d, want 5", store.Current().Seq)
	}
}

func TestNewPollerBuildsTheRequestURLAndTimeout(t *testing.T) {
	cfg := settings.Settings{ControlPlaneURL: "http://control-plane:8000", InternalToken: "t", ConfigWait: 30 * time.Second}
	p := NewPoller(cfg, &Store{}, slog.New(slog.DiscardHandler))

	if p.url != "http://control-plane:8000/internal/config" || p.wait != "30" {
		t.Errorf("url = %s, wait = %s", p.url, p.wait)
	}
	if p.client.Timeout != 40*time.Second {
		t.Errorf("client timeout = %s, want 40s (wait + margin)", p.client.Timeout)
	}
}
