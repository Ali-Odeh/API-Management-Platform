// Package config keeps the gateway's in-memory copy of the control plane's
// configuration (every workspace's routes and API keys) and keeps it fresh.
//
// The gateway never touches Postgres: a Poller long-polls
// GET /internal/config, which answers only the workspaces that changed. The
// poller merges each answer into a new immutable Snapshot and swaps it into
// a Store that request handlers read without locks.
package config

import (
	"net/url"
	"time"
)

// DefaultHost is the host label of the `default` workspace. Requests without
// a workspace subdomain go to it; until the gateway tells workspaces apart
// by Host (MT.7), every request does.
const DefaultHost = "default"

// ConsumerStatus says whether a consumer's keys may be used.
type ConsumerStatus string

const (
	ConsumerActive  ConsumerStatus = "active"
	ConsumerBlocked ConsumerStatus = "blocked" // its keys get 403, not 401
)

// WorkspaceStatus says whether a workspace's traffic is served.
type WorkspaceStatus string

const (
	WorkspaceActive    WorkspaceStatus = "active"
	WorkspaceSuspended WorkspaceStatus = "suspended" // its traffic gets 403 (MT.7)
)

// Route is one active route, ready for matching and proxying.
type Route struct {
	ID           string
	ServiceID    string
	PathPrefix   string
	Methods      []string
	StripPrefix  bool
	AuthRequired bool
	Upstream     *url.URL      // the service's target, parsed once here, not on every request
	Timeout      time.Duration // the service's timeout_ms
}

// Key is one non-revoked API key. The raw key is never known to the gateway.
type Key struct {
	ID              string
	ConsumerID      string
	ConsumerStatus  ConsumerStatus
	RateLimitPerMin int
}

// Limits are the workspace's limits as they apply (its own or the platform
// defaults); the gateway enforces them from MT.8 on.
type Limits struct {
	RateLimitPerMin int
	MaxConcurrent   int
}

// Workspace is one workspace's whole config at one version. Like a
// Snapshot it is never changed after it is built, so a new snapshot can
// share it with the previous one.
type Workspace struct {
	ID      string
	Slug    string
	Status  WorkspaceStatus
	Version int64
	Hosts   []string // host labels it is served on (its slug; custom domains later)
	Limits  Limits
	Routes  []Route        // longest PathPrefix first, so the first match is the most specific
	Keys    map[string]Key // by SHA-256 key_hash, for one lookup per request
}

// Snapshot is the gateway's whole config at one sequence number. It is never
// changed after it is built: a newer config is a new Snapshot, which reuses
// the *Workspace of every workspace that didn't change. That is what lets
// many goroutines read it at once without locks.
type Snapshot struct {
	Seq        int64
	Workspaces map[string]*Workspace // by workspace id
	ByHost     map[string]*Workspace // by host label
}

// NewSnapshot builds a snapshot of the given workspaces at sequence seq.
func NewSnapshot(seq int64, workspaces ...*Workspace) *Snapshot {
	snap := &Snapshot{
		Seq:        seq,
		Workspaces: make(map[string]*Workspace, len(workspaces)),
		ByHost:     make(map[string]*Workspace, len(workspaces)),
	}
	for _, ws := range workspaces {
		snap.Workspaces[ws.ID] = ws
	}
	snap.indexHosts()
	return snap
}

// indexHosts fills ByHost from Workspaces. Hosts are unique across
// workspaces (they are slugs, unique in the control plane).
func (s *Snapshot) indexHosts() {
	for _, ws := range s.Workspaces {
		for _, host := range ws.Hosts {
			s.ByHost[host] = ws
		}
	}
}

// Default is the `default` workspace, or nil if the config has none.
func (s *Snapshot) Default() *Workspace {
	return s.ByHost[DefaultHost]
}
