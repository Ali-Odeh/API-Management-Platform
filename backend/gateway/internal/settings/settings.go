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
)

// Defaults used when an optional variable is unset or empty.
const (
	DefaultListenAddr = ":8080"
	// Below Docker's 10 s stop grace period, so we finish before a SIGKILL.
	DefaultShutdownTimeout = 5 * time.Second
	DefaultConfigWait      = 30 * time.Second
)

// /internal/config accepts ?wait= from 0 to 60 whole seconds. 0 would turn
// long polling into a busy loop, so the gateway needs at least 1.
const (
	minConfigWait = 1 * time.Second
	maxConfigWait = 60 * time.Second
)

// Settings is everything the gateway needs to start. Later steps add fields
// (such as the Redis URL) when they first use them.
type Settings struct {
	ListenAddr      string        // LISTEN_ADDR, e.g. ":8080"
	ShutdownTimeout time.Duration // SHUTDOWN_TIMEOUT, e.g. "5s"
	ControlPlaneURL string        // CONTROL_PLANE_URL, e.g. "http://control-plane:8000" (no trailing slash)
	InternalToken   Secret        // INTERNAL_TOKEN, shared with the control plane
	ConfigWait      time.Duration // CONFIG_WAIT, how long the control plane may hold a config request
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
		ShutdownTimeout: DefaultShutdownTimeout,
		ConfigWait:      DefaultConfigWait,
	}
	var errs []error

	if v := os.Getenv("LISTEN_ADDR"); v != "" {
		if _, _, err := net.SplitHostPort(v); err != nil {
			errs = append(errs, fmt.Errorf("LISTEN_ADDR %q: want host:port such as \":8080\": %w", v, err))
		}
		s.ListenAddr = v
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
