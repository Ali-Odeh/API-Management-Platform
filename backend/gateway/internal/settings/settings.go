// Package settings reads the gateway's own configuration from environment
// variables.
//
// It is named "settings", not "config", because internal/config holds the
// route and key snapshot pulled from the control plane. Keeping the two apart
// avoids mixing "how the process starts" with "what it serves".
package settings

import (
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/egress"
)

// Defaults used when an optional variable is unset or empty.
const (
	DefaultListenAddr = ":8080"
	// Prometheus scrapes here (V2.3); never the public port.
	DefaultMetricsAddr = ":9091"
	// Below Docker's 10 s stop grace period, so we finish before a SIGKILL.
	DefaultShutdownTimeout = 5 * time.Second
	DefaultConfigWait      = 30 * time.Second
	// Locally `acme.localhost` resolves to this machine without any DNS.
	DefaultBaseDomain = "localhost"
)

// /internal/config accepts ?wait= from 0 to 60 whole seconds. 0 would turn
// long polling into a busy loop, so the gateway needs at least 1.
const (
	minConfigWait = 1 * time.Second
	maxConfigWait = 60 * time.Second
)

// Settings is everything the gateway needs to start. Later steps add fields
// when they first use them.
type Settings struct {
	ListenAddr      string        // LISTEN_ADDR, e.g. ":8080"
	MetricsAddr     string        // METRICS_ADDR, e.g. ":9091": GET /metrics only
	ShutdownTimeout time.Duration // SHUTDOWN_TIMEOUT, e.g. "5s"
	ControlPlaneURL string        // CONTROL_PLANE_URL, e.g. "http://control-plane:8000" (no trailing slash)
	InternalToken   Secret        // INTERNAL_TOKEN, shared with the control plane
	ConfigWait      time.Duration // CONFIG_WAIT, how long the control plane may hold a config request
	RedisURL        Secret        // REDIS_URL, e.g. "redis://redis:6379/0"; a Secret because it may hold a password
	BaseDomain      string        // GATEWAY_BASE_DOMAIN, e.g. "gw.example.com": workspaces are its subdomains
	// UPSTREAM_ALLOW, e.g. "mock-backend,10.20.0.0/16": the internal hosts and
	// networks tenants' services may reach anyway. Empty: none (production).
	Egress *egress.Policy
}

// Secret is a string that must never end up in a log or an error message.
// fmt and slog print it as "[redacted]"; Reveal gives the real value to the
// one place that needs it (the request header).
type Secret string

func (Secret) String() string       { return "[redacted]" }
func (Secret) GoString() string     { return `"[redacted]"` }
func (Secret) LogValue() slog.Value { return slog.StringValue("[redacted]") }
func (s Secret) Reveal() string     { return string(s) }
func (s Secret) IsZero() bool       { return s == "" }

// Load reads the settings from the environment and validates them, so a bad
// value stops the gateway at startup instead of failing later at runtime.
// All problems are reported at once, so one restart is enough to fix them.
func Load() (Settings, error) {
	s := Settings{
		ListenAddr:      DefaultListenAddr,
		MetricsAddr:     DefaultMetricsAddr,
		ShutdownTimeout: DefaultShutdownTimeout,
		ConfigWait:      DefaultConfigWait,
		BaseDomain:      DefaultBaseDomain,
	}
	var errs []error

	if v := os.Getenv("LISTEN_ADDR"); v != "" {
		if _, _, err := net.SplitHostPort(v); err != nil {
			errs = append(errs, fmt.Errorf("LISTEN_ADDR %q: want host:port such as \":8080\": %w", v, err))
		}
		s.ListenAddr = v
	}
	if v := os.Getenv("METRICS_ADDR"); v != "" {
		if _, _, err := net.SplitHostPort(v); err != nil {
			errs = append(errs, fmt.Errorf("METRICS_ADDR %q: want host:port such as \":9091\": %w", v, err))
		}
		s.MetricsAddr = v
	}

	if v := os.Getenv("SHUTDOWN_TIMEOUT"); v != "" {
		d, err := time.ParseDuration(v)
		switch {
		case err != nil:
			errs = append(errs, fmt.Errorf("SHUTDOWN_TIMEOUT %q: want a duration such as \"5s\": %w", v, err))
		case d <= 0:
			errs = append(errs, fmt.Errorf("SHUTDOWN_TIMEOUT %q: must be greater than zero", v))
		}
		s.ShutdownTimeout = d
	}

	cpURL, err := parseControlPlaneURL(os.Getenv("CONTROL_PLANE_URL"))
	if err != nil {
		errs = append(errs, err)
	}
	s.ControlPlaneURL = cpURL

	s.InternalToken = Secret(os.Getenv("INTERNAL_TOKEN"))
	if s.InternalToken.IsZero() {
		errs = append(errs, errors.New("INTERNAL_TOKEN is required (the same value as the control plane's)"))
	}

	if v := os.Getenv("CONFIG_WAIT"); v != "" {
		d, err := time.ParseDuration(v)
		switch {
		case err != nil:
			errs = append(errs, fmt.Errorf("CONFIG_WAIT %q: want a duration such as \"30s\": %w", v, err))
		case d < minConfigWait || d > maxConfigWait || d%time.Second != 0:
			errs = append(errs, fmt.Errorf("CONFIG_WAIT %q: must be whole seconds from %s to %s", v, minConfigWait, maxConfigWait))
		}
		s.ConfigWait = d
	}

	s.RedisURL = Secret(os.Getenv("REDIS_URL"))
	if err := checkRedisURL(s.RedisURL.Reveal()); err != nil {
		errs = append(errs, err)
	}

	if v := os.Getenv("GATEWAY_BASE_DOMAIN"); v != "" {
		s.BaseDomain = strings.TrimSuffix(strings.ToLower(v), ".")
		if !isDomain(s.BaseDomain) {
			errs = append(errs, fmt.Errorf("GATEWAY_BASE_DOMAIN %q: want a domain name such as \"gw.example.com\" (no scheme, port or path)", v))
		}
	}

	policy, err := egress.NewPolicy(strings.Split(os.Getenv("UPSTREAM_ALLOW"), ","))
	if err != nil {
		errs = append(errs, fmt.Errorf("UPSTREAM_ALLOW: %w", err))
	}
	s.Egress = policy

	if len(errs) > 0 {
		return Settings{}, errors.Join(errs...)
	}
	return s, nil
}

// parseControlPlaneURL accepts an absolute http(s) URL without query or
// fragment and drops a trailing slash, so paths can simply be appended.
func parseControlPlaneURL(v string) (string, error) {
	if v == "" {
		return "", errors.New("CONTROL_PLANE_URL is required, e.g. \"http://control-plane:8000\"")
	}
	u, err := url.Parse(v)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.RawQuery != "" || u.Fragment != "" {
		return "", fmt.Errorf("CONTROL_PLANE_URL %q: want an http(s) URL such as \"http://control-plane:8000\"", v)
	}
	return strings.TrimRight(v, "/"), nil
}

// checkRedisURL accepts redis:// or rediss:// (TLS) with a host. Only the
// form is checked: Redis being down at startup is fine (the rate limit fails
// open). The value is never printed, as it may hold a password.
func checkRedisURL(v string) error {
	if v == "" {
		return errors.New(`REDIS_URL is required, e.g. "redis://redis:6379/0"`)
	}
	u, err := url.Parse(v)
	if err != nil || (u.Scheme != "redis" && u.Scheme != "rediss") || u.Host == "" {
		return errors.New(`REDIS_URL: want a URL such as "redis://redis:6379/0"`)
	}
	return nil
}

// isDomain reports whether d is a domain name: dot-separated DNS labels of
// a-z, 0-9 and '-', each 1 to 63 long and not starting or ending with '-'.
func isDomain(d string) bool {
	if len(d) > 253 {
		return false
	}
	for _, label := range strings.Split(d, ".") {
		if label == "" || len(label) > 63 || label[0] == '-' || label[len(label)-1] == '-' {
			return false
		}
		for _, c := range []byte(label) {
			if (c < 'a' || c > 'z') && (c < '0' || c > '9') && c != '-' {
				return false
			}
		}
	}
	return true
}
