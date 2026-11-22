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
	ID          string       `json:"id"`
	TimeoutMS   int          `json:"timeout_ms"`
	LBAlgorithm string       `json:"lb_algorithm"`
	Health      *wireHealth  `json:"health"`
	Targets     []wireTarget `json:"targets"`
}

type wireHealth struct {
	Path            string `json:"path"`
	IntervalS       int    `json:"interval_s"`
	Unhealthy       int    `json:"unhealthy"`
	Healthy         int    `json:"healthy"`
	PassiveFailures int    `json:"passive_failures"`
	PassiveEjectS   int    `json:"passive_eject_s"`
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
	Dropped    []string // workspaces that couldn't be read: not served, not kept stale
}

// parseAnswer turns a /internal/config body into an answer.
//
// Decode one workspace at a time, so a full answer doesn't need a second
// copy of every workspace in memory. A workspace that can't be read is
// dropped (Dropped): keeping its previous config would leave stale keys in
// service, and rejecting the whole answer would freeze every other
// workspace too. One without an id can't be dropped alone, so it fails the
// answer. A bad route or key is left out of the workspace that replaces it.
func parseAnswer(body io.Reader) (a answer, skipped []error, err error) {
	decoder := json.NewDecoder(body)
	if err := expectToken(decoder, '{'); err != nil {
		return answer{}, nil, err
	}
	var seq *int64
	var full *bool
	seen := make(map[string]bool)
	for decoder.More() {
		token, err := decoder.Token()
		if err != nil {
			return answer{}, nil, fmt.Errorf("invalid JSON: %w", err)
		}
		name := token.(string) // JSON object keys are always strings
		if seen[name] {
			return answer{}, nil, fmt.Errorf("duplicate config field %q", name)
		}
		seen[name] = true
		switch name {
		case "seq":
			err = decoder.Decode(&seq)
		case "full":
			err = decoder.Decode(&full)
		case "workspaces":
			a.Workspaces, a.Dropped, skipped, err = decodeWorkspaces(decoder)
		case "deleted":
			err = decoder.Decode(&a.Deleted)
		default:
			var ignored json.RawMessage
			err = decoder.Decode(&ignored)
		}
		if err != nil {
			return answer{}, nil, fmt.Errorf("invalid JSON config field %q: %w", name, err)
		}
	}
	if err := expectToken(decoder, '}'); err != nil {
		return answer{}, nil, err
	}
	if _, err := decoder.Token(); !errors.Is(err, io.EOF) {
		return answer{}, nil, errors.New("invalid JSON: data after config answer")
	}
	if seq == nil {
		return answer{}, nil, errors.New(`missing "seq"`)
	}
	if *seq <= 0 || full == nil || !seen["workspaces"] || a.Deleted == nil {
		return answer{}, nil, errors.New("incomplete config answer")
	}
	a.Seq, a.Full = *seq, *full
	return a, skipped, nil
}

func expectToken(decoder *json.Decoder, want json.Delim) error {
	token, err := decoder.Token()
	if err != nil {
		return fmt.Errorf("invalid JSON: %w", err)
	}
	if token != want {
		return fmt.Errorf("invalid JSON: expected %q", want)
	}
	return nil
}

func decodeWorkspaces(decoder *json.Decoder) (workspaces []*Workspace, dropped []string, skipped []error, err error) {
	if err := expectToken(decoder, '['); err != nil {
		return nil, nil, nil, err
	}
	seen := make(map[string]bool)
	for decoder.More() {
		var ww wireWorkspace
		if err := decoder.Decode(&ww); err != nil {
			return nil, nil, nil, err
		}
		if ww.ID == "" {
			return nil, nil, nil, errors.New("a workspace without an id")
		}
		if seen[ww.ID] {
			return nil, nil, nil, fmt.Errorf("duplicate workspace %s", ww.ID)
		}
		seen[ww.ID] = true
		ws, wsSkipped, err := ww.toWorkspace()
		if err != nil {
			dropped = append(dropped, ww.ID)
			skipped = append(skipped, fmt.Errorf("workspace %s not served: %w", ww.ID, err))
			continue
		}
		skipped = append(skipped, wsSkipped...)
		workspaces = append(workspaces, ws)
	}
	return workspaces, dropped, skipped, expectToken(decoder, ']')
}

func (w wireWorkspace) toWorkspace() (ws *Workspace, skipped []error, err error) {
	if w.Slug == "" {
		return nil, nil, errors.New("no slug")
	}
	status := WorkspaceStatus(w.Status)
	if status != WorkspaceActive && status != WorkspaceSuspended {
		// Unknown means "not served": it is dropped, and answers 404 (MT.7).
		return nil, nil, fmt.Errorf("unknown status %q", w.Status)
	}
	// The control plane always sends the effective limits (at least 1, by
	// its CHECKs). Without them the gateway couldn't protect the others from
	// this workspace (MT.8): not served, like any workspace it can't read.
	if w.Limits.RateLimitPerMin < 1 || w.Limits.MaxConcurrent < 1 {
		return nil, nil, fmt.Errorf("invalid limits (rate_limit_per_min %d, max_concurrent %d)",
			w.Limits.RateLimitPerMin, w.Limits.MaxConcurrent)
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

	services := make(map[string]*Service, len(w.Services))
	for _, wsvc := range w.Services {
		svc, svcSkipped, err := wsvc.toService(w.ID)
		skipped = append(skipped, svcSkipped...)
		if err != nil {
			skipped = append(skipped, fmt.Errorf("workspace %s, service %s: %w", w.Slug, wsvc.ID, err))
			continue
		}
		services[svc.ID] = svc
		ws.Services = append(ws.Services, svc)
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

// The only load-balancing algorithm so far (decision 14). An empty one is
// round robin too, as from a control plane older than MT.10.
const lbRoundRobin = "round_robin"

// Target weights the control plane allows.
const minWeight, maxWeight = 1, 1000

// toService checks a service and its targets. A bad target is left out
// (skipped); a service without any usable target, or with unusable
// settings, is an error: its routes are left out.
func (w wireService) toService(workspaceID string) (svc *Service, skipped []error, err error) {
	if w.ID == "" {
		return nil, nil, errors.New("no id")
	}
	if w.TimeoutMS <= 0 {
		return nil, nil, fmt.Errorf("timeout_ms %d is not positive", w.TimeoutMS)
	}
	if w.LBAlgorithm != "" && w.LBAlgorithm != lbRoundRobin {
		return nil, nil, fmt.Errorf("unknown lb_algorithm %q", w.LBAlgorithm)
	}
	health, err := w.Health.toHealth()
	if err != nil {
		return nil, nil, err
	}
	svc = &Service{
		ID:          w.ID,
		WorkspaceID: workspaceID,
		Timeout:     time.Duration(w.TimeoutMS) * time.Millisecond,
		Health:      health,
		Targets:     make([]Target, 0, len(w.Targets)),
	}
	seen := make(map[string]bool, len(w.Targets))
	for _, wt := range w.Targets {
		target, err := wt.toTarget()
		if err == nil && seen[wt.ID] {
			err = errors.New("duplicate id")
		}
		if err != nil {
			skipped = append(skipped, fmt.Errorf("service %s, target %s: %w", w.ID, wt.ID, err))
			continue
		}
		seen[wt.ID] = true
		svc.Targets = append(svc.Targets, target)
	}
	if len(svc.Targets) == 0 {
		return nil, skipped, errors.New("no usable target")
	}
	svc.Fingerprint = fingerprint(svc)
	return svc, skipped, nil
}

func (w wireTarget) toTarget() (Target, error) {
	if w.ID == "" {
		return Target{}, errors.New("no id")
	}
	u, err := url.Parse(w.URL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return Target{}, fmt.Errorf("url %q is not an absolute http(s) URL", w.URL)
	}
	if w.Weight < minWeight || w.Weight > maxWeight {
		return Target{}, fmt.Errorf("weight %d is not %d to %d", w.Weight, minWeight, maxWeight)
	}
	return Target{ID: w.ID, URL: u, Weight: w.Weight}, nil
}

// toHealth checks the health settings. Missing settings (a control plane
// older than MT.10) mean no checks at all.
func (w *wireHealth) toHealth() (Health, error) {
	if w == nil {
		return Health{}, nil
	}
	switch {
	case !strings.HasPrefix(w.Path, "/"):
		return Health{}, fmt.Errorf("health path %q does not start with '/'", w.Path)
	case w.IntervalS < 1 || w.Unhealthy < 1 || w.Healthy < 1:
		return Health{}, fmt.Errorf("active health settings %d s, %d, %d must be at least 1", w.IntervalS, w.Unhealthy, w.Healthy)
	case w.PassiveFailures < 1 || w.PassiveEjectS < 1:
		return Health{}, fmt.Errorf("passive health settings %d, %d s must be at least 1", w.PassiveFailures, w.PassiveEjectS)
	}
	return Health{
		Path:            w.Path,
		Interval:        time.Duration(w.IntervalS) * time.Second,
		Unhealthy:       w.Unhealthy,
		Healthy:         w.Healthy,
		PassiveFailures: w.PassiveFailures,
		PassiveEject:    time.Duration(w.PassiveEjectS) * time.Second,
	}, nil
}

// fingerprint names everything a balancer depends on: the targets (id, url,
// weight, in order) and the health settings.
func fingerprint(svc *Service) string {
	var b strings.Builder
	fmt.Fprintf(&b, "%+v", svc.Health)
	for _, t := range svc.Targets {
		fmt.Fprintf(&b, "|%s %s %d", t.ID, t.URL, t.Weight)
	}
	return b.String()
}

func (w wireRoute) toRoute(services map[string]*Service) (Route, error) {
	if !strings.HasPrefix(w.PathPrefix, "/") {
		return Route{}, fmt.Errorf("path_prefix %q does not start with '/'", w.PathPrefix)
	}
	if len(w.Methods) == 0 {
		return Route{}, errors.New("no methods")
	}
	service, ok := services[w.ServiceID]
	if !ok {
		return Route{}, fmt.Errorf("service %s is not in the config, or not usable", w.ServiceID)
	}
	return Route{
		ID:           w.ID,
		ServiceID:    w.ServiceID,
		PathPrefix:   w.PathPrefix,
		Methods:      w.Methods,
		StripPrefix:  w.StripPrefix,
		AuthRequired: w.AuthRequired,
		Service:      service,
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
