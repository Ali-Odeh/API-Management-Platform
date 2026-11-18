package server

import (
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestProbe(t *testing.T) {
	healthy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer healthy.Close()

	unhealthy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer unhealthy.Close()

	// A port that was free a moment ago: nothing listens there now.
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	closedAddr := ln.Addr().String()
	ln.Close()

	_, healthyPort, _ := net.SplitHostPort(healthy.Listener.Addr().String())

	tests := []struct {
		name    string
		addr    string
		wantErr string // empty means no error expected
	}{
		{name: "200 is healthy", addr: healthy.Listener.Addr().String()},
		{name: "port only means loopback", addr: ":" + healthyPort},
		{name: "all interfaces means loopback", addr: "0.0.0.0:" + healthyPort},
		{name: "non-200 is unhealthy", addr: unhealthy.Listener.Addr().String(), wantErr: "status 503"},
		{name: "nothing listening", addr: closedAddr, wantErr: "call"},
		{name: "bad address", addr: "8080", wantErr: "listen address"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := Probe(t.Context(), tt.addr)

			if tt.wantErr == "" {
				if err != nil {
					t.Fatalf("Probe() = %v, want nil", err)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), tt.wantErr) {
				t.Fatalf("Probe() = %v, want an error mentioning %q", err, tt.wantErr)
			}
		})
	}
}
