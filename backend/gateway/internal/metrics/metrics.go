// Package metrics exposes the gateway's numbers to Prometheus (PLAN.md
// section 7, V2.3), on a port of their own, never the public one.
//
// A request costs a few counter and histogram updates, fed from the usage
// event the logger builds anyway. Everything else (requests in flight,
// target health, the config's sequence and last sync, dropped usage events)
// is read from the gateway's own state only when Prometheus scrapes.
//
// Labels stay bounded: workspace and route ids come from the config, never
// from the request; a request that matched no route is route "none", and
// statuses are grouped by class ("2xx" ... "5xx").
package metrics

import (
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/usage"
)

// Sources is the gateway state read at scrape time. *inflight.Limiter,
// *upstream.Registry, *config.Store and *usage.Recorder fit them.
type Sources struct {
	InFlight interface {
		Each(fn func(workspaceID string, inFlight int))
	}
	Targets interface {
		EachTarget(fn func(workspaceID, serviceID, targetID string, usable bool))
	}
	Config interface {
		Seq() int64
		LastSync() time.Time
	}
	Usage interface {
		Written() int64
		Dropped() int64
	}
}

// Metrics records requests and serves everything in the Prometheus format.
type Metrics struct {
	registry *prometheus.Registry
	requests *prometheus.CounterVec
	latency  *prometheus.HistogramVec
	upstream *prometheus.HistogramVec
	refusals *prometheus.CounterVec
}

// Latency buckets in seconds: the gateway's own work is well under a
// millisecond, backends take milliseconds to seconds.
var buckets = []float64{.0005, .001, .0025, .005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10}

// New builds the metrics on a registry of their own (no global state).
func New(s Sources) *Metrics {
	m := &Metrics{
		registry: prometheus.NewRegistry(),
		requests: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "gateway_requests_total",
			Help: "Requests answered for a workspace, by route and status class.",
		}, []string{"workspace", "route", "status"}),
		latency: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "gateway_request_duration_seconds",
			Help:    "Whole request time at the gateway, by workspace.",
			Buckets: buckets,
		}, []string{"workspace"}),
		upstream: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "gateway_upstream_duration_seconds",
			Help:    "Time waiting for the backend, by workspace (forwarded requests only).",
			Buckets: buckets,
		}, []string{"workspace"}),
		refusals: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "gateway_ratelimit_refusals_total",
			Help: "Requests refused by a limit, by workspace and reason (the log's ratelimit value).",
		}, []string{"workspace", "reason"}),
	}
	m.registry.MustRegister(
		m.requests, m.latency, m.upstream, m.refusals,
		&state{s: s},
		collectors.NewGoCollector(),
		collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}),
	)
	return m
}

// Record counts one answered request (the logger calls it with the usage
// event it built for the request).
func (m *Metrics) Record(e usage.Event) {
	route := e.RouteID
	if route == "" {
		route = "none"
	}
	m.requests.WithLabelValues(e.WorkspaceID, route, statusClass(e.Status)).Inc()
	m.latency.WithLabelValues(e.WorkspaceID).Observe(e.LatencyMS / 1000)
	if e.TargetID != "" {
		m.upstream.WithLabelValues(e.WorkspaceID).Observe(e.UpstreamMS / 1000)
	}
	if strings.HasPrefix(e.RateLimit, "limited") || strings.HasPrefix(e.RateLimit, "blocked") {
		m.refusals.WithLabelValues(e.WorkspaceID, e.RateLimit).Inc()
	}
}

// Handler serves GET /metrics.
func (m *Metrics) Handler() http.Handler {
	return promhttp.HandlerFor(m.registry, promhttp.HandlerOpts{})
}

func statusClass(status int) string {
	if status < 100 || status > 599 {
		return "other"
	}
	return strconv.Itoa(status/100) + "xx"
}

// state reads the gateway's state when Prometheus scrapes.
type state struct{ s Sources }

var (
	inFlightDesc = prometheus.NewDesc("gateway_requests_in_flight",
		"Requests of the workspace on their way to a backend right now (this instance).",
		[]string{"workspace"}, nil)
	targetDesc = prometheus.NewDesc("gateway_target_up",
		"1 when the target takes traffic (healthy and not set aside), 0 otherwise.",
		[]string{"workspace", "service", "target"}, nil)
	seqDesc = prometheus.NewDesc("gateway_config_seq",
		"The config sequence this gateway serves.", nil, nil)
	syncDesc = prometheus.NewDesc("gateway_config_last_sync_timestamp_seconds",
		"When the control plane last answered a config poll (0: never).", nil, nil)
	writtenDesc = prometheus.NewDesc("gateway_usage_events_written_total",
		"Usage events written to the Redis stream.", nil, nil)
	droppedDesc = prometheus.NewDesc("gateway_usage_events_dropped_total",
		"Usage events lost: buffer full or Redis unavailable.", nil, nil)
)

func (c *state) Describe(ch chan<- *prometheus.Desc) {
	for _, d := range []*prometheus.Desc{inFlightDesc, targetDesc, seqDesc, syncDesc, writtenDesc, droppedDesc} {
		ch <- d
	}
}

func (c *state) Collect(ch chan<- prometheus.Metric) {
	c.s.InFlight.Each(func(ws string, n int) {
		ch <- prometheus.MustNewConstMetric(inFlightDesc, prometheus.GaugeValue, float64(n), ws)
	})
	c.s.Targets.EachTarget(func(ws, service, target string, usable bool) {
		up := 0.0
		if usable {
			up = 1
		}
		ch <- prometheus.MustNewConstMetric(targetDesc, prometheus.GaugeValue, up, ws, service, target)
	})
	ch <- prometheus.MustNewConstMetric(seqDesc, prometheus.GaugeValue, float64(c.s.Config.Seq()))
	last := 0.0
	if t := c.s.Config.LastSync(); !t.IsZero() {
		last = float64(t.UnixNano()) / 1e9
	}
	ch <- prometheus.MustNewConstMetric(syncDesc, prometheus.GaugeValue, last)
	ch <- prometheus.MustNewConstMetric(writtenDesc, prometheus.CounterValue, float64(c.s.Usage.Written()))
	ch <- prometheus.MustNewConstMetric(droppedDesc, prometheus.CounterValue, float64(c.s.Usage.Dropped()))
}
