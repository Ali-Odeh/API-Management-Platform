// Command gateway is the API gateway (data plane): every API request passes
// through it on its way to a backend service.
//
// Usage:
//
//	gateway              run the gateway
//	gateway healthcheck  exit 0 if the running gateway answers /health
//
// main only wires the pieces together; the logic lives in internal/.
package main

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/middleware"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/proxy"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/ratelimit"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/server"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/settings"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/tenant"
)

func main() {
	// os.Exit skips deferred calls, so all the work (and its defers) happens
	// in run, and main only turns the result into the exit code.
	os.Exit(run(os.Args[1:]))
}

func run(args []string) int {
	log := newLogger(os.Stdout)

	cfg, err := settings.Load()
	if err != nil {
		log.Error("invalid settings", "error", err)
		return 1
	}

	switch {
	case len(args) == 0:
		// Cancelled by Ctrl+C (SIGINT) or `docker stop` (SIGTERM).
		ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		return serve(ctx, cfg, log)
	case len(args) == 1 && args[0] == "healthcheck":
		return healthcheck(cfg)
	default:
		fmt.Fprintf(os.Stderr, "unknown arguments %q\nusage: gateway [healthcheck]\n", args)
		return 2
	}
}

// serve runs the gateway until ctx is cancelled. Everything that runs long
// (the server and the config poller) watches ctx. Taking ctx as a parameter
// (instead of listening for signals here) lets a test start and stop the
// whole gateway.
func serve(ctx context.Context, cfg settings.Settings, log *slog.Logger) int {
	ctx, stop := context.WithCancel(ctx)
	defer stop()

	log.Info("gateway starting", "listen_addr", cfg.ListenAddr, "control_plane_url", cfg.ControlPlaneURL)

	// The server starts at once, without waiting for the first config, so a
	// slow control plane never keeps the gateway from starting.
	store := &config.Store{}
	poller := config.NewPoller(cfg, store, log)
	var background sync.WaitGroup
	background.Go(func() { poller.Run(ctx) })

	// Redis is only reached on the first limited request; if it is down,
	// requests are let through (fail open) until it is back.
	rdb, err := ratelimit.NewClient(cfg.RedisURL.Reveal())
	if err != nil {
		log.Error("cannot start", "error", err)
		return 1
	}
	defer rdb.Close()

	err = server.New(cfg, apiHandler(store, cfg.BaseDomain, ratelimit.New(rdb), log), log).ListenAndServe(ctx)
	stop()            // if the server failed on its own, stop the poller too
	background.Wait() // never exit while the poller is still running

	if err != nil {
		log.Error("gateway stopped with an error", "error", err)
		return 1
	}
	log.Info("gateway stopped")
	return 0
}

// apiHandler builds the path of every API request, outermost first:
//
//	request ID → logger → recover → tenant (503/404/403) → router (400/404/405)
//	  → auth (401/403) → rate limit (429) → forwarder (502/504)
//
// Recover sits inside the logger, so a panic's 500 shows in the request log.
// Tenant finds the workspace from the Host before anything else: matching,
// keys and limits all happen inside that one workspace. Auth comes after
// the router: it needs the route (auth_required) and the workspace's keys.
// The rate limit comes after auth: it needs the consumer, and refused
// requests must cost no budget. It checks the workspace's bucket on every
// request, public routes included.
// GET /health is answered by the server before this chain, so Docker's
// health checks don't fill the log.
func apiHandler(source tenant.SnapshotSource, baseDomain string, limiter middleware.Allower, log *slog.Logger) http.Handler {
	afterMatch := middleware.Chain(
		proxy.NewForwarder(log),
		middleware.Auth(log),
		middleware.RateLimit(limiter, log),
	)
	return middleware.Chain(
		proxy.NewRouter(afterMatch),
		middleware.RequestID,
		middleware.Logger(log),
		middleware.Recover(log),
		tenant.Middleware(source, baseDomain),
	)
}

// healthcheck is what Docker runs inside the container to ask "are you up?".
func healthcheck(cfg settings.Settings) int {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	if err := server.Probe(ctx, cfg.ListenAddr); err != nil {
		fmt.Fprintln(os.Stderr, "unhealthy:", err)
		return 1
	}
	return 0
}

// newLogger writes one JSON object per line. The time key is "ts", the same
// as the request log line in PLAN.md section 7.
func newLogger(w io.Writer) *slog.Logger {
	return slog.New(slog.NewJSONHandler(w, &slog.HandlerOptions{
		ReplaceAttr: func(groups []string, a slog.Attr) slog.Attr {
			if a.Key == slog.TimeKey && len(groups) == 0 {
				a.Key = "ts"
			}
			return a
		},
	}))
}
