package settings

import (
	"bytes"
	"fmt"
	"log/slog"
	"strings"
	"testing"
	"time"
)

// required holds valid values for the variables that have no default.
var required = map[string]string{
	"CONTROL_PLANE_URL": "http://control-plane:8000",
	"INTERNAL_TOKEN":    "s3cret",
}

// defaults is what Load returns when only the required variables are set.
var defaults = Settings{
	ListenAddr:      ":8080",
	ShutdownTimeout: 5 * time.Second,
	ControlPlaneURL: "http://control-plane:8000",
	InternalToken:   "s3cret",
	ConfigWait:      30 * time.Second,
}

// setEnv starts from a clean slate, whatever the machine has set, then
// applies the required values and the case's own values on top.
func setEnv(t *testing.T, env map[string]string) {
	t.Helper()
	for _, k := range []string{"LISTEN_ADDR", "SHUTDOWN_TIMEOUT", "CONTROL_PLANE_URL", "INTERNAL_TOKEN", "CONFIG_WAIT"} {
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
			env:  map[string]string{"LISTEN_ADDR": "", "SHUTDOWN_TIMEOUT": "", "CONFIG_WAIT": ""},
			want: defaults,
		},
		{
			name: "custom values",
			env: map[string]string{
				"LISTEN_ADDR":       "127.0.0.1:9090",
				"SHUTDOWN_TIMEOUT":  "1500ms",
				"CONTROL_PLANE_URL": "https://cp.example.com/",
				"CONFIG_WAIT":       "60s",
			},
			want: Settings{
				ListenAddr:      "127.0.0.1:9090",
				ShutdownTimeout: 1500 * time.Millisecond,
				ControlPlaneURL: "https://cp.example.com", // trailing slash dropped
				InternalToken:   "s3cret",
				ConfigWait:      60 * time.Second,
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
		{name: "config wait not a duration", env: map[string]string{"CONFIG_WAIT": "soon"}, wantErr: []string{"CONFIG_WAIT"}},
		{name: "config wait of zero", env: map[string]string{"CONFIG_WAIT": "0s"}, wantErr: []string{"whole seconds"}},
		{name: "config wait above the endpoint's limit", env: map[string]string{"CONFIG_WAIT": "61s"}, wantErr: []string{"whole seconds"}},
		{name: "config wait with a fraction", env: map[string]string{"CONFIG_WAIT": "1500ms"}, wantErr: []string{"whole seconds"}},
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
			if got != tt.want {
				t.Errorf("Load() = %+v, want %+v", got, tt.want)
			}
		})
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
