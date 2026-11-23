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
	"errors"
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
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/egress"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/inflight"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/metrics"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/middleware"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/proxy"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/ratelimit"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/redisconn"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/server"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/settings"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/tenant"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/upstream"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/usage"
)

// usageRedisTimeout bounds one batch of usage events (V2.1). Their client is
// separate from the rate limiter's, whose checks must answer in 50 ms.
const usageRedisTimeout = usage.DefaultWriteTimeout

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

	// One balancer per service, following the config: it starts and stops
	// the targets' active health checks as services come and go.
	targets := upstream.NewRegistry(ctx, cfg.Egress, log)
	background.Go(func() { targets.Run(ctx, store) })

	// Redis is only reached on the first limited request; if it is down,
	// requests are let through (fail open) until it is back.
	rdb, err := ratelimit.NewClient(cfg.RedisURL.Reveal())
	if err != nil {
		log.Error("cannot start", "error", err)
		return 1
	}
	defer rdb.Close()
	usageRDB, err := redisconn.Open(cfg.RedisURL.Reveal(), usageRedisTimeout)
	if err != nil {
		log.Error("cannot start", "error", err)
		return 1
	}
	defer usageRDB.Close()

	// Usage events are written until the server has stopped, so the last
	// requests' events are written too: the recorder gets its own context,
	// ended only after ListenAndServe returns.
	recorder := usage.NewRecorder(usage.NewStream(usageRDB), log, usage.Options{})
	usageCtx, stopUsage := context.WithCancel(context.WithoutCancel(ctx))
	var usageDone sync.WaitGroup
	usageDone.Go(func() { recorder.Run(usageCtx) })

	// Prometheus scrapes /metrics on a port of its own (V2.3).
	inFlight := &inflight.Limiter{}
	gatewayMetrics := metrics.New(metrics.Sources{InFlight: inFlight, Targets: targets, Config: store, Usage: recorder})
	metricsDone := serveMetrics(ctx, cfg, gatewayMetrics.Handler(), log)

	err = server.New(cfg, apiHandler(deps{
		source:     store,
		baseDomain: cfg.BaseDomain,
		egress:     cfg.Egress,
		targets:    targets,
		limiter:    ratelimit.New(rdb),
		inFlight:   inFlight,
		usage:      middleware.UsageRecorders{recorder, gatewayMetrics},
		log:        log,
	}), log).ListenAndServe(ctx)
	stop() // if the server failed on its own, stop the poller and checkers too
	<-metricsDone
	stopUsage()       // no request is running any more: write the last events
	background.Wait() // never exit while the poller or a health check is still running
	usageDone.Wait()

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
//	  → CORS (preflight 204) → body limit (413) → auth (401/403)
//	  → rate limit (429) → in flight (503) → forwarder (502/503/504)
//
// The forwarder sends each request to one of its service's healthy targets,
// by weight (503 when none is left), and connects only to upstreams the
// egress policy allows (SSRF protection): an internal address not on
// UPSTREAM_ALLOW gets 502.
//
// CORS comes right after the router: a browser's preflight carries no API
// key, so it is answered before auth, and costs no rate-limit token. The body
// limit comes before anything is forwarded.
//
// Recover sits inside the logger, so a panic's 500 shows in the request log.
// Tenant finds the workspace from the Host before anything else: matching,
// keys and limits all happen inside that one workspace. Auth comes after
// the router: it needs the route (auth_required) and the workspace's keys.
// The rate limit comes after auth: it needs the consumer, and refused
// requests must cost no budget. It checks the workspace's bucket on every
// request, public routes included. The in-flight limit comes last, so only
// requests on their way to a backend hold one of the workspace's slots.
// GET /health is answered by the server before this chain, so Docker's
// health checks don't fill the log.
//
// The logger also hands each answered request's usage event to the
// recorder (V2.1), which writes it to Redis in the background.
func apiHandler(d deps) http.Handler {
	log := d.log
	failures, _ := d.limiter.(middleware.FailureCounter) // the real limiter counts wrong keys
	afterMatch := middleware.Chain(
		proxy.NewForwarder(log, d.egress, d.targets),
		middleware.CORS(log),
		middleware.BodyLimit(log),
		middleware.Auth(log, failures),
		middleware.RateLimit(d.limiter, log),
		middleware.InFlight(d.inFlight, log),
	)
	return middleware.Chain(
		proxy.NewRouter(afterMatch),
		middleware.RequestID,
		middleware.Logger(log, d.usage),
		middleware.Recover(log),
		tenant.Middleware(d.source, d.baseDomain),
	)
}

// deps is what the request chain is built from.
type deps struct {
	source     tenant.SnapshotSource // the current config
	baseDomain string                // GATEWAY_BASE_DOMAIN: workspaces are its subdomains
	egress     *egress.Policy        // which upstreams may be reached (SSRF)
	targets    *upstream.Registry    // the services' balancers
	limiter    middleware.Allower    // the Redis rate limiter
	inFlight   *inflight.Limiter     // requests in flight per workspace
	usage      middleware.UsageRecorder
	log        *slog.Logger
}

// serveMetrics serves /metrics on cfg.MetricsAddr until ctx ends; the
// returned channel closes once it has stopped. A port that can't be opened
// is logged, not fatal: the API matters more than its metrics. An empty
// address (settings built by hand, in tests) serves nothing.
func serveMetrics(ctx context.Context, cfg settings.Settings, h http.Handler, log *slog.Logger) <-chan struct{} {
	done := make(chan struct{})
	if cfg.MetricsAddr == "" {
		close(done)
		return done
	}
	mux := http.NewServeMux()
	mux.Handle("GET /metrics", h)
	srv := &http.Server{Addr: cfg.MetricsAddr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		defer close(done)
		go func() {
			<-ctx.Done()
			shutdown, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			_ = srv.Shutdown(shutdown)
		}()
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("metrics server stopped", "error", err, "addr", cfg.MetricsAddr)
		}
	}()
	return done
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
