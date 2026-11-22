package settings

import (
	"bytes"
	"fmt"
	"log/slog"
	"net/netip"
	"strings"
	"testing"
	"time"
)

// required holds valid values for the variables that have no default.
var required = map[string]string{
	"CONTROL_PLANE_URL": "http://control-plane:8000",
	"INTERNAL_TOKEN":    "s3cret",
	"REDIS_URL":         "redis://redis:6379/0",
}

// defaults is what Load returns when only the required variables are set.
var defaults = Settings{
	ListenAddr:      ":8080",
	ShutdownTimeout: 5 * time.Second,
	ControlPlaneURL: "http://control-plane:8000",
	InternalToken:   "s3cret",
	RedisURL:        "redis://redis:6379/0",
	ConfigWait:      30 * time.Second,
	BaseDomain:      "localhost",
}

// setEnv starts from a clean slate, whatever the machine has set, then
// applies the required values and the case's own values on top.
func setEnv(t *testing.T, env map[string]string) {
	t.Helper()
	for _, k := range []string{"LISTEN_ADDR", "SHUTDOWN_TIMEOUT", "CONTROL_PLANE_URL", "INTERNAL_TOKEN", "CONFIG_WAIT", "REDIS_URL", "GATEWAY_BASE_DOMAIN", "UPSTREAM_ALLOW"} {
		t.Setenv(k, "")
	}
	for k, v := range required {
		t.Setenv(k, v)
	}
	for k, v := range env {
		t.Setenv(k, v)
	}
}

func TestLoad(t *testing.T) {
	tests := []struct {
		name    string
		env     map[string]string
		want    Settings
		wantErr []string // every string must appear in the error; empty means no error
	}{
		{
			name: "defaults when only required values are set",
			env:  map[string]string{},
			want: defaults,
		},
		{
			name: "empty optional values count as unset",
			env:  map[string]string{"LISTEN_ADDR": "", "SHUTDOWN_TIMEOUT": "", "CONFIG_WAIT": "", "GATEWAY_BASE_DOMAIN": ""},
			want: defaults,
		},
		{
			name: "custom values",
			env: map[string]string{
				"LISTEN_ADDR":         "127.0.0.1:9090",
				"SHUTDOWN_TIMEOUT":    "1500ms",
				"CONTROL_PLANE_URL":   "https://cp.example.com/",
				"CONFIG_WAIT":         "60s",
				"GATEWAY_BASE_DOMAIN": "GW.Example.com.",
			},
			want: Settings{
				ListenAddr:      "127.0.0.1:9090",
				ShutdownTimeout: 1500 * time.Millisecond,
				ControlPlaneURL: "https://cp.example.com", // trailing slash dropped
				InternalToken:   "s3cret",
				RedisURL:        "redis://redis:6379/0",
				ConfigWait:      60 * time.Second,
				BaseDomain:      "gw.example.com", // lowercase, no trailing dot
			},
		},
		{name: "listen address without a colon", env: map[string]string{"LISTEN_ADDR": "8080"}, wantErr: []string{"LISTEN_ADDR"}},
		{name: "shutdown timeout that is not a duration", env: map[string]string{"SHUTDOWN_TIMEOUT": "five"}, wantErr: []string{"SHUTDOWN_TIMEOUT"}},
		{name: "shutdown timeout of zero", env: map[string]string{"SHUTDOWN_TIMEOUT": "0s"}, wantErr: []string{"greater than zero"}},
		{name: "negative shutdown timeout", env: map[string]string{"SHUTDOWN_TIMEOUT": "-1s"}, wantErr: []string{"greater than zero"}},
		{name: "control plane URL missing", env: map[string]string{"CONTROL_PLANE_URL": ""}, wantErr: []string{"CONTROL_PLANE_URL is required"}},
		{name: "control plane URL without scheme", env: map[string]string{"CONTROL_PLANE_URL": "control-plane:8000"}, wantErr: []string{"CONTROL_PLANE_URL"}},
		{name: "control plane URL with another scheme", env: map[string]string{"CONTROL_PLANE_URL": "ftp://cp"}, wantErr: []string{"CONTROL_PLANE_URL"}},
		{name: "control plane URL with a query", env: map[string]string{"CONTROL_PLANE_URL": "http://cp?x=1"}, wantErr: []string{"CONTROL_PLANE_URL"}},
		{name: "internal token missing", env: map[string]string{"INTERNAL_TOKEN": ""}, wantErr: []string{"INTERNAL_TOKEN is required"}},
		{name: "redis URL missing", env: map[string]string{"REDIS_URL": ""}, wantErr: []string{"REDIS_URL is required"}},
		{name: "redis URL with another scheme", env: map[string]string{"REDIS_URL": "http://redis:6379"}, wantErr: []string{"REDIS_URL: want"}},
		{name: "redis URL without host", env: map[string]string{"REDIS_URL": "redis://"}, wantErr: []string{"REDIS_URL: want"}},
		{name: "config wait not a duration", env: map[string]string{"CONFIG_WAIT": "soon"}, wantErr: []string{"CONFIG_WAIT"}},
		{name: "config wait of zero", env: map[string]string{"CONFIG_WAIT": "0s"}, wantErr: []string{"whole seconds"}},
		{name: "config wait above the endpoint's limit", env: map[string]string{"CONFIG_WAIT": "61s"}, wantErr: []string{"whole seconds"}},
		{name: "base domain with a port", env: map[string]string{"GATEWAY_BASE_DOMAIN": "localhost:8081"}, wantErr: []string{"GATEWAY_BASE_DOMAIN"}},
		{name: "base domain with a scheme", env: map[string]string{"GATEWAY_BASE_DOMAIN": "https://gw.example.com"}, wantErr: []string{"GATEWAY_BASE_DOMAIN"}},
		{name: "base domain with an empty label", env: map[string]string{"GATEWAY_BASE_DOMAIN": "gw..example.com"}, wantErr: []string{"GATEWAY_BASE_DOMAIN"}},
		{name: "base domain label starting with a dash", env: map[string]string{"GATEWAY_BASE_DOMAIN": "-gw.example.com"}, wantErr: []string{"GATEWAY_BASE_DOMAIN"}},
		{name: "base domain too long", env: map[string]string{"GATEWAY_BASE_DOMAIN": strings.Repeat("a.", 127) + "com"}, wantErr: []string{"GATEWAY_BASE_DOMAIN"}},
		{name: "base domain label too long", env: map[string]string{"GATEWAY_BASE_DOMAIN": strings.Repeat("a", 64) + ".com"}, wantErr: []string{"GATEWAY_BASE_DOMAIN"}},
		{name: "config wait with a fraction", env: map[string]string{"CONFIG_WAIT": "1500ms"}, wantErr: []string{"whole seconds"}},
		{name: "upstream allow entry with a scheme", env: map[string]string{"UPSTREAM_ALLOW": "mock-backend,http://redis"}, wantErr: []string{"UPSTREAM_ALLOW", "http://redis"}},
		{name: "upstream allow network out of range", env: map[string]string{"UPSTREAM_ALLOW": "10.0.0.0/40"}, wantErr: []string{"UPSTREAM_ALLOW"}},
		{
			name:    "every problem is reported at once",
			env:     map[string]string{"LISTEN_ADDR": "x", "CONTROL_PLANE_URL": "", "INTERNAL_TOKEN": ""},
			wantErr: []string{"LISTEN_ADDR", "CONTROL_PLANE_URL", "INTERNAL_TOKEN"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			setEnv(t, tt.env)

			got, err := Load()

			if len(tt.wantErr) > 0 {
				if err == nil {
					t.Fatalf("Load() error = nil, want one mentioning %q", tt.wantErr)
				}
				for _, want := range tt.wantErr {
					if !strings.Contains(err.Error(), want) {
						t.Errorf("Load() error = %v, want it to mention %q", err, want)
					}
				}
				return
			}
			if err != nil {
				t.Fatalf("Load() unexpected error: %v", err)
			}
			if got.Egress == nil {
				t.Fatal("Load() left Egress nil; the deny list must always apply")
			}
			got.Egress = nil // a pointer: checked on its own below
			if got != tt.want {
				t.Errorf("Load() = %+v, want %+v", got, tt.want)
			}
		})
	}
}

// UPSTREAM_ALLOW becomes the egress policy: its names and networks are
// allowed, everything internal not on it stays refused. Unset, nothing
// internal is allowed (the production default).
func TestLoadUpstreamAllow(t *testing.T) {
	setEnv(t, map[string]string{"UPSTREAM_ALLOW": " mock-backend , 10.20.0.0/16,,"})
	s, err := Load()
	if err != nil {
		t.Fatalf("Load() error: %v", err)
	}
	if !s.Egress.AllowsHost("mock-backend") || s.Egress.AllowsHost("redis") {
		t.Error("the allowed name isn't allowed, or another name is")
	}
	if !s.Egress.AllowsAddr(netip.MustParseAddr("10.20.1.2")) || s.Egress.AllowsAddr(netip.MustParseAddr("10.21.1.2")) {
		t.Error("the allowed network isn't allowed, or another internal address is")
	}

	setEnv(t, nil)
	s, err = Load()
	if err != nil {
		t.Fatalf("Load() error: %v", err)
	}
	if s.Egress.AllowsHost("mock-backend") || s.Egress.AllowsAddr(netip.MustParseAddr("10.20.1.2")) {
		t.Error("without UPSTREAM_ALLOW something internal is allowed")
	}
}

// The token must never leak through printing or logging.
func TestSecretIsNeverPrinted(t *testing.T) {
	s := Settings{InternalToken: "s3cret"}

	var logs bytes.Buffer
	slog.New(slog.NewJSONHandler(&logs, nil)).Info("settings", "token", s.InternalToken)

	outputs := map[string]string{
		"%v":  fmt.Sprintf("%v", s),
		"%+v": fmt.Sprintf("%+v", s),
		"%#v": fmt.Sprintf("%#v", s),
		"%s":  fmt.Sprintf("%s", s.InternalToken),
		"log": logs.String(),
	}
	for name, out := range outputs {
		if strings.Contains(out, "s3cret") {
			t.Errorf("%s leaks the token: %s", name, out)
		}
	}
	if s.InternalToken.Reveal() != "s3cret" {
		t.Errorf("Reveal() = %q, want the real value", s.InternalToken.Reveal())
	}
}

// A TLS Redis URL with a password is accepted, and the password never
// shows when the settings are printed.
func TestLoadAcceptsTLSRedisURLAndHidesIt(t *testing.T) {
	const withPassword = "rediss://:pa55word@cache.example.com:6380/1"
	setEnv(t, map[string]string{"REDIS_URL": withPassword})

	got, err := Load()
	if err != nil {
		t.Fatalf("Load() error: %v", err)
	}
	if got.RedisURL.Reveal() != withPassword {
		t.Errorf("RedisURL = %q", got.RedisURL.Reveal())
	}
	if out := fmt.Sprintf("%+v", got); strings.Contains(out, "pa55word") {
		t.Errorf("the Redis password leaks when printed: %s", out)
	}
}

// A bad REDIS_URL is reported without printing it (it may hold a password).
func TestBadRedisURLIsNotPrinted(t *testing.T) {
	setEnv(t, map[string]string{"REDIS_URL": "http://:pa55word@redis:6379"})

	_, err := Load()
	if err == nil || strings.Contains(err.Error(), "pa55word") {
		t.Errorf("Load() error = %v; want an error that does not show the URL", err)
	}
}
