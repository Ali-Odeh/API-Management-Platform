// Package server owns the gateway's HTTP server: its timeouts, its own
// endpoints (/health) and a graceful shutdown.
//
// The proxy (step 3.4) and middleware (3.5) plug into this server later;
// this package stays about running HTTP well, not about API traffic.
package server

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/settings"
)

const (
	// A client gets this long to send its request headers. It stops
	// "Slowloris" clients that hold connections open by sending headers
	// very slowly.
	readHeaderTimeout = 5 * time.Second
	// A keep-alive connection with no request for this long is closed.
	idleTimeout = 60 * time.Second
	// No ReadTimeout or WriteTimeout on purpose: they would cut off large
	// uploads and slow but valid upstream responses. Each route gets its own
	// timeout in step 3.4 instead.
)

// Server is the gateway's HTTP server.
type Server struct {
	http            *http.Server
	shutdownTimeout time.Duration
	log             *slog.Logger
}

// New builds a server from the settings. It does not start listening yet.
func New(cfg settings.Settings, log *slog.Logger) *Server {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", handleHealth)

	return &Server{
		http: &http.Server{
			Addr:              cfg.ListenAddr,
			Handler:           mux,
			ReadHeaderTimeout: readHeaderTimeout,
			IdleTimeout:       idleTimeout,
			// net/http reports problems (bad TLS, broken connections) through
			// a classic logger; route them into our JSON logs.
			ErrorLog: slog.NewLogLogger(log.Handler(), slog.LevelWarn),
		},
		shutdownTimeout: cfg.ShutdownTimeout,
		log:             log,
	}
}

// ListenAndServe opens the listen address and serves until ctx is cancelled.
func (s *Server) ListenAndServe(ctx context.Context) error {
	ln, err := net.Listen("tcp", s.http.Addr)
	if err != nil {
		return fmt.Errorf("listen on %s: %w", s.http.Addr, err)
	}
	return s.Serve(ctx, ln)
}

// Serve handles requests on ln until ctx is cancelled, then shuts down
// gracefully: it stops accepting new connections and waits up to the
// shutdown timeout for requests already in progress.
//
// It returns nil after a clean shutdown, or an error if the server failed or
// requests were still running when the timeout ran out.
func (s *Server) Serve(ctx context.Context, ln net.Listener) error {
	// Buffered, so the goroutine can always send its result and exit, even
	// when nobody is reading any more.
	serveErr := make(chan error, 1)
	go func() {
		serveErr <- s.http.Serve(ln)
	}()
	s.log.Info("listening", "addr", ln.Addr().String())

	select {
	case err := <-serveErr:
		// Serve only returns early on a real failure.
		return fmt.Errorf("serve: %w", err)
	case <-ctx.Done():
	}

	s.log.Info("shutting down", "timeout", s.shutdownTimeout.String())
	// A fresh context: ctx is already cancelled, but shutdown needs time.
	shutdownCtx, cancel := context.WithTimeout(context.Background(), s.shutdownTimeout)
	defer cancel()

	if err := s.http.Shutdown(shutdownCtx); err != nil {
		// Time is up: drop the requests that are still running.
		_ = s.http.Close()
		return fmt.Errorf("graceful shutdown: %w", err)
	}
	// The goroutine now sends http.ErrServerClosed (expected) into the
	// buffered channel and exits; there is nothing left to check.
	return nil
}

// handleHealth tells Docker (and load balancers later) that the process is
// up and serving. It checks nothing else on purpose: a gateway whose control
// plane is down must stay "healthy" and keep serving its last config.
func handleHealth(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}
