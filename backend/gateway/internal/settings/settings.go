// Package settings reads the gateway's own configuration from environment
// variables.
//
// It is named "settings", not "config", because internal/config holds the
// route and key snapshot pulled from the control plane (step 3.2). Keeping
// the two apart avoids mixing "how the process starts" with "what it serves".
package settings

import (
	"fmt"
	"net"
	"os"
	"time"
)

// Defaults used when a variable is unset or empty.
const (
	DefaultListenAddr = ":8080"
	// Below Docker's 10 s stop grace period, so we finish before a SIGKILL.
	DefaultShutdownTimeout = 5 * time.Second
)

// Settings is everything the gateway needs to start. Later steps add fields
// (control plane URL, internal token, Redis URL) when they first use them.
type Settings struct {
	ListenAddr      string        // LISTEN_ADDR, e.g. ":8080"
	ShutdownTimeout time.Duration // SHUTDOWN_TIMEOUT, e.g. "5s"
}

// Load reads the settings from the environment and validates them, so a bad
// value stops the gateway at startup instead of failing later at runtime.
func Load() (Settings, error) {
	s := Settings{
		ListenAddr:      DefaultListenAddr,
		ShutdownTimeout: DefaultShutdownTimeout,
	}

	if v := os.Getenv("LISTEN_ADDR"); v != "" {
		if _, _, err := net.SplitHostPort(v); err != nil {
			return Settings{}, fmt.Errorf("LISTEN_ADDR %q: want host:port such as \":8080\": %w", v, err)
		}
		s.ListenAddr = v
	}

	if v := os.Getenv("SHUTDOWN_TIMEOUT"); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil {
			return Settings{}, fmt.Errorf("SHUTDOWN_TIMEOUT %q: want a duration such as \"5s\": %w", v, err)
		}
		if d <= 0 {
			return Settings{}, fmt.Errorf("SHUTDOWN_TIMEOUT %q: must be greater than zero", v)
		}
		s.ShutdownTimeout = d
	}

	return s, nil
}
