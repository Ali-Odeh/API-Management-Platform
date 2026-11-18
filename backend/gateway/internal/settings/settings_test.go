package settings

import (
	"strings"
	"testing"
	"time"
)

func TestLoad(t *testing.T) {
	tests := []struct {
		name    string
		env     map[string]string
		want    Settings
		wantErr string // empty means no error expected
	}{
		{
			name: "defaults when nothing is set",
			env:  map[string]string{},
			want: Settings{ListenAddr: ":8080", ShutdownTimeout: 5 * time.Second},
		},
		{
			name: "empty values count as unset",
			env:  map[string]string{"LISTEN_ADDR": "", "SHUTDOWN_TIMEOUT": ""},
			want: Settings{ListenAddr: ":8080", ShutdownTimeout: 5 * time.Second},
		},
		{
			name: "custom values",
			env:  map[string]string{"LISTEN_ADDR": "127.0.0.1:9090", "SHUTDOWN_TIMEOUT": "1500ms"},
			want: Settings{ListenAddr: "127.0.0.1:9090", ShutdownTimeout: 1500 * time.Millisecond},
		},
		{
			name:    "listen address without a colon",
			env:     map[string]string{"LISTEN_ADDR": "8080"},
			wantErr: "LISTEN_ADDR",
		},
		{
			name:    "shutdown timeout that is not a duration",
			env:     map[string]string{"SHUTDOWN_TIMEOUT": "five"},
			wantErr: "SHUTDOWN_TIMEOUT",
		},
		{
			name:    "shutdown timeout of zero",
			env:     map[string]string{"SHUTDOWN_TIMEOUT": "0s"},
			wantErr: "greater than zero",
		},
		{
			name:    "negative shutdown timeout",
			env:     map[string]string{"SHUTDOWN_TIMEOUT": "-1s"},
			wantErr: "greater than zero",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Start every case from a clean slate, whatever the machine has set.
			t.Setenv("LISTEN_ADDR", "")
			t.Setenv("SHUTDOWN_TIMEOUT", "")
			for k, v := range tt.env {
				t.Setenv(k, v)
			}

			got, err := Load()

			if tt.wantErr != "" {
				if err == nil || !strings.Contains(err.Error(), tt.wantErr) {
					t.Fatalf("Load() error = %v, want it to mention %q", err, tt.wantErr)
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
