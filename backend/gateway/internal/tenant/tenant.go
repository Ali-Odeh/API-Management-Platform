// Package tenant finds the workspace a request is for, from its Host header
// (decision 1): `acme.gw.example.com` is workspace `acme`. It runs before
// route matching, so everything after it sees one workspace only.
package tenant

import (
	"net"
	"net/http"
	"strings"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/respond"
)

// WorkspaceIDHeader tells the backend which workspace the request is for.
// Only the gateway sets it; a value sent by the client is always replaced.
const WorkspaceIDHeader = "X-Workspace-ID"

// SnapshotSource gives the config snapshot in use, or nil before the first
// one arrives. *config.Store satisfies it; tests pass a fake.
type SnapshotSource interface {
	Current() *config.Snapshot
}

// Middleware finds the request's workspace and puts it on the request's
// notes card (reqctx), for the router, auth and the log:
//
//	no config yet                 → 503 config_not_loaded
//	unknown workspace subdomain   → 404 workspace_not_found
//	suspended workspace           → 403 workspace_suspended
//	anything else                 → on, with a trusted X-Workspace-ID
//
// The snapshot is read once here; every later step uses the workspace taken
// from it, even if a newer config arrives while the request runs.
func Middleware(source SnapshotSource, baseDomain string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if !reqctx.Has(r.Context()) { // the logger normally adds it first
				r = r.WithContext(reqctx.With(r.Context(), &reqctx.Info{}))
			}
			snap := source.Current()
			if snap == nil {
				w.Header().Set("Retry-After", "1")
				respond.Error(w, http.StatusServiceUnavailable, "config_not_loaded")
				return
			}
			var ws *config.Workspace
			if label, ok := Label(r.Host, baseDomain); ok {
				ws = snap.ByHost[label]
			}
			if ws == nil {
				respond.Error(w, http.StatusNotFound, "workspace_not_found")
				return
			}
			reqctx.From(r.Context()).Workspace = ws // also for the log line

			if ws.Status == config.WorkspaceSuspended {
				respond.Error(w, http.StatusForbidden, "workspace_suspended")
				return
			}
			// Replaces any value the client sent: nobody may pose as another workspace.
			r.Header.Set(WorkspaceIDHeader, ws.ID)
			next.ServeHTTP(w, r)
		})
	}
}

// Label returns the workspace host label a Host header names:
//
//	"acme.gw.example.com:8443" (base gw.example.com) → "acme", true
//	"gw.example.com", "localhost", an IP, "gateway:8080", "" → "default", true
//	"a.b.gw.example.com", "-x.gw.example.com", "a:b:c" → "", false
//
// Only one valid DNS label directly under the base domain leads to a
// workspace. Every other name (the bare domain, internal names, IPs) is the
// gateway's own and serves `default`, like a reverse proxy's default server:
// it can never lead to another tenant. Matching ignores case and a trailing dot.
func Label(host, baseDomain string) (string, bool) {
	if host == "" { // HTTP/1.0 may send none: no subdomain
		return config.DefaultHost, true
	}
	name, ok := hostname(host)
	if !ok {
		return "", false
	}
	sub, found := strings.CutSuffix(name, "."+baseDomain)
	if !found {
		return config.DefaultHost, true // not under the base domain (or the bare domain itself)
	}
	if !isLabel(sub) {
		return "", false
	}
	return sub, true
}

// hostname is host without its port, lowercase, without a trailing dot.
func hostname(host string) (string, bool) {
	if strings.ContainsRune(host, ':') {
		h, _, err := net.SplitHostPort(host)
		if err != nil {
			// Brackets alone, as http.Request.Host keeps them for IPv6 without a port.
			if !strings.HasPrefix(host, "[") || !strings.HasSuffix(host, "]") {
				return "", false
			}
			h = host[1 : len(host)-1]
		}
		host = h
	}
	host = strings.TrimSuffix(strings.ToLower(host), ".")
	return host, host != ""
}

// isLabel reports whether s is one DNS label: 1 to 63 of a-z, 0-9 and '-',
// not starting or ending with '-'.
func isLabel(s string) bool {
	if s == "" || len(s) > 63 || s[0] == '-' || s[len(s)-1] == '-' {
		return false
	}
	for _, c := range []byte(s) {
		if (c < 'a' || c > 'z') && (c < '0' || c > '9') && c != '-' {
			return false
		}
	}
	return true
}
