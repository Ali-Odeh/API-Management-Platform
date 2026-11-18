package config

import (
	"strings"
	"testing"
	"time"
)

// A snapshot shaped like the plan's example (PLAN.md section 6).
const validBody = `{
  "version": 43,
  "routes": [
    {"id": "r1", "path_prefix": "/v1", "methods": ["GET"], "strip_prefix": false,
     "auth_required": true, "upstream_url": "http://mock-backend:9000", "timeout_ms": 5000},
    {"id": "r2", "path_prefix": "/v1/orders", "methods": ["GET", "POST"], "strip_prefix": true,
     "auth_required": false, "upstream_url": "https://orders.internal/api", "timeout_ms": 250},
    {"id": "r3", "path_prefix": "/v2", "methods": ["GET"], "strip_prefix": false,
     "auth_required": true, "upstream_url": "http://mock-backend:9000", "timeout_ms": 5000}
  ],
  "keys": [
    {"id": "k1", "key_hash": "aaa", "consumer_id": "c1", "consumer_status": "active", "rate_limit_per_min": 60},
    {"id": "k2", "key_hash": "bbb", "consumer_id": "c2", "consumer_status": "blocked", "rate_limit_per_min": 10}
  ],
  "a_field_from_the_future": true
}`

func TestParseSnapshot(t *testing.T) {
	snap, skipped, err := parseSnapshot(strings.NewReader(validBody))
	if err != nil {
		t.Fatalf("parseSnapshot() error: %v", err)
	}
	if len(skipped) != 0 {
		t.Errorf("skipped = %v, want none", skipped)
	}
	if snap.Version != 43 {
		t.Errorf("Version = %d, want 43", snap.Version)
	}

	// Longest prefix first; equal lengths keep the control plane's order.
	var ids []string
	for _, r := range snap.Routes {
		ids = append(ids, r.ID)
	}
	if got := strings.Join(ids, ","); got != "r2,r1,r3" {
		t.Errorf("route order = %s, want r2,r1,r3", got)
	}

	orders := snap.Routes[0]
	if orders.Upstream.String() != "https://orders.internal/api" {
		t.Errorf("Upstream = %s", orders.Upstream)
	}
	if orders.Timeout != 250*time.Millisecond {
		t.Errorf("Timeout = %s, want 250ms", orders.Timeout)
	}
	if !orders.StripPrefix || orders.AuthRequired {
		t.Errorf("flags = strip %v auth %v, want strip true auth false", orders.StripPrefix, orders.AuthRequired)
	}

	if k, ok := snap.Keys["bbb"]; !ok || k.ConsumerStatus != ConsumerBlocked || k.ID != "k2" {
		t.Errorf(`Keys["bbb"] = %+v, %v; want blocked key k2`, k, ok)
	}
	if len(snap.Keys) != 2 {
		t.Errorf("len(Keys) = %d, want 2", len(snap.Keys))
	}
}

func TestParseSnapshotEmptyConfig(t *testing.T) {
	snap, _, err := parseSnapshot(strings.NewReader(`{"version": 1, "routes": [], "keys": []}`))
	if err != nil {
		t.Fatalf("parseSnapshot() error: %v", err)
	}
	if len(snap.Routes) != 0 || snap.Keys == nil || len(snap.Keys) != 0 {
		t.Errorf("snapshot = %+v, want no routes and an empty, usable key map", snap)
	}
}

func TestParseSnapshotRejectsUnreadableBodies(t *testing.T) {
	tests := []struct {
		name    string
		body    string
		wantErr string
	}{
		{name: "not JSON", body: "<html>oops</html>", wantErr: "invalid JSON"},
		{name: "cut off", body: `{"version": 4, "routes": [`, wantErr: "invalid JSON"},
		{name: "wrong type", body: `{"version": "four"}`, wantErr: "invalid JSON"},
		{name: "no version", body: `{"routes": [], "keys": []}`, wantErr: "missing \"version\""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			snap, _, err := parseSnapshot(strings.NewReader(tt.body))
			if err == nil || !strings.Contains(err.Error(), tt.wantErr) {
				t.Fatalf("parseSnapshot() = %v, %v; want an error mentioning %q", snap, err, tt.wantErr)
			}
		})
	}
}

func TestParseSnapshotSkipsOnlyBadEntries(t *testing.T) {
	const good = `{"id": "good", "path_prefix": "/ok", "methods": ["GET"], "upstream_url": "http://up:1", "timeout_ms": 100}`
	tests := []struct {
		name        string
		badRoute    string
		badKey      string
		wantSkipped string
	}{
		{name: "prefix without slash", badRoute: `{"id": "bad", "path_prefix": "x", "methods": ["GET"], "upstream_url": "http://up:1", "timeout_ms": 100}`, wantSkipped: "route bad"},
		{name: "no methods", badRoute: `{"id": "bad", "path_prefix": "/x", "methods": [], "upstream_url": "http://up:1", "timeout_ms": 100}`, wantSkipped: "no methods"},
		{name: "relative upstream", badRoute: `{"id": "bad", "path_prefix": "/x", "methods": ["GET"], "upstream_url": "up:1/x", "timeout_ms": 100}`, wantSkipped: "upstream_url"},
		{name: "non-http upstream", badRoute: `{"id": "bad", "path_prefix": "/x", "methods": ["GET"], "upstream_url": "ftp://up", "timeout_ms": 100}`, wantSkipped: "upstream_url"},
		{name: "unparseable upstream", badRoute: `{"id": "bad", "path_prefix": "/x", "methods": ["GET"], "upstream_url": "http://[::1", "timeout_ms": 100}`, wantSkipped: "upstream_url"},
		{name: "zero timeout", badRoute: `{"id": "bad", "path_prefix": "/x", "methods": ["GET"], "upstream_url": "http://up:1", "timeout_ms": 0}`, wantSkipped: "timeout_ms"},
		{name: "empty key hash", badKey: `{"id": "kbad", "key_hash": "", "consumer_status": "active"}`, wantSkipped: "key kbad"},
		{name: "unknown consumer status", badKey: `{"id": "kbad", "key_hash": "zzz", "consumer_status": "suspended"}`, wantSkipped: "consumer_status"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			routes, keys := good, `{"id": "kgood", "key_hash": "h", "consumer_status": "active"}`
			if tt.badRoute != "" {
				routes += "," + tt.badRoute
			}
			if tt.badKey != "" {
				keys += "," + tt.badKey
			}
			body := `{"version": 9, "routes": [` + routes + `], "keys": [` + keys + `]}`

			snap, skipped, err := parseSnapshot(strings.NewReader(body))
			if err != nil {
				t.Fatalf("parseSnapshot() error: %v", err)
			}
			if len(skipped) != 1 || !strings.Contains(skipped[0].Error(), tt.wantSkipped) {
				t.Fatalf("skipped = %v, want one entry mentioning %q", skipped, tt.wantSkipped)
			}
			// The good entries still made it in.
			if len(snap.Routes) != 1 || snap.Routes[0].ID != "good" || len(snap.Keys) != 1 {
				t.Errorf("snapshot = %+v, want only the good route and key", snap)
			}
		})
	}
}
