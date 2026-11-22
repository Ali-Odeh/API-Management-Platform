// Package config keeps the gateway's in-memory copy of the control plane's
// configuration (every workspace's routes and API keys) and keeps it fresh.
//
// The gateway never touches Postgres: a Poller long-polls
// GET /internal/config, which answers only the workspaces that changed. The
// poller merges each answer into a new immutable Snapshot and swaps it into
// a Store that request handlers read without locks.
package config

import (
	"errors"
	"fmt"
	"net/url"
	"strconv"
	"strings"
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
	Service      *Service // its service, shared with the workspace's other routes
	// MaxBodyBytes is the largest request body let through (MT.11): the
	// route's own limit or the platform default. 0: no limit (a control
	// plane older than MT.11).
	MaxBodyBytes int64
	CORS         *CORS // nil: CORS off for this route
}

// CORS is a route's CORS settings (MT.11, decision 15), with the answer
// headers prepared once here instead of on every request.
type CORS struct {
	AnyOrigin   bool            // "*": every origin (never with Credentials)
	Origins     map[string]bool // exact origins, lowercase, as browsers send them
	Methods     string          // Access-Control-Allow-Methods, e.g. "GET, POST"
	Headers     string          // Access-Control-Allow-Headers; "" when none
	Credentials bool
	MaxAge      string // Access-Control-Max-Age, in seconds

	methods map[string]bool
}

// NewCORS builds a route's CORS settings. No origin means CORS off (nil,
// no error). Origins are exact ("https://app.example.com") or "*" for any,
// which can't go with credentials: browsers refuse that answer.
func NewCORS(origins, methods, headers []string, credentials bool, maxAge int) (*CORS, error) {
	if len(origins) == 0 {
		return nil, nil
	}
	if len(methods) == 0 {
		return nil, errors.New("cors without methods")
	}
	if maxAge < 0 {
		return nil, fmt.Errorf("cors max_age %d is negative", maxAge)
	}
	c := &CORS{
		Origins:     make(map[string]bool, len(origins)),
		Methods:     strings.Join(methods, ", "),
		Headers:     strings.Join(headers, ", "),
		Credentials: credentials,
		MaxAge:      strconv.Itoa(maxAge),
		methods:     make(map[string]bool, len(methods)),
	}
	for _, origin := range origins {
		if origin == "*" {
			c.AnyOrigin = true
			continue
		}
		c.Origins[strings.ToLower(origin)] = true
	}
	if c.AnyOrigin && c.Credentials {
		return nil, errors.New("cors allows credentials for any origin")
	}
	for _, m := range methods {
		c.methods[m] = true
	}
	return c, nil
}

// AllowsOrigin reports whether a browser page from origin may call the route.
func (c *CORS) AllowsOrigin(origin string) bool {
	return origin != "" && (c.AnyOrigin || c.Origins[strings.ToLower(origin)])
}

// AllowsMethod reports whether a browser may use method on the route.
func (c *CORS) AllowsMethod(method string) bool {
	return c.methods[method]
}

// Service is one active service with its usable targets (MT.10).
type Service struct {
	ID          string
	WorkspaceID string
	Timeout     time.Duration // the whole answer, start to last byte
	Targets     []Target      // at least one, in the control plane's order
	Health      Health
	// Fingerprint identifies the targets and health settings. The gateway
	// keeps a service's balancer and health state across config versions
	// while it stays the same, and starts fresh when it changes.
	Fingerprint string
}

// Target is one backend instance of a service.
type Target struct {
	ID     string
	URL    *url.URL // parsed once here, not on every request
	Weight int      // its share of the requests, relative to the others (1-1000)
}

// Health is how a service's targets are checked (decision 14).
type Health struct {
	Path            string        // GET <target><Path> must answer 2xx or 3xx
	Interval        time.Duration // between active checks; 0: no active checks
	Unhealthy       int           // failed checks in a row that take a target out
	Healthy         int           // passed checks in a row that bring it back
	PassiveFailures int           // failed requests in a row that set a target aside; 0: never
	PassiveEject    time.Duration // how long it is set aside
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
	ID       string
	Slug     string
	Status   WorkspaceStatus
	Version  int64
	Hosts    []string // host labels it is served on (its slug; custom domains later)
	Limits   Limits
	Services []*Service     // the usable services, in the control plane's order
	Routes   []Route        // longest PathPrefix first, so the first match is the most specific
	Keys     map[string]Key // by SHA-256 key_hash, for one lookup per request
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
