// Package upstream picks the backend instance (target) for each request and
// keeps track of which targets are healthy (decision 14).
//
// Each service has a Balancer. It spreads requests over the service's
// usable targets with smooth weighted round robin, the way nginx does: over
// time every target gets its share in proportion to its weight, without
// sending several requests in a row to one target. A target is usable when
// the active health checks say it is healthy and real traffic hasn't just
// set it aside (passive checks).
package upstream

import (
	"log/slog"
	"net/url"
	"sync"
	"sync/atomic"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
)

// Balancer picks targets for one service. It is built for one fingerprint
// of the service (its targets and health settings) and lives as long as
// that stays the same, so health state survives other config changes.
type Balancer struct {
	serviceID   string
	workspaceID string
	fingerprint string
	health      config.Health
	targets     []*member
	log         *slog.Logger
	now         func() time.Time

	mu sync.Mutex // guards every member's current weight

	stop func() // stops the active checkers
}

// member is one target and what the balancer knows about it.
type member struct {
	target config.Target

	current int // smooth round robin's running weight; guarded by Balancer.mu

	healthy atomic.Bool // the active checks' verdict; new targets start healthy

	failures     atomic.Int64 // passive: failed requests in a row
	ejectedUntil atomic.Int64 // passive: set aside until (Unix nanoseconds); 0 = not
}

func newBalancer(svc *config.Service, log *slog.Logger, now func() time.Time) *Balancer {
	b := &Balancer{
		serviceID:   svc.ID,
		workspaceID: svc.WorkspaceID,
		fingerprint: svc.Fingerprint,
		health:      svc.Health,
		targets:     make([]*member, len(svc.Targets)),
		log:         log,
		now:         now,
		stop:        func() {},
	}
	for i, t := range svc.Targets {
		m := &member{target: t}
		m.healthy.Store(true)
		b.targets[i] = m
	}
	return b
}

// Pick is the target chosen for one request. Report its outcome with Done.
type Pick struct {
	b *Balancer
	m *member
}

// TargetID is the chosen target's id, for the request log.
func (p Pick) TargetID() string { return p.m.target.ID }

// URL is the chosen target's base URL.
func (p Pick) URL() *url.URL { return p.m.target.URL }

// Pick chooses the next usable target by smooth weighted round robin. ok is
// false when no target is usable: every one is unhealthy or set aside.
//
// Each usable target's running weight grows by its weight; the one with the
// largest wins and gives back the total. With weights 5, 1, 1 that gives
// a a b a c a a, instead of a a a a a b c.
func (b *Balancer) Pick() (pick Pick, ok bool) {
	now := b.now().UnixNano()
	b.mu.Lock()
	defer b.mu.Unlock()

	var best *member
	total := 0
	for _, m := range b.targets {
		if !m.usable(now) {
			continue
		}
		m.current += m.target.Weight
		total += m.target.Weight
		if best == nil || m.current > best.current {
			best = m
		}
	}
	if best == nil {
		return Pick{}, false
	}
	best.current -= total
	return Pick{b: b, m: best}, true
}

func (m *member) usable(now int64) bool {
	return m.healthy.Load() && now >= m.ejectedUntil.Load()
}

// Done reports how the request to the target went (passive health check).
// failed means a connection error, a timeout or a 5xx answer. A request the
// client gave up on, or one the egress policy refused, isn't reported at
// all: it says nothing about the target.
func (p Pick) Done(failed bool) {
	b, m := p.b, p.m
	if !failed {
		m.failures.Store(0)
		return
	}
	limit := b.health.PassiveFailures
	if limit <= 0 { // passive checks off
		return
	}
	if m.failures.Add(1) < int64(limit) {
		return
	}
	m.failures.Store(0)
	until := b.now().Add(b.health.PassiveEject)
	m.ejectedUntil.Store(until.UnixNano())
	b.log.Warn("target set aside after failed requests",
		"workspace_id", b.workspaceID, "service_id", b.serviceID, "target_id", m.target.ID,
		"failures", limit, "for_s", b.health.PassiveEject.Seconds())
}

// setHealthy records the active checks' verdict for m and logs a change.
// Called only by m's own checker goroutine.
func (b *Balancer) setHealthy(m *member, healthy bool, reason string) {
	if m.healthy.Swap(healthy) == healthy {
		return
	}
	attrs := []any{
		"workspace_id", b.workspaceID, "service_id", b.serviceID, "target_id", m.target.ID,
		"target", m.target.URL.Host,
	}
	if healthy {
		b.log.Info("target healthy again", attrs...)
		return
	}
	b.log.Warn("target unhealthy", append(attrs, "reason", reason)...)
}
