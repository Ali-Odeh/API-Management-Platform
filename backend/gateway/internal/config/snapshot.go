// Package config keeps the gateway's in-memory copy of the control plane's
// configuration (routes and API keys) and keeps it fresh.
//
// The gateway never touches Postgres: a Poller long-polls
// GET /internal/config, turns each answer into an immutable Snapshot, and
// swaps it into a Store that request handlers read without locks.
package config

import (
	"cmp"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"slices"
	"strings"
	"time"
)

// ConsumerStatus says whether a consumer's keys may be used.
type ConsumerStatus string

const (
	ConsumerActive  ConsumerStatus = "active"
	ConsumerBlocked ConsumerStatus = "blocked" // its keys get 403, not 401
)

// Route is one active route, ready for matching and proxying.
type Route struct {
	ID           string
	PathPrefix   string
	Methods      []string
	StripPrefix  bool
	AuthRequired bool
	Upstream     *url.URL      // parsed once here, not on every request
	Timeout      time.Duration // the service's timeout_ms
}

// Key is one non-revoked API key. The raw key is never known to the gateway.
type Key struct {
	ID              string
	ConsumerID      string
	ConsumerStatus  ConsumerStatus
	RateLimitPerMin int
}

// Snapshot is one version of the whole config. It is never changed after it
// is built: a newer config is a new Snapshot. That is what lets many
// goroutines read it at once without locks.
type Snapshot struct {
	Version int64
	Routes  []Route        // longest PathPrefix first, so the first match is the most specific
	Keys    map[string]Key // by SHA-256 key_hash, for one lookup per request
}

// The JSON shapes of GET /internal/config (PLAN.md section 6). They stay
// separate from the types above, so the rest of the gateway works with
// checked, ready-to-use values (a parsed URL, a time.Duration) instead of
// raw JSON. Unknown JSON fields are ignored, so the control plane can add
// fields without breaking older gateways.
type wireSnapshot struct {
	Version *int64      `json:"version"` // a pointer, so a missing version is nil, not 0
	Routes  []wireRoute `json:"routes"`
	Keys    []wireKey   `json:"keys"`
}

type wireRoute struct {
	ID           string   `json:"id"`
	PathPrefix   string   `json:"path_prefix"`
	Methods      []string `json:"methods"`
	StripPrefix  bool     `json:"strip_prefix"`
	AuthRequired bool     `json:"auth_required"`
	UpstreamURL  string   `json:"upstream_url"`
	TimeoutMS    int      `json:"timeout_ms"`
}

type wireKey struct {
	ID              string `json:"id"`
	KeyHash         string `json:"key_hash"`
	ConsumerID      string `json:"consumer_id"`
	ConsumerStatus  string `json:"consumer_status"`
	RateLimitPerMin int    `json:"rate_limit_per_min"`
}

// parseSnapshot turns a /internal/config body into a Snapshot.
//
// A body that can't be read at all is an error, and the caller keeps its old
// snapshot. A single bad route or key is only skipped and reported in
// skipped: rejecting the whole snapshot would also block every other change,
// including key revocations, which is worse. The control plane validates
// everything already, so this is a safety net.
func parseSnapshot(body io.Reader) (snap *Snapshot, skipped []error, err error) {
	var w wireSnapshot
	if err := json.NewDecoder(body).Decode(&w); err != nil {
		return nil, nil, fmt.Errorf("invalid JSON: %w", err)
	}
	if w.Version == nil {
		return nil, nil, errors.New(`missing "version"`)
	}

	snap = &Snapshot{
		Version: *w.Version,
		Routes:  make([]Route, 0, len(w.Routes)),
		Keys:    make(map[string]Key, len(w.Keys)),
	}

	for _, wr := range w.Routes {
		route, err := wr.toRoute()
		if err != nil {
			skipped = append(skipped, fmt.Errorf("route %s: %w", wr.ID, err))
			continue
		}
		snap.Routes = append(snap.Routes, route)
	}
	// Stable: equal lengths keep the control plane's order, so every gateway
	// builds exactly the same list from the same snapshot.
	slices.SortStableFunc(snap.Routes, func(a, b Route) int {
		return cmp.Compare(len(b.PathPrefix), len(a.PathPrefix))
	})

	for _, wk := range w.Keys {
		key, err := wk.toKey()
		if err != nil {
			skipped = append(skipped, fmt.Errorf("key %s: %w", wk.ID, err))
			continue
		}
		snap.Keys[wk.KeyHash] = key
	}

	return snap, skipped, nil
}

func (w wireRoute) toRoute() (Route, error) {
	if !strings.HasPrefix(w.PathPrefix, "/") {
		return Route{}, fmt.Errorf("path_prefix %q does not start with '/'", w.PathPrefix)
	}
	if len(w.Methods) == 0 {
		return Route{}, errors.New("no methods")
	}
	upstream, err := url.Parse(w.UpstreamURL)
	if err != nil || (upstream.Scheme != "http" && upstream.Scheme != "https") || upstream.Host == "" {
		return Route{}, fmt.Errorf("upstream_url %q is not an absolute http(s) URL", w.UpstreamURL)
	}
	if w.TimeoutMS <= 0 {
		return Route{}, fmt.Errorf("timeout_ms %d is not positive", w.TimeoutMS)
	}
	return Route{
		ID:           w.ID,
		PathPrefix:   w.PathPrefix,
		Methods:      w.Methods,
		StripPrefix:  w.StripPrefix,
		AuthRequired: w.AuthRequired,
		Upstream:     upstream,
		Timeout:      time.Duration(w.TimeoutMS) * time.Millisecond,
	}, nil
}

func (w wireKey) toKey() (Key, error) {
	if w.KeyHash == "" {
		return Key{}, errors.New("empty key_hash")
	}
	status := ConsumerStatus(w.ConsumerStatus)
	if status != ConsumerActive && status != ConsumerBlocked {
		// Unknown means "not allowed": the key is left out, so it gets 401.
		return Key{}, fmt.Errorf("unknown consumer_status %q", w.ConsumerStatus)
	}
	return Key{
		ID:              w.ID,
		ConsumerID:      w.ConsumerID,
		ConsumerStatus:  status,
		RateLimitPerMin: w.RateLimitPerMin,
	}, nil
}
