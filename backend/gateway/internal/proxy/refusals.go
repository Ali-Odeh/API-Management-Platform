package proxy

import (
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/egress"
)

// At most one "upstream not allowed" warning per workspace and upstream host
// per interval. A refused connection never enters the pool, so every request
// to such a service is refused again: without a limit a tenant could write a
// warning per request into the platform's log.
const refusalWarnEvery = 10 * time.Second

// refusalLog writes the SSRF warning, throttled per (workspace, host), and
// reports how many it held back since the last one. Many request goroutines
// call it at once.
type refusalLog struct {
	log   *slog.Logger
	every time.Duration
	now   func() time.Time // replaced in tests
	seen  sync.Map         // "workspace|host" → *refusalCount
}

// One entry per (workspace, host) that was ever refused: bounded by the
// services tenants configure, a few dozen bytes each.
type refusalCount struct {
	last    atomic.Int64 // time of the last warning, Unix nanoseconds (0: none yet)
	skipped atomic.Int64 // warnings held back since then
}

func newRefusalLog(log *slog.Logger) *refusalLog {
	return &refusalLog{log: log, every: refusalWarnEvery, now: time.Now}
}

func (r *refusalLog) warn(workspaceID, routeID string, blocked *egress.BlockedError) {
	key := workspaceID + "|" + blocked.Host
	entry, ok := r.seen.Load(key)
	if !ok {
		entry, _ = r.seen.LoadOrStore(key, new(refusalCount))
	}
	count := entry.(*refusalCount)

	now := r.now().UnixNano()
	last := count.last.Load()
	// Too soon, or another goroutine is logging this round: just count it.
	if (last != 0 && now-last < int64(r.every)) || !count.last.CompareAndSwap(last, now) {
		count.skipped.Add(1)
		return
	}
	// A tenant's service points inside the platform: worth an operator's
	// look, but not the backend's fault.
	r.log.Warn("upstream not allowed (internal address)",
		"workspace_id", workspaceID, "route_id", routeID,
		"upstream", blocked.Host, "address", blocked.Addr.String(),
		"skipped_warnings", count.skipped.Swap(0))
}
