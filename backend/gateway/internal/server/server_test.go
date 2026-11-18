package server

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/settings"
)

// newTestServer builds a server that logs nothing and listens on a free port.
func newTestServer(t *testing.T, shutdownTimeout time.Duration) (*Server, net.Listener) {
	t.Helper()
	srv := New(settings.Settings{ListenAddr: "127.0.0.1:0", ShutdownTimeout: shutdownTimeout}, slog.New(slog.DiscardHandler))
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	return srv, ln
}

func TestHealthEndpoint(t *testing.T) {
	srv := New(settings.Settings{ListenAddr: ":0", ShutdownTimeout: time.Second}, slog.New(slog.DiscardHandler))

	tests := []struct {
		name       string
		method     string
		path       string
		wantStatus int
		wantBody   string // checked only when not empty
	}{
		{name: "GET returns ok", method: http.MethodGet, path: "/health", wantStatus: http.StatusOK, wantBody: `{"status":"ok"}` + "\n"},
		{name: "HEAD is allowed", method: http.MethodHead, path: "/health", wantStatus: http.StatusOK},
		{name: "POST is not allowed", method: http.MethodPost, path: "/health", wantStatus: http.StatusMethodNotAllowed},
		{name: "unknown path", method: http.MethodGet, path: "/nope", wantStatus: http.StatusNotFound},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rec := httptest.NewRecorder()
			req := httptest.NewRequest(tt.method, tt.path, nil)

			srv.http.Handler.ServeHTTP(rec, req)

			if rec.Code != tt.wantStatus {
				t.Fatalf("status = %d, want %d", rec.Code, tt.wantStatus)
			}
			if tt.wantStatus == http.StatusOK {
				if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
					t.Errorf("Content-Type = %q, want application/json", ct)
				}
			}
			if tt.wantBody != "" && rec.Body.String() != tt.wantBody {
				t.Errorf("body = %q, want %q", rec.Body.String(), tt.wantBody)
			}
		})
	}
}

// A request that is running when shutdown starts must still get its answer.
func TestServeFinishesInFlightRequestsOnShutdown(t *testing.T) {
	srv, ln := newTestServer(t, 5*time.Second)

	started := make(chan struct{})
	release := make(chan struct{})
	srv.http.Handler = http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		close(started)
		<-release // stay "in flight" until the test says so
		_, _ = io.WriteString(w, "done")
	})

	ctx, cancel := context.WithCancel(t.Context())
	serveDone := make(chan error, 1)
	go func() { serveDone <- srv.Serve(ctx, ln) }()

	type result struct {
		body string
		err  error
	}
	got := make(chan result, 1)
	go func() {
		resp, err := http.Get("http://" + ln.Addr().String() + "/slow")
		if err != nil {
			got <- result{err: err}
			return
		}
		defer resp.Body.Close()
		b, err := io.ReadAll(resp.Body)
		got <- result{body: string(b), err: err}
	}()

	<-started
	cancel() // like docker stop: start the shutdown while the request runs

	// Serve must keep waiting while the request is still running.
	select {
	case err := <-serveDone:
		t.Fatalf("Serve returned before the in-flight request finished: %v", err)
	case <-time.After(100 * time.Millisecond):
	}

	close(release)

	r := <-got
	if r.err != nil || r.body != "done" {
		t.Fatalf("in-flight request: body %q, err %v; want \"done\" and no error", r.body, r.err)
	}
	if err := <-serveDone; err != nil {
		t.Fatalf("Serve() = %v, want nil after a clean shutdown", err)
	}
}

// When requests outlive the shutdown timeout, Serve gives up and says so.
func TestServeReportsShutdownTimeout(t *testing.T) {
	srv, ln := newTestServer(t, 50*time.Millisecond)

	started := make(chan struct{})
	release := make(chan struct{})
	t.Cleanup(func() { close(release) })
	srv.http.Handler = http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		close(started)
		<-release
	})

	ctx, cancel := context.WithCancel(t.Context())
	serveDone := make(chan error, 1)
	go func() { serveDone <- srv.Serve(ctx, ln) }()

	go func() {
		resp, err := http.Get("http://" + ln.Addr().String() + "/stuck")
		if err == nil {
			resp.Body.Close()
		}
	}()

	<-started
	cancel()

	err := <-serveDone
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("Serve() = %v, want an error wrapping context.DeadlineExceeded", err)
	}
}

func TestServeReturnsErrorWhenServingFails(t *testing.T) {
	srv, ln := newTestServer(t, time.Second)
	ln.Close() // a closed listener makes http.Server.Serve fail at once

	err := srv.Serve(t.Context(), ln)
	if err == nil {
		t.Fatal("Serve() = nil, want an error for a closed listener")
	}
}

func TestListenAndServeFailsWhenPortIsTaken(t *testing.T) {
	busy, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	defer busy.Close()

	srv := New(settings.Settings{ListenAddr: busy.Addr().String(), ShutdownTimeout: time.Second}, slog.New(slog.DiscardHandler))

	if err := srv.ListenAndServe(t.Context()); err == nil {
		t.Fatal("ListenAndServe() = nil, want an error for a port in use")
	}
}

// End to end on a real port: start, answer /health, stop cleanly.
func TestListenAndServeRunsAndStops(t *testing.T) {
	// Find a free port, then let the server open it itself.
	probe, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	addr := probe.Addr().String()
	probe.Close()

	srv := New(settings.Settings{ListenAddr: addr, ShutdownTimeout: time.Second}, slog.New(slog.DiscardHandler))
	ctx, cancel := context.WithCancel(t.Context())
	serveDone := make(chan error, 1)
	go func() { serveDone <- srv.ListenAndServe(ctx) }()

	// The server needs a moment to start; retry the probe briefly.
	deadline := time.Now().Add(2 * time.Second)
	for {
		err = Probe(t.Context(), addr)
		if err == nil || time.Now().After(deadline) {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if err != nil {
		t.Fatalf("Probe() = %v, want nil once the server runs", err)
	}

	cancel()
	if err := <-serveDone; err != nil {
		t.Fatalf("ListenAndServe() = %v, want nil after a clean shutdown", err)
	}
}
