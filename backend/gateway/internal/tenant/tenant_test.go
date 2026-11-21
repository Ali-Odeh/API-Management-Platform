package tenant

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strings"
	"testing"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
)

func TestLabel(t *testing.T) {
	tests := []struct {
		host, base, want string
		ok               bool
	}{
		// A workspace subdomain.
		{"acme.gw.example.com", "gw.example.com", "acme", true},
		{"acme.gw.example.com:8443", "gw.example.com", "acme", true},
		{"ACME.GW.Example.com.", "gw.example.com", "acme", true}, // case and a trailing dot ignored
		{"acme.localhost:8081", "localhost", "acme", true},
		{"e2e-1.localhost", "localhost", "e2e-1", true},
		// No subdomain: the gateway's own names serve `default`.
		{"gw.example.com", "gw.example.com", "default", true},
		{"localhost:8081", "localhost", "default", true},
		{"gateway:8080", "localhost", "default", true},
		{"10.0.0.7", "localhost", "default", true},
		{"[::1]:8081", "localhost", "default", true},
		{"[::1]", "localhost", "default", true},
		{"", "localhost", "default", true}, // HTTP/1.0 without a Host
		{"acme.localhost.evil.com", "localhost", "default", true},
		// Under the base domain, but not one valid label: nobody's workspace.
		{"a.b.gw.example.com", "gw.example.com", "", false},
		{"-x.localhost", "localhost", "", false},
		{"x-.localhost", "localhost", "", false},
		{"a_b.localhost", "localhost", "", false},
		{".localhost", "localhost", "", false},
		{strings.Repeat("a", 64) + ".localhost", "localhost", "", false},
		// Not a host at all.
		{"a:b:c", "localhost", "", false},
		{":8081", "localhost", "", false},
	}
	for _, tt := range tests {
		got, ok := Label(tt.host, tt.base)
		if got != tt.want || ok != tt.ok {
			t.Errorf("Label(%q, %q) = %q, %v; want %q, %v", tt.host, tt.base, got, ok, tt.want, tt.ok)
		}
	}
}

// FuzzLabel checks Label against independent rules, with any Host a client
// could send: a workspace label is only ever one valid DNS label directly
// under the base domain, never anything that could name another tenant.
func FuzzLabel(f *testing.F) {
	for _, seed := range []string{"acme.localhost", "acme.localhost:8081", "localhost", "a.b.localhost",
		"ACME.localhost.", "[::1]:80", "x", "-a.localhost", "a..localhost", ".localhost", "a:b:c", ""} {
		f.Add(seed)
	}
	validLabel := regexp.MustCompile(`^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`)
	f.Fuzz(func(t *testing.T, host string) {
		label, ok := Label(host, "localhost")
		if !ok {
			if label != "" {
				t.Fatalf("Label(%q) = %q with ok=false", host, label)
			}
			return
		}
		if label == config.DefaultHost {
			return
		}
		if !validLabel.MatchString(label) {
			t.Fatalf("Label(%q) = %q, not a DNS label", host, label)
		}
		// The host (any case, port or trailing dot) really is <label>.localhost.
		name := strings.ToLower(host)
		if i := strings.LastIndex(name, ":"); i > 0 && !strings.HasSuffix(name, "]") {
			name = name[:i]
		}
		if strings.TrimSuffix(name, ".") != label+".localhost" {
			t.Fatalf("Label(%q) = %q, but the host is not %q", host, label, label+".localhost")
		}
	})
}

type fakeSource struct{ snap *config.Snapshot }

func (f fakeSource) Current() *config.Snapshot { return f.snap }

func testSnapshot() *config.Snapshot {
	return config.NewSnapshot(3,
		&config.Workspace{ID: "w-default", Slug: "default", Status: config.WorkspaceActive, Hosts: []string{"default"}},
		&config.Workspace{ID: "w-acme", Slug: "acme", Status: config.WorkspaceActive, Hosts: []string{"acme"}},
		&config.Workspace{ID: "w-banned", Slug: "banned", Status: config.WorkspaceSuspended, Hosts: []string{"banned"}},
	)
}

// serve runs one request through the middleware and returns the answer and
// what the next handler saw (nil if it wasn't called).
func serve(t *testing.T, snap *config.Snapshot, host string, header http.Header) (*httptest.ResponseRecorder, *http.Request) {
	t.Helper()
	var seen *http.Request
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { seen = r })
	req := httptest.NewRequest("GET", "/v1/orders", nil)
	req.Host = host
	for k, v := range header {
		req.Header[k] = v
	}
	info := &reqctx.Info{}
	req = req.WithContext(reqctx.With(req.Context(), info))
	rec := httptest.NewRecorder()

	Middleware(fakeSource{snap}, "localhost")(next).ServeHTTP(rec, req)
	return rec, seen
}

func errorOf(t *testing.T, rec *httptest.ResponseRecorder) string {
	t.Helper()
	var body map[string]string
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("body %q is not JSON: %v", rec.Body.String(), err)
	}
	return body["error"]
}

func TestMiddlewareFindsTheHostsWorkspace(t *testing.T) {
	for host, wantID := range map[string]string{"acme.localhost:8081": "w-acme", "localhost:8081": "w-default"} {
		// A client can't choose the workspace the backend is told about.
		rec, seen := serve(t, testSnapshot(), host, http.Header{"X-Workspace-Id": {"w-somebody-else"}})

		if seen == nil {
			t.Fatalf("%s: next not called (status %d)", host, rec.Code)
		}
		if got := reqctx.From(seen.Context()).Workspace; got == nil || got.ID != wantID {
			t.Errorf("%s: workspace on the notes card = %+v, want %s", host, got, wantID)
		}
		if got := seen.Header.Values(WorkspaceIDHeader); len(got) != 1 || got[0] != wantID {
			t.Errorf("%s: %s = %v, want only the trusted %s", host, WorkspaceIDHeader, got, wantID)
		}
	}
}

func TestMiddlewareAnswersItself(t *testing.T) {
	tests := []struct {
		name, host string
		snap       *config.Snapshot
		status     int
		code       string
		workspace  string // the workspace the log line shows, if any
	}{
		{name: "no config yet", host: "acme.localhost", snap: nil, status: 503, code: "config_not_loaded"},
		{name: "unknown workspace", host: "nobody.localhost", snap: testSnapshot(), status: 404, code: "workspace_not_found"},
		{name: "not a workspace label", host: "a.b.localhost", snap: testSnapshot(), status: 404, code: "workspace_not_found"},
		{name: "suspended workspace", host: "banned.localhost", snap: testSnapshot(), status: 403, code: "workspace_suspended", workspace: "w-banned"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var info *reqctx.Info
			next := http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
				t.Error("next was called for a request tenant should answer itself")
			})
			req := httptest.NewRequest("GET", "/v1/orders", nil)
			req.Host = tt.host
			info = &reqctx.Info{}
			req = req.WithContext(reqctx.With(req.Context(), info))
			rec := httptest.NewRecorder()

			Middleware(fakeSource{tt.snap}, "localhost")(next).ServeHTTP(rec, req)

			if rec.Code != tt.status || errorOf(t, rec) != tt.code {
				t.Errorf("answer = %d %q, want %d %q", rec.Code, rec.Body.String(), tt.status, tt.code)
			}
			if got := info.WorkspaceID(); got != tt.workspace {
				t.Errorf("workspace for the log = %q, want %q", got, tt.workspace)
			}
			if tt.status == 503 && rec.Header().Get("Retry-After") != "1" {
				t.Error("503 without Retry-After: 1")
			}
		})
	}
}

// Without a notes card in the context (no logger before it), tenant adds
// its own, so the steps after it still find the workspace.
func TestMiddlewareWorksWithoutALogger(t *testing.T) {
	var seen *http.Request
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { seen = r })
	req := httptest.NewRequest("GET", "/", nil)
	req.Host = "acme.localhost"

	Middleware(fakeSource{testSnapshot()}, "localhost")(next).ServeHTTP(httptest.NewRecorder(), req)

	if seen == nil || reqctx.From(seen.Context()).WorkspaceID() != "w-acme" {
		t.Error("the workspace didn't reach the next handler")
	}
}
