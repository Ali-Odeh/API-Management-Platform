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
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/proxy"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/server"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/settings"
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
		return serve(cfg, log)
	case len(args) == 1 && args[0] == "healthcheck":
		return healthcheck(cfg)
	default:
		fmt.Fprintf(os.Stderr, "unknown arguments %q\nusage: gateway [healthcheck]\n", args)
		return 2
	}
}

// serve runs the gateway until Ctrl+C (SIGINT) or `docker stop` (SIGTERM).
func serve(cfg settings.Settings, log *slog.Logger) int {
	// ctx is cancelled when one of these signals arrives. Everything that
	// runs long (the server and the config poller) watches it.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	log.Info("gateway starting", "listen_addr", cfg.ListenAddr, "control_plane_url", cfg.ControlPlaneURL)

	// The server starts at once, without waiting for the first config, so a
	// slow control plane never keeps the gateway from starting.
	store := &config.Store{}
	poller := config.NewPoller(cfg, store, log)
	var background sync.WaitGroup
	background.Go(func() { poller.Run(ctx) })

	// API traffic: match the route (404/405/503 answered here), then the
	// matched request goes on. The 501 placeholder becomes the proxy in 3.4.
	api := proxy.NewRouter(store, proxy.NotImplemented())

	err := server.New(cfg, api, log).ListenAndServe(ctx)
	stop()            // if the server failed on its own, stop the poller too
	background.Wait() // never exit while the poller is still running

	if err != nil {
		log.Error("gateway stopped with an error", "error", err)
		return 1
	}
	log.Info("gateway stopped")
	return 0
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
