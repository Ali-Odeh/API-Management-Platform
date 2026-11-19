package main

import (
	"bytes"
	"encoding/json"
	"testing"
)

func TestRunExitCodes(t *testing.T) {
	tests := []struct {
		name              string
		listenAddr        string
		noControlPlaneURL bool // leave the required CONTROL_PLANE_URL unset
		args              []string
		want              int
	}{
		{name: "unknown command", listenAddr: ":8080", args: []string{"bogus"}, want: 2},
		{name: "too many arguments", listenAddr: ":8080", args: []string{"healthcheck", "extra"}, want: 2},
		{name: "invalid settings stop startup", listenAddr: "8080", args: nil, want: 1},
		{name: "missing required settings stop startup", listenAddr: ":8080", noControlPlaneURL: true, args: nil, want: 1},
		{name: "healthcheck with nothing running", listenAddr: "127.0.0.1:1", args: []string{"healthcheck"}, want: 1},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			cpURL := "http://control-plane:8000"
			if tt.noControlPlaneURL {
				cpURL = ""
			}
			t.Setenv("LISTEN_ADDR", tt.listenAddr)
			t.Setenv("SHUTDOWN_TIMEOUT", "")
			t.Setenv("CONTROL_PLANE_URL", cpURL)
			t.Setenv("INTERNAL_TOKEN", "t")
			t.Setenv("CONFIG_WAIT", "")
			t.Setenv("REDIS_URL", "redis://127.0.0.1:1/0")

			if got := run(tt.args); got != tt.want {
				t.Errorf("run(%q) = %d, want %d", tt.args, got, tt.want)
			}
		})
	}
}

func TestLoggerWritesJSONWithTsKey(t *testing.T) {
	var buf bytes.Buffer
	newLogger(&buf).Info("hello", "route_id", "r1")

	var line map[string]any
	if err := json.Unmarshal(buf.Bytes(), &line); err != nil {
		t.Fatalf("log line is not JSON: %v (%q)", err, buf.String())
	}
	if _, ok := line["ts"]; !ok {
		t.Errorf("log line %v has no \"ts\" key", line)
	}
	if _, ok := line["time"]; ok {
		t.Errorf("log line %v still has a \"time\" key", line)
	}
	if line["msg"] != "hello" || line["route_id"] != "r1" {
		t.Errorf("log line %v is missing msg or route_id", line)
	}
}
