package config

import (
	"strings"
	"testing"
	"time"
)

// An answer shaped like the plan's example (PLAN.md section 6).
const validBody = `{
  "seq": 43,
  "full": false,
  "workspaces": [{
    "id": "ws1", "slug": "acme", "status": "active", "version": 18, "hosts": ["acme"],
    "limits": {"rate_limit_per_min": 6000, "max_concurrent": 200},
    "services": [
      {"id": "s1", "timeout_ms": 5000, "targets": [{"id": "s1", "url": "http://mock-backend:9000", "weight": 100}]},
      {"id": "s2", "timeout_ms": 250, "targets": [{"id": "s2", "url": "https://orders.internal/api", "weight": 100}],
       "health": {"path": "/health"}}
    ],
    "routes": [
      {"id": "r1", "service_id": "s1", "path_prefix": "/v1", "methods": ["GET"], "strip_prefix": false, "auth_required": true},
      {"id": "r2", "service_id": "s2", "path_prefix": "/v1/orders", "methods": ["GET", "POST"], "strip_prefix": true, "auth_required": false},
      {"id": "r3", "service_id": "s1", "path_prefix": "/v2", "methods": ["GET"], "strip_prefix": false, "auth_required": true}
    ],
    "keys": [
      {"id": "k1", "key_hash": "aaa", "consumer_id": "c1", "consumer_status": "active", "rate_limit_per_min": 60},
      {"id": "k2", "key_hash": "bbb", "consumer_id": "c2", "consumer_status": "blocked", "rate_limit_per_min": 10}
    ]
  }],
  "deleted": ["ws9"],
  "a_field_from_the_future": true
}`

func TestParseAnswer(t *testing.T) {
	a, skipped, err := parseAnswer(strings.NewReader(validBody))
	if err != nil {
		t.Fatalf("parseAnswer() error: %v", err)
	}
	if len(skipped) != 0 {
		t.Errorf("skipped = %v, want none", skipped)
	}
	if a.Seq != 43 || a.Full || len(a.Deleted) != 1 || a.Deleted[0] != "ws9" {
		t.Errorf("answer = seq %d full %v deleted %v; want 43, false, [ws9]", a.Seq, a.Full, a.Deleted)
	}
	if len(a.Workspaces) != 1 {
		t.Fatalf("got %d workspaces, want 1", len(a.Workspaces))
	}
	ws := a.Workspaces[0]
	if ws.ID != "ws1" || ws.Slug != "acme" || ws.Status != WorkspaceActive || ws.Version != 18 {
		t.Errorf("workspace = %+v", ws)
	}
	if len(ws.Hosts) != 1 || ws.Hosts[0] != "acme" {
		t.Errorf("Hosts = %v, want [acme]", ws.Hosts)
	}
	if ws.Limits != (Limits{RateLimitPerMin: 6000, MaxConcurrent: 200}) {
		t.Errorf("Limits = %+v", ws.Limits)
	}

	// Longest prefix first; equal lengths keep the control plane's order.
	var ids []string
	for _, r := range ws.Routes {
		ids = append(ids, r.ID)
	}
	if got := strings.Join(ids, ","); got != "r2,r1,r3" {
		t.Errorf("route order = %s, want r2,r1,r3", got)
	}

	// A route carries its service's target and timeout.
	orders := ws.Routes[0]
	if orders.ServiceID != "s2" || orders.Upstream.String() != "https://orders.internal/api" {
		t.Errorf("route r2 = service %s upstream %s", orders.ServiceID, orders.Upstream)
	}
	if orders.Timeout != 250*time.Millisecond {
		t.Errorf("Timeout = %s, want 250ms", orders.Timeout)
	}
	if !orders.StripPrefix || orders.AuthRequired {
		t.Errorf("flags = strip %v auth %v, want strip true auth false", orders.StripPrefix, orders.AuthRequired)
	}

	if k, ok := ws.Keys["bbb"]; !ok || k.ConsumerStatus != ConsumerBlocked || k.ID != "k2" {
		t.Errorf(`Keys["bbb"] = %+v, %v; want blocked key k2`, k, ok)
	}
	if len(ws.Keys) != 2 {
		t.Errorf("len(Keys) = %d, want 2", len(ws.Keys))
	}
}

func TestParseAnswerEmptyWorkspace(t *testing.T) {
	body := `{"seq": 1, "full": true, "workspaces": [{"id": "w", "slug": "default", "status": "suspended",
	  "hosts": ["default"], "services": [], "routes": [], "keys": []}], "deleted": []}`
	a, _, err := parseAnswer(strings.NewReader(body))
	if err != nil {
		t.Fatalf("parseAnswer() error: %v", err)
	}
	ws := a.Workspaces[0]
	if !a.Full || ws.Status != WorkspaceSuspended || len(ws.Routes) != 0 || ws.Keys == nil {
		t.Errorf("workspace = %+v, want a suspended one with no routes and a usable key map", ws)
	}
}

func TestParseAnswerRejectsUnreadableBodies(t *testing.T) {
	tests := []struct {
		name    string
		body    string
		wantErr string
	}{
		{name: "not JSON", body: "<html>oops</html>", wantErr: "invalid JSON"},
		{name: "cut off", body: `{"seq": 4, "workspaces": [`, wantErr: "invalid JSON"},
		{name: "wrong type", body: `{"seq": "four"}`, wantErr: "invalid JSON"},
		{name: "no seq", body: `{"full": true, "workspaces": []}`, wantErr: `missing "seq"`},
		// The MVP's answer: a gateway newer than its control plane says so clearly.
		{name: "old protocol", body: `{"version": 4, "routes": [], "keys": []}`, wantErr: `missing "seq"`},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			a, _, err := parseAnswer(strings.NewReader(tt.body))
			if err == nil || !strings.Contains(err.Error(), tt.wantErr) {
				t.Fatalf("parseAnswer() = %+v, %v; want an error mentioning %q", a, err, tt.wantErr)
			}
		})
	}
}

func TestParseAnswerSkipsOnlyBadEntries(t *testing.T) {
	const service = `{"id": "s", "timeout_ms": 100, "targets": [{"id": "s", "url": "http://up:1", "weight": 100}]}`
	const good = `{"id": "good", "service_id": "s", "path_prefix": "/ok", "methods": ["GET"]}`
	tests := []struct {
		name        string
		badService  string
		badRoute    string
		badKey      string
		wantSkipped string
	}{
		{name: "prefix without slash", badRoute: `{"id": "bad", "service_id": "s", "path_prefix": "x", "methods": ["GET"]}`, wantSkipped: "route bad"},
		{name: "no methods", badRoute: `{"id": "bad", "service_id": "s", "path_prefix": "/x", "methods": []}`, wantSkipped: "no methods"},
		{name: "unknown service", badRoute: `{"id": "bad", "service_id": "nope", "path_prefix": "/x", "methods": ["GET"]}`, wantSkipped: "not in the config"},
		{name: "service without target", badService: `{"id": "t", "timeout_ms": 100, "targets": []}`, wantSkipped: "no target"},
		{name: "relative target", badService: `{"id": "t", "timeout_ms": 100, "targets": [{"url": "up:1/x"}]}`, wantSkipped: "target url"},
		{name: "non-http target", badService: `{"id": "t", "timeout_ms": 100, "targets": [{"url": "ftp://up"}]}`, wantSkipped: "target url"},
		{name: "unparseable target", badService: `{"id": "t", "timeout_ms": 100, "targets": [{"url": "http://[::1"}]}`, wantSkipped: "target url"},
		{name: "zero timeout", badService: `{"id": "t", "timeout_ms": 0, "targets": [{"url": "http://up:1"}]}`, wantSkipped: "timeout_ms"},
		{name: "empty key hash", badKey: `{"id": "kbad", "key_hash": "", "consumer_status": "active"}`, wantSkipped: "key kbad"},
		{name: "unknown consumer status", badKey: `{"id": "kbad", "key_hash": "zzz", "consumer_status": "suspended"}`, wantSkipped: "consumer_status"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			services, routes := service, good
			keys := `{"id": "kgood", "key_hash": "h", "consumer_status": "active"}`
			if tt.badService != "" {
				services += "," + tt.badService
				routes += `,{"id": "bad", "service_id": "t", "path_prefix": "/x", "methods": ["GET"]}`
			}
			if tt.badRoute != "" {
				routes += "," + tt.badRoute
			}
			if tt.badKey != "" {
				keys += "," + tt.badKey
			}
			body := `{"seq": 9, "workspaces": [{"id": "w", "slug": "acme", "status": "active",
			  "services": [` + services + `], "routes": [` + routes + `], "keys": [` + keys + `]}]}`

			a, skipped, err := parseAnswer(strings.NewReader(body))
			if err != nil {
				t.Fatalf("parseAnswer() error: %v", err)
			}
			if len(skipped) != 1 || !strings.Contains(skipped[0].Error(), tt.wantSkipped) {
				t.Fatalf("skipped = %v, want one entry mentioning %q", skipped, tt.wantSkipped)
			}
			// The good entries still made it in.
			ws := a.Workspaces[0]
			if len(ws.Routes) != 1 || ws.Routes[0].ID != "good" || len(ws.Keys) != 1 {
				t.Errorf("workspace = %+v, want only the good route and key", ws)
			}
		})
	}
}

func TestParseAnswerSkipsBadWorkspaces(t *testing.T) {
	tests := []struct {
		name, workspace, wantSkipped string
	}{
		{name: "no id", workspace: `{"slug": "x", "status": "active"}`, wantSkipped: "no id or slug"},
		{name: "no slug", workspace: `{"id": "x", "status": "active"}`, wantSkipped: "no id or slug"},
		// Unknown means not served: left out, it answers 404 (MT.7).
		{name: "unknown status", workspace: `{"id": "x", "slug": "x", "status": "archived"}`, wantSkipped: "unknown status"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			body := `{"seq": 2, "full": true, "workspaces": [` + tt.workspace +
				`, {"id": "ok", "slug": "ok", "status": "active"}]}`
			a, skipped, err := parseAnswer(strings.NewReader(body))
			if err != nil {
				t.Fatalf("parseAnswer() error: %v", err)
			}
			if len(skipped) != 1 || !strings.Contains(skipped[0].Error(), tt.wantSkipped) {
				t.Fatalf("skipped = %v, want one entry mentioning %q", skipped, tt.wantSkipped)
			}
			if len(a.Workspaces) != 1 || a.Workspaces[0].ID != "ok" {
				t.Errorf("workspaces = %+v, want only the good one", a.Workspaces)
			}
		})
	}
}
