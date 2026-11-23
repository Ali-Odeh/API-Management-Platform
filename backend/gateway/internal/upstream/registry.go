package upstream

import (
	"context"
	"log/slog"
	"net/http"
	"sync"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/egress"
)

// Source is where the registry follows the config. *config.Store fits it.
type Source interface {
	Current() *config.Snapshot
	Changed() <-chan struct{}
}

// Registry keeps one Balancer per service of the current config. A
// service's balancer (its round-robin position and its targets' health)
// stays across config versions while the service's fingerprint stays the
// same; a changed target list or health settings start a fresh one.
type Registry struct {
	ctx    context.Context // the active checkers' lifetime
	client *http.Client    // for active checks
	log    *slog.Logger
	now    func() time.Time

	balancers sync.Map // service id → *Balancer; read without locks on every request

	mu     sync.Mutex        // serializes changes to balancers
	want   map[string]string // service id → fingerprint, as of the last Sync
	synced bool              // Sync has run at least once
}

// NewRegistry builds a registry whose active checks go through policy's
// dialer and stop when ctx ends.
func NewRegistry(ctx context.Context, policy *egress.Policy, log *slog.Logger) *Registry {
	return &Registry{ctx: ctx, client: newCheckClient(policy), log: log, now: time.Now}
}

// Balancer returns the service's balancer. Requests call it, so the common
// case (the service's balancer exists and still fits) takes no lock.
func (r *Registry) Balancer(svc *config.Service) *Balancer {
	if b, ok := r.load(svc.ID); ok && b.fingerprint == svc.Fingerprint {
		return b
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.synced && r.want[svc.ID] != svc.Fingerprint {
		// A request still on an older config (or on a newer one Sync hasn't
		// seen yet, for a few milliseconds): serve it with a fresh balancer
		// of its own, without checkers. Only the current config's balancers
		// are kept, so nothing outlives its service.
		return newBalancer(svc, r.log, r.now)
	}
	return r.ensureLocked(svc)
}

// Sync makes the registry follow snap: a balancer (with its checkers) for
// every service of a served workspace, and none for anything else. A
// suspended workspace's services aren't checked: no traffic reaches them.
func (r *Registry) Sync(snap *config.Snapshot) {
	r.mu.Lock()
	defer r.mu.Unlock()
	want := make(map[string]string)
	if snap != nil {
		for _, ws := range snap.Workspaces {
			if ws.Status != config.WorkspaceActive {
				continue
			}
			for _, svc := range ws.Services {
				want[svc.ID] = svc.Fingerprint
				r.ensureLocked(svc)
			}
		}
	}
	r.balancers.Range(func(id, v any) bool {
		if b := v.(*Balancer); want[id.(string)] != b.fingerprint {
			r.balancers.Delete(id)
			b.stop()
		}
		return true
	})
	r.want, r.synced = want, true
}

// Run follows source until ctx ends, then stops every checker.
func (r *Registry) Run(ctx context.Context, source Source) {
	for {
		changed := source.Changed() // before Current: no change is missed
		r.Sync(source.Current())
		select {
		case <-ctx.Done():
			r.Close()
			return
		case <-changed:
		}
	}
}

// Close stops every active checker and forgets every balancer.
func (r *Registry) Close() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.balancers.Range(func(id, v any) bool {
		r.balancers.Delete(id)
		v.(*Balancer).stop()
		return true
	})
	r.want = map[string]string{}
}

// ensureLocked returns the balancer fitting svc, making it (and starting its
// checkers) if there is none or the service changed. r.mu must be held.
func (r *Registry) ensureLocked(svc *config.Service) *Balancer {
	if b, ok := r.load(svc.ID); ok && b.fingerprint == svc.Fingerprint {
		return b
	}
	b := newBalancer(svc, r.log, r.now)
	b.startCheckers(r.ctx, r.client)
	if old, loaded := r.balancers.Swap(svc.ID, b); loaded {
		old.(*Balancer).stop()
	}
	return b
}

// EachTarget calls fn with every target of the current config's services
// and whether it takes traffic right now (healthy and not set aside). For
// the metrics.
func (r *Registry) EachTarget(fn func(workspaceID, serviceID, targetID string, usable bool)) {
	now := r.now().UnixNano()
	r.balancers.Range(func(_, v any) bool {
		b := v.(*Balancer)
		for _, m := range b.targets {
			fn(b.workspaceID, b.serviceID, m.target.ID, m.usable(now))
		}
		return true
	})
}

func (r *Registry) load(id string) (*Balancer, bool) {
	v, ok := r.balancers.Load(id)
	if !ok {
		return nil, false
	}
	return v.(*Balancer), true
}
