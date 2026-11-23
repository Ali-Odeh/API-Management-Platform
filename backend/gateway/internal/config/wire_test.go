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
      {"id": "s2", "timeout_ms": 250, "lb_algorithm": "round_robin",
       "targets": [{"id": "t1", "url": "https://orders.internal/api", "weight": 100},
                   {"id": "t2", "url": "http://orders-2:9000", "weight": 50}],
       "health": {"path": "/healthz", "interval_s": 10, "unhealthy": 3, "healthy": 2,
                  "passive_failures": 5, "passive_eject_s": 30, "a_setting_from_the_future": 1}}
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

	// A route points to its service: targets, timeout and health settings.
	orders := ws.Routes[0]
	svc := orders.Service
	if orders.ServiceID != "s2" || svc == nil || svc.ID != "s2" || svc.WorkspaceID != "ws1" {
		t.Fatalf("route r2 = service %s, %+v", orders.ServiceID, svc)
	}
	if svc.Timeout != 250*time.Millisecond {
		t.Errorf("Timeout = %s, want 250ms", svc.Timeout)
	}
	if len(svc.Targets) != 2 || svc.Targets[0].ID != "t1" || svc.Targets[0].URL.String() != "https://orders.internal/api" ||
		svc.Targets[1].Weight != 50 {
		t.Errorf("Targets = %+v", svc.Targets)
	}
	want := Health{Path: "/healthz", Interval: 10 * time.Second, Unhealthy: 3, Healthy: 2, PassiveFailures: 5, PassiveEject: 30 * time.Second}
	if svc.Health != want {
		t.Errorf("Health = %+v, want %+v", svc.Health, want)
	}
	// Without health settings (a control plane before MT.10): no checks.
	if s1 := ws.Routes[1].Service; s1.Health != (Health{}) || s1.Fingerprint == "" {
		t.Errorf("service s1 = %+v", s1)
	}
	if len(ws.Services) != 2 || ws.Services[0].ID != "s1" || ws.Routes[1].Service != ws.Routes[2].Service {
		t.Errorf("Services = %v; routes of one service must share it", ws.Services)
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
"limits": {"rate_limit_per_min": 60, "max_concurrent": 10}, 	  "hosts": ["default"], "services": [], "routes": [], "keys": []}], "deleted": []}`
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
		{name: "missing workspaces", body: `{"seq": 4, "full": false, "deleted": []}`, wantErr: "incomplete"},
		{name: "extra data", body: validBody + " {}", wantErr: "data after config answer"},
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
		{name: "service without target", badService: `{"id": "t", "timeout_ms": 100, "targets": []}`, wantSkipped: "no usable target"},
		{name: "service without id", badService: `{"timeout_ms": 100, "targets": [{"id": "t1", "url": "http://up:1", "weight": 1}]}`, wantSkipped: "no id"},
		{name: "relative target", badService: `{"id": "t", "timeout_ms": 100, "targets": [{"id": "t1", "url": "up:1/x", "weight": 1}]}`, wantSkipped: "not an absolute"},
		{name: "non-http target", badService: `{"id": "t", "timeout_ms": 100, "targets": [{"id": "t1", "url": "ftp://up", "weight": 1}]}`, wantSkipped: "not an absolute"},
		{name: "unparseable target", badService: `{"id": "t", "timeout_ms": 100, "targets": [{"id": "t1", "url": "http://[::1", "weight": 1}]}`, wantSkipped: "not an absolute"},
		{name: "target without id", badService: `{"id": "t", "timeout_ms": 100, "targets": [{"url": "http://up:1", "weight": 1}]}`, wantSkipped: "target :"},
		{name: "weight 0", badService: `{"id": "t", "timeout_ms": 100, "targets": [{"id": "t1", "url": "http://up:1", "weight": 0}]}`, wantSkipped: "weight 0"},
		{name: "weight over 1000", badService: `{"id": "t", "timeout_ms": 100, "targets": [{"id": "t1", "url": "http://up:1", "weight": 1001}]}`, wantSkipped: "weight 1001"},
		{name: "zero timeout", badService: `{"id": "t", "timeout_ms": 0, "targets": [{"id": "t1", "url": "http://up:1", "weight": 1}]}`, wantSkipped: "timeout_ms"},
		{name: "unknown algorithm", badService: `{"id": "t", "timeout_ms": 100, "lb_algorithm": "random", "targets": [{"id": "t1", "url": "http://up:1", "weight": 1}]}`, wantSkipped: "lb_algorithm"},
		{name: "health path", badService: `{"id": "t", "timeout_ms": 100, "health": {"path": "health", "interval_s": 1, "unhealthy": 1, "healthy": 1, "passive_failures": 1, "passive_eject_s": 1},
			"targets": [{"id": "t1", "url": "http://up:1", "weight": 1}]}`, wantSkipped: "health path"},
		{name: "health interval 0", badService: `{"id": "t", "timeout_ms": 100, "health": {"path": "/h", "interval_s": 0, "unhealthy": 1, "healthy": 1, "passive_failures": 1, "passive_eject_s": 1},
			"targets": [{"id": "t1", "url": "http://up:1", "weight": 1}]}`, wantSkipped: "active health"},
		{name: "passive eject 0", badService: `{"id": "t", "timeout_ms": 100, "health": {"path": "/h", "interval_s": 1, "unhealthy": 1, "healthy": 1, "passive_failures": 1, "passive_eject_s": 0},
			"targets": [{"id": "t1", "url": "http://up:1", "weight": 1}]}`, wantSkipped: "passive health"},
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
			body := `{"seq": 9, "full": false, "workspaces": [{"id": "w", "slug": "acme", "status": "active",
"limits": {"rate_limit_per_min": 60, "max_concurrent": 10}, 			  "services": [` + services + `], "routes": [` + routes + `], "keys": [` + keys + `]}], "deleted": []}`

			a, skipped, err := parseAnswer(strings.NewReader(body))
			if err != nil {
				t.Fatalf("parseAnswer() error: %v", err)
			}
			// A bad target also leaves its service unusable, and so its route:
			// one cause, a few lines. The first names the cause.
			if len(skipped) == 0 || !strings.Contains(skipped[0].Error(), tt.wantSkipped) {
				t.Fatalf("skipped = %v, want the first mentioning %q", skipped, tt.wantSkipped)
			}
			// The good entries still made it in.
			ws := a.Workspaces[0]
			if len(ws.Routes) != 1 || ws.Routes[0].ID != "good" || len(ws.Keys) != 1 {
				t.Errorf("workspace = %+v, want only the good route and key", ws)
			}
		})
	}
}

// A workspace that can't be read is dropped (not served), and only it:
// the others in the answer still apply.
func TestParseAnswerDropsOnlyBadWorkspaces(t *testing.T) {
	tests := []struct {
		name, workspace, wantSkipped string
	}{
		{name: "no slug", workspace: `{"id": "x", "status": "active"}`, wantSkipped: "no slug"},
		{name: "unknown status", workspace: `{"id": "x", "slug": "x", "status": "archived"}`, wantSkipped: "unknown status"},
		{name: "no status", workspace: `{"id": "x", "slug": "x"}`, wantSkipped: "unknown status"},
		// The limits protect the other workspaces from this one (MT.8).
		{name: "no limits", workspace: `{"id": "x", "slug": "x", "status": "active"}`, wantSkipped: "invalid limits"},
		{name: "rate limit 0", workspace: `{"id": "x", "slug": "x", "status": "active",
			"limits": {"rate_limit_per_min": 0, "max_concurrent": 10}}`, wantSkipped: "invalid limits"},
		{name: "in-flight limit 0", workspace: `{"id": "x", "slug": "x", "status": "active",
			"limits": {"rate_limit_per_min": 60, "max_concurrent": 0}}`, wantSkipped: "invalid limits"},
		{name: "negative limit", workspace: `{"id": "x", "slug": "x", "status": "suspended",
			"limits": {"rate_limit_per_min": -5, "max_concurrent": 10}}`, wantSkipped: "invalid limits"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			body := `{"seq": 2, "full": false, "workspaces": [` + tt.workspace +
				`, {"id": "ok", "slug": "ok", "status": "active", "limits": {"rate_limit_per_min": 60, "max_concurrent": 10}}],
				"deleted": ["gone"]}`
			a, skipped, err := parseAnswer(strings.NewReader(body))
			if err != nil {
				t.Fatalf("parseAnswer() error: %v", err)
			}
			if len(a.Dropped) != 1 || a.Dropped[0] != "x" {
				t.Errorf("Dropped = %v, want [x]", a.Dropped)
			}
			if len(a.Workspaces) != 1 || a.Workspaces[0].ID != "ok" || len(a.Deleted) != 1 {
				t.Errorf("answer = %+v, want the good workspace and the deleted one", a)
			}
			if len(skipped) != 1 || !strings.Contains(skipped[0].Error(), tt.wantSkipped) {
				t.Errorf("skipped = %v, want one mentioning %q", skipped, tt.wantSkipped)
			}
		})
	}
}

// One without an id can't be dropped alone: the whole answer is refused.
func TestParseAnswerRejectsAWorkspaceWithoutAnID(t *testing.T) {
	body := `{"seq": 2, "full": false, "workspaces": [{"slug": "x", "status": "active"}], "deleted": []}`
	if _, _, err := parseAnswer(strings.NewReader(body)); err == nil || !strings.Contains(err.Error(), "without an id") {
		t.Errorf("parseAnswer() error: %v, want one about the missing id", err)
	}
}

func FuzzParseAnswer(f *testing.F) {
	f.Add(validBody)
	f.Add(`{"seq":1,"full":true,"workspaces":[],"deleted":[]}`)
	f.Add(`{"seq":1,"full":true,"workspaces":[`)
	f.Fuzz(func(t *testing.T, body string) {
		answer, _, err := parseAnswer(strings.NewReader(body))
		if err == nil && answer.Seq <= 0 {
			t.Fatalf("parsed a non-positive sequence %d", answer.Seq)
		}
	})
}

// A bad target is left out; the service keeps its other targets, and a
// duplicate target id keeps the first.
func TestParseAnswerKeepsAServicesGoodTargets(t *testing.T) {
	body := `{"seq": 9, "full": false, "deleted": [], "workspaces": [{"id": "w", "slug": "acme", "status": "active",
		"limits": {"rate_limit_per_min": 60, "max_concurrent": 10},
		"services": [{"id": "s", "timeout_ms": 100, "targets": [
			{"id": "t1", "url": "http://a:1", "weight": 10},
			{"id": "t2", "url": "ftp://b", "weight": 10},
			{"id": "t1", "url": "http://c:1", "weight": 10},
			{"id": "t3", "url": "http://d:1", "weight": 30}]}],
		"routes": [{"id": "r", "service_id": "s", "path_prefix": "/x", "methods": ["GET"]}], "keys": []}]}`

	a, skipped, err := parseAnswer(strings.NewReader(body))
	if err != nil {
		t.Fatalf("parseAnswer() error: %v", err)
	}
	if len(skipped) != 2 {
		t.Errorf("skipped = %v, want the ftp target and the duplicate", skipped)
	}
	svc := a.Workspaces[0].Routes[0].Service
	if len(svc.Targets) != 2 || svc.Targets[0].URL.Host != "a:1" || svc.Targets[1].ID != "t3" {
		t.Errorf("Targets = %+v, want t1 (a:1) and t3", svc.Targets)
	}
}

// The fingerprint changes with anything a balancer depends on, and only then.
func TestFingerprint(t *testing.T) {
	parse := func(targets, health string) string {
		body := `{"seq": 1, "full": true, "deleted": [], "workspaces": [{"id": "w", "slug": "acme", "status": "active",
			"limits": {"rate_limit_per_min": 60, "max_concurrent": 10},
			"services": [{"id": "s", "timeout_ms": 100, ` + health + ` "targets": [` + targets + `]}],
			"routes": [], "keys": []}]}`
		a, _, err := parseAnswer(strings.NewReader(body))
		if err != nil || len(a.Workspaces[0].Services) != 1 {
			t.Fatalf("parseAnswer: %v", err)
		}
		return a.Workspaces[0].Services[0].Fingerprint
	}
	two := `{"id": "t1", "url": "http://a:1", "weight": 10}, {"id": "t2", "url": "http://b:1", "weight": 10}`
	health := `"health": {"path": "/h", "interval_s": 10, "unhealthy": 3, "healthy": 2, "passive_failures": 5, "passive_eject_s": 30},`
	base := parse(two, health)
	if parse(two, health) != base {
		t.Error("the same service gave two fingerprints")
	}
	for name, fp := range map[string]string{
		"weight":    parse(`{"id": "t1", "url": "http://a:1", "weight": 20}, {"id": "t2", "url": "http://b:1", "weight": 10}`, health),
		"url":       parse(`{"id": "t1", "url": "http://a:2", "weight": 10}, {"id": "t2", "url": "http://b:1", "weight": 10}`, health),
		"target":    parse(`{"id": "t1", "url": "http://a:1", "weight": 10}`, health),
		"order":     parse(`{"id": "t2", "url": "http://b:1", "weight": 10}, {"id": "t1", "url": "http://a:1", "weight": 10}`, health),
		"health":    parse(two, strings.Replace(health, `"interval_s": 10`, `"interval_s": 5`, 1)),
		"no health": parse(two, ""),
	} {
		if fp == base {
			t.Errorf("changing the %s kept the fingerprint", name)
		}
	}
}

// The body limit and CORS settings of a route (MT.11).
func TestParseRouteBodyLimitAndCORS(t *testing.T) {
	parse := func(route string) (*Route, []error) {
		body := `{"seq": 1, "full": true, "deleted": [], "workspaces": [{"id": "w", "slug": "acme", "status": "active",
			"limits": {"rate_limit_per_min": 60, "max_concurrent": 10},
			"services": [{"id": "s", "timeout_ms": 100, "targets": [{"id": "t", "url": "http://a:1", "weight": 1}]}],
			"routes": [` + route + `], "keys": []}]}`
		a, skipped, err := parseAnswer(strings.NewReader(body))
		if err != nil {
			t.Fatalf("parseAnswer: %v", err)
		}
		if routes := a.Workspaces[0].Routes; len(routes) == 1 {
			return &routes[0], skipped
		}
		return nil, skipped
	}

	r, _ := parse(`{"id": "r", "service_id": "s", "path_prefix": "/x", "methods": ["GET", "POST"],
		"max_body_bytes": 2048, "cors": {"origins": ["https://App.example.com", "http://localhost:5173"],
		"methods": ["GET", "POST"], "headers": ["Content-Type", "X-API-Key"], "credentials": true, "max_age": 600}}`)
	if r == nil || r.MaxBodyBytes != 2048 || r.CORS == nil {
		t.Fatalf("route = %+v", r)
	}
	c := r.CORS
	if !c.AllowsOrigin("https://app.example.com") || !c.AllowsOrigin("http://localhost:5173") || c.AllowsOrigin("https://evil.example") || c.AllowsOrigin("") {
		t.Errorf("origins = %v", c.Origins)
	}
	if !c.AllowsMethod("POST") || c.AllowsMethod("DELETE") {
		t.Error("methods wrong")
	}
	if c.Methods != "GET, POST" || c.Headers != "Content-Type, X-API-Key" || !c.Credentials || c.MaxAge != "600" || c.AnyOrigin {
		t.Errorf("CORS = %+v", c)
	}

	// Older control planes: no limit, no CORS. No origin: CORS off.
	for _, route := range []string{
		`{"id": "r", "service_id": "s", "path_prefix": "/x", "methods": ["GET"]}`,
		`{"id": "r", "service_id": "s", "path_prefix": "/x", "methods": ["GET"], "max_body_bytes": 5,
		  "cors": {"origins": [], "methods": ["GET"], "headers": [], "credentials": false, "max_age": 600}}`,
	} {
		if r, _ := parse(route); r == nil || r.CORS != nil {
			t.Errorf("%s: route %+v, want CORS off", route, r)
		}
	}

	// Unusable settings leave the route out, with a reason.
	for want, route := range map[string]string{
		"negative":    `{"id": "r", "service_id": "s", "path_prefix": "/x", "methods": ["GET"], "max_body_bytes": -1}`,
		"credentials": `{"id": "r", "service_id": "s", "path_prefix": "/x", "methods": ["GET"], "cors": {"origins": ["*"], "methods": ["GET"], "credentials": true}}`,
		"methods":     `{"id": "r", "service_id": "s", "path_prefix": "/x", "methods": ["GET"], "cors": {"origins": ["https://a"], "methods": []}}`,
		"max_age":     `{"id": "r", "service_id": "s", "path_prefix": "/x", "methods": ["GET"], "cors": {"origins": ["https://a"], "methods": ["GET"], "max_age": -1}}`,
	} {
		r, skipped := parse(route)
		if r != nil || len(skipped) != 1 || !strings.Contains(skipped[0].Error(), want) {
			t.Errorf("%s: route %+v, skipped %v", want, r, skipped)
		}
	}
}

// V2.2: a key carries its plan's quota (null: none); the per-IP limits come
// with the workspace (missing from an older control plane: none).
func TestKeysCarryTheirPlansQuotaAndWorkspacesTheIPLimits(t *testing.T) {
	quota := int64(1000)
	zero := int64(0)
	for name, tc := range map[string]struct {
		in   wireKey
		want int64
		bad  bool
	}{
		"a quota":    {wireKey{KeyHash: "h", ConsumerStatus: "active", PlanID: "p1", MonthlyQuota: &quota}, 1000, false},
		"no quota":   {wireKey{KeyHash: "h", ConsumerStatus: "active", PlanID: "p1"}, 0, false},
		"zero quota": {wireKey{KeyHash: "h", ConsumerStatus: "active", MonthlyQuota: &zero}, 0, true},
	} {
		key, err := tc.in.toKey()
		if (err != nil) != tc.bad || key.MonthlyQuota != tc.want {
			t.Errorf("%s: %+v, %v", name, key, err)
		}
		if !tc.bad && key.PlanID != tc.in.PlanID {
			t.Errorf("%s: plan %q", name, key.PlanID)
		}
	}

	ws := wireWorkspace{ID: "w", Slug: "acme", Status: "active",
		Limits: wireLimits{RateLimitPerMin: 10, MaxConcurrent: 5, IPRateLimitPerMin: 30, AuthFailLimit: 20}}
	got, _, err := ws.toWorkspace()
	if err != nil || got.Limits.IPRateLimitPerMin != 30 || got.Limits.AuthFailLimit != 20 {
		t.Errorf("limits %+v, %v", got, err)
	}
	ws.Limits.AuthFailLimit = -1
	if _, _, err := ws.toWorkspace(); err == nil {
		t.Error("a negative auth_fail_limit was accepted")
	}
}
