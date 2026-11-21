package middleware

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/proxy"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
)

const (
	activeKey  = "ak_live_0123456789abcdef0123456789abcdef"
	blockedKey = "ak_live_ffffffffffffffffffffffffffffffff"
)

type staticSource struct{ snap *config.Snapshot }

func (s staticSource) Current() *config.Snapshot { return s.snap }

// seen is what the backend side (the handler after Auth) received.
type seen struct {
	called     bool
	apiKey     string
	consumerID string
}

// newAuthGateway is router → Auth → a handler that records what it got,
// with one private and one public route and two keys.
func newAuthGateway(t *testing.T) (http.Handler, *seen) {
	t.Helper()
	snap := snapshotOf(&config.Workspace{
		Version: 1,
		Routes: []config.Route{
			{ID: "private", PathPrefix: "/private", Methods: []string{"GET"}, AuthRequired: true},
			{ID: "public", PathPrefix: "/public", Methods: []string{"GET"}, AuthRequired: false},
		},
		Keys: map[string]config.Key{
			hashAPIKey(activeKey):  {ID: "k1", ConsumerID: "c1", ConsumerStatus: config.ConsumerActive, RateLimitPerMin: 60},
			hashAPIKey(blockedKey): {ID: "k2", ConsumerID: "c2", ConsumerStatus: config.ConsumerBlocked, RateLimitPerMin: 60},
		},
	})
	got := &seen{}
	backend := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		*got = seen{called: true, apiKey: r.Header.Get(APIKeyHeader), consumerID: r.Header.Get(ConsumerIDHeader)}
		w.WriteHeader(http.StatusOK)
	})
	auth := Auth(slog.New(slog.DiscardHandler))
	return proxy.NewRouter(staticSource{snap}, auth(backend)), got
}

func TestAuth(t *testing.T) {
	tests := []struct {
		name         string
		path         string
		keys         []string // X-API-Key values sent; nil means none
		target       string   // overrides path, e.g. to put a key in the query
		fakeConsumer string   // a client-sent X-Consumer-ID
		wantStatus   int
		wantError    string
		wantConsumer string // X-Consumer-ID the backend gets
		wantLogged   [2]string
	}{
		{name: "valid key", path: "/private/x", keys: []string{activeKey}, wantStatus: 200, wantConsumer: "c1", wantLogged: [2]string{"c1", "k1"}},
		{name: "no key", path: "/private/x", wantStatus: 401, wantError: "missing_api_key"},
		{name: "blank key", path: "/private/x", keys: []string{"   "}, wantStatus: 401, wantError: "missing_api_key"},
		{name: "unknown key", path: "/private/x", keys: []string{"ak_live_00000000000000000000000000000000"}, wantStatus: 401, wantError: "invalid_api_key"},
		{name: "valid key with a space added", path: "/private/x", keys: []string{activeKey + " "}, wantStatus: 401, wantError: "invalid_api_key"},
		{name: "two keys at once", path: "/private/x", keys: []string{activeKey, activeKey}, wantStatus: 401, wantError: "invalid_api_key"},
		{name: "absurdly long key", path: "/private/x", keys: []string{strings.Repeat("a", 257)}, wantStatus: 401, wantError: "invalid_api_key"},
		{name: "key only in the query", target: "/private/x?api_key=" + activeKey, wantStatus: 401, wantError: "missing_api_key"},
		{name: "blocked consumer", path: "/private/x", keys: []string{blockedKey}, wantStatus: 403, wantError: "consumer_blocked", wantLogged: [2]string{"c2", "k2"}},
		{name: "fake consumer ID is replaced", path: "/private/x", keys: []string{activeKey}, fakeConsumer: "admin", wantStatus: 200, wantConsumer: "c1", wantLogged: [2]string{"c1", "k1"}},
		{name: "public route without key", path: "/public/x", wantStatus: 200},
		{name: "public route: key and fake ID removed", path: "/public/x", keys: []string{activeKey}, fakeConsumer: "admin", wantStatus: 200},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			gw, got := newAuthGateway(t)
			target := tt.target
			if target == "" {
				target = tt.path
			}
			req := httptest.NewRequest("GET", target, nil)
			for _, k := range tt.keys {
				req.Header.Add(APIKeyHeader, k)
			}
			if tt.fakeConsumer != "" {
				req.Header.Set(ConsumerIDHeader, tt.fakeConsumer)
			}
			info := &reqctx.Info{}
			req = req.WithContext(reqctx.With(req.Context(), info))
			rec := httptest.NewRecorder()

			gw.ServeHTTP(rec, req)

			if rec.Code != tt.wantStatus {
				t.Fatalf("status = %d, want %d (body %q)", rec.Code, tt.wantStatus, rec.Body.String())
			}
			if tt.wantError != "" {
				var body map[string]string
				_ = json.Unmarshal(rec.Body.Bytes(), &body)
				if body["error"] != tt.wantError {
					t.Errorf("error = %q, want %q", body["error"], tt.wantError)
				}
				if got.called {
					t.Error("the backend was called for a refused request")
				}
				wantChallenge := tt.wantStatus == 401
				if (rec.Header().Get("WWW-Authenticate") != "") != wantChallenge {
					t.Errorf("WWW-Authenticate = %q, want set only on 401", rec.Header().Get("WWW-Authenticate"))
				}
			} else {
				if got.apiKey != "" {
					t.Errorf("the backend received the API key %q", got.apiKey)
				}
				if got.consumerID != tt.wantConsumer {
					t.Errorf("backend X-Consumer-ID = %q, want %q", got.consumerID, tt.wantConsumer)
				}
			}
			if info.ConsumerID != tt.wantLogged[0] || info.KeyID != tt.wantLogged[1] {
				t.Errorf("logged consumer %q key %q, want %q %q", info.ConsumerID, info.KeyID, tt.wantLogged[0], tt.wantLogged[1])
			}
		})
	}
}

// The gateway must hash exactly like the control plane (app/core/security.py:
// hashlib.sha256(raw_key.encode()).hexdigest()). Reference value computed
// with Python.
func TestHashAPIKeyMatchesTheControlPlane(t *testing.T) {
	const want = "99504879fe6db456b3a561afca2d468395ea0c44cc1546bec9b77a68158ec9da"
	if got := hashAPIKey(activeKey); got != want {
		t.Errorf("hashAPIKey = %s, want %s", got, want)
	}
}

// Matching and auth use one snapshot: a key added in a newer version is not
// seen by a request that matched in the older one.
func TestAuthUsesTheRoutersSnapshot(t *testing.T) {
	old := snapshotOf(&config.Workspace{
		Version: 1,
		Routes:  []config.Route{{ID: "r", PathPrefix: "/", Methods: []string{"GET"}, AuthRequired: true}},
		Keys:    map[string]config.Key{},
	})
	source := &switchingSource{first: old, then: snapshotOf(&config.Workspace{
		Version: 2, Routes: old.Default().Routes,
		Keys: map[string]config.Key{hashAPIKey(activeKey): {ID: "k1", ConsumerID: "c1", ConsumerStatus: config.ConsumerActive}},
	})}
	gw := proxy.NewRouter(source, Auth(slog.New(slog.DiscardHandler))(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {})))
	req := httptest.NewRequest("GET", "/x", nil)
	req.Header.Set(APIKeyHeader, activeKey)
	rec := httptest.NewRecorder()

	gw.ServeHTTP(rec, req)

	if rec.Code != 401 {
		t.Errorf("status = %d, want 401: auth must use version 1, which the router used", rec.Code)
	}
}

// switchingSource returns first once, then a newer snapshot: as if the
// config changed right after the router read it.
type switchingSource struct {
	first, then *config.Snapshot
	calls       int
}

func (s *switchingSource) Current() *config.Snapshot {
	s.calls++
	if s.calls == 1 {
		return s.first
	}
	return s.then
}

func TestAuthWithoutRouterIs500(t *testing.T) {
	var logs jsonLogger
	rec := httptest.NewRecorder()

	Auth(logs.logger())(http.NotFoundHandler()).ServeHTTP(rec, httptest.NewRequest("GET", "/x", nil))

	if rec.Code != 500 || !strings.Contains(logs.buf.String(), "without a matched route") {
		t.Errorf("status %d, logs %q", rec.Code, logs.buf.String())
	}
}

func TestLoggerShowsConsumerAndKey(t *testing.T) {
	var logs jsonLogger
	h := Logger(logs.logger())(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		info := reqctx.From(r.Context())
		info.ConsumerID, info.KeyID = "c1", "k1"
	}))

	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/x", nil))

	l := logs.lines(t)[0]
	if l["consumer_id"] != "c1" || l["key_id"] != "k1" {
		t.Errorf("log line = %v", l)
	}
}

// snapshotOf is a config with ws as its `default` workspace (until MT.7
// every request is served from it).
func snapshotOf(ws *config.Workspace) *config.Snapshot {
	ws.ID, ws.Slug, ws.Status = "ws-default", config.DefaultHost, config.WorkspaceActive
	ws.Hosts = []string{config.DefaultHost}
	return config.NewSnapshot(ws.Version, ws)
}
