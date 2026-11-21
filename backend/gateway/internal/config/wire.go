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

// The JSON shapes of GET /internal/config (PLAN.md section 6). They stay
// separate from the types in snapshot.go, so the rest of the gateway works
// with checked, ready-to-use values (a parsed URL, a time.Duration) instead
// of raw JSON. Unknown JSON fields are ignored, so the control plane can add
// fields (target health checks, CORS) without breaking older gateways.
type wireAnswer struct {
	Seq        *int64          `json:"seq"` // a pointer, so a missing seq is nil, not 0
	Full       bool            `json:"full"`
	Workspaces []wireWorkspace `json:"workspaces"`
	Deleted    []string        `json:"deleted"`
}

type wireWorkspace struct {
	ID       string        `json:"id"`
	Slug     string        `json:"slug"`
	Status   string        `json:"status"`
	Version  int64         `json:"version"`
	Hosts    []string      `json:"hosts"`
	Limits   wireLimits    `json:"limits"`
	Services []wireService `json:"services"`
	Routes   []wireRoute   `json:"routes"`
	Keys     []wireKey     `json:"keys"`
}

type wireLimits struct {
	RateLimitPerMin int `json:"rate_limit_per_min"`
	MaxConcurrent   int `json:"max_concurrent"`
}

type wireService struct {
	ID        string       `json:"id"`
	TimeoutMS int          `json:"timeout_ms"`
	Targets   []wireTarget `json:"targets"`
}

type wireTarget struct {
	ID     string `json:"id"`
	URL    string `json:"url"`
	Weight int    `json:"weight"`
}

type wireRoute struct {
	ID           string   `json:"id"`
	ServiceID    string   `json:"service_id"`
	PathPrefix   string   `json:"path_prefix"`
	Methods      []string `json:"methods"`
	StripPrefix  bool     `json:"strip_prefix"`
	AuthRequired bool     `json:"auth_required"`
}

type wireKey struct {
	ID              string `json:"id"`
	KeyHash         string `json:"key_hash"`
	ConsumerID      string `json:"consumer_id"`
	ConsumerStatus  string `json:"consumer_status"`
	RateLimitPerMin int    `json:"rate_limit_per_min"`
}

// answer is a parsed /internal/config body: the workspaces that changed
// (each complete) and the ids of those that left the config.
type answer struct {
	Seq        int64
	Full       bool
	Workspaces []*Workspace
	Deleted    []string
}

// parseAnswer turns a /internal/config body into an answer.
//
// A body that can't be read at all is an error, and the caller keeps its old
// snapshot. A single bad workspace, route or key is only skipped and
// reported in skipped: rejecting the whole answer would also block every
// other change, including key revocations, which is worse. The control plane
// validates everything already, so this is a safety net.
func parseAnswer(body io.Reader) (a answer, skipped []error, err error) {
	var w wireAnswer
	if err := json.NewDecoder(body).Decode(&w); err != nil {
		return answer{}, nil, fmt.Errorf("invalid JSON: %w", err)
	}
	if w.Seq == nil {
		return answer{}, nil, errors.New(`missing "seq"`)
	}
	a = answer{Seq: *w.Seq, Full: w.Full, Deleted: w.Deleted}
	for _, ww := range w.Workspaces {
		ws, wsSkipped, err := ww.toWorkspace()
		skipped = append(skipped, wsSkipped...)
		if err != nil {
			skipped = append(skipped, fmt.Errorf("workspace %s: %w", ww.ID, err))
			continue
		}
		a.Workspaces = append(a.Workspaces, ws)
	}
	return a, skipped, nil
}

func (w wireWorkspace) toWorkspace() (ws *Workspace, skipped []error, err error) {
	if w.ID == "" || w.Slug == "" {
		return nil, nil, errors.New("no id or slug")
	}
	status := WorkspaceStatus(w.Status)
	if status != WorkspaceActive && status != WorkspaceSuspended {
		// Unknown means "not served": leaving it out answers 404 (MT.7).
		return nil, nil, fmt.Errorf("unknown status %q", w.Status)
	}
	ws = &Workspace{
		ID:      w.ID,
		Slug:    w.Slug,
		Status:  status,
		Version: w.Version,
		Hosts:   w.Hosts,
		Limits:  Limits{RateLimitPerMin: w.Limits.RateLimitPerMin, MaxConcurrent: w.Limits.MaxConcurrent},
		Routes:  make([]Route, 0, len(w.Routes)),
		Keys:    make(map[string]Key, len(w.Keys)),
	}

	services := make(map[string]wireService, len(w.Services))
	for _, s := range w.Services {
		services[s.ID] = s
	}
	for _, wr := range w.Routes {
		route, err := wr.toRoute(services)
		if err != nil {
			skipped = append(skipped, fmt.Errorf("workspace %s, route %s: %w", w.Slug, wr.ID, err))
			continue
		}
		ws.Routes = append(ws.Routes, route)
	}
	// Stable: equal lengths keep the control plane's order, so every gateway
	// builds exactly the same list from the same answer.
	slices.SortStableFunc(ws.Routes, func(a, b Route) int {
		return cmp.Compare(len(b.PathPrefix), len(a.PathPrefix))
	})

	for _, wk := range w.Keys {
		key, err := wk.toKey()
		if err != nil {
			skipped = append(skipped, fmt.Errorf("workspace %s, key %s: %w", w.Slug, wk.ID, err))
			continue
		}
		ws.Keys[wk.KeyHash] = key
	}
	return ws, skipped, nil
}

func (w wireRoute) toRoute(services map[string]wireService) (Route, error) {
	if !strings.HasPrefix(w.PathPrefix, "/") {
		return Route{}, fmt.Errorf("path_prefix %q does not start with '/'", w.PathPrefix)
	}
	if len(w.Methods) == 0 {
		return Route{}, errors.New("no methods")
	}
	service, ok := services[w.ServiceID]
	if !ok {
		return Route{}, fmt.Errorf("service %s is not in the config", w.ServiceID)
	}
	if service.TimeoutMS <= 0 {
		return Route{}, fmt.Errorf("service timeout_ms %d is not positive", service.TimeoutMS)
	}
	// One target per service until load balancing (MT.10).
	if len(service.Targets) == 0 {
		return Route{}, fmt.Errorf("service %s has no target", w.ServiceID)
	}
	upstream, err := url.Parse(service.Targets[0].URL)
	if err != nil || (upstream.Scheme != "http" && upstream.Scheme != "https") || upstream.Host == "" {
		return Route{}, fmt.Errorf("target url %q is not an absolute http(s) URL", service.Targets[0].URL)
	}
	return Route{
		ID:           w.ID,
		ServiceID:    w.ServiceID,
		PathPrefix:   w.PathPrefix,
		Methods:      w.Methods,
		StripPrefix:  w.StripPrefix,
		AuthRequired: w.AuthRequired,
		Upstream:     upstream,
		Timeout:      time.Duration(service.TimeoutMS) * time.Millisecond,
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
