// Package inflight caps how many requests each workspace may have in flight
// on this gateway instance at once (decision 6), so a slow backend in one
// workspace can't tie up the gateway for the others.
//
// The count lives in memory, per instance on purpose: it protects each
// instance's own connections and goroutines. The limit comes with every
// request (the workspace's effective max_concurrent from the config
// snapshot), so a changed limit applies to the next request without
// resetting the count of the requests already running.
package inflight

import (
	"sync"
	"sync/atomic"
)

// Limiter holds one counter per workspace. The zero value is ready to use
// and safe for many goroutines at once.
type Limiter struct {
	// workspace id → *atomic.Int64. A counter outlives config changes (a
	// workspace's config is replaced on every change, its running requests
	// are not). Counters are never removed: one small entry per workspace
	// this instance has served, and workspace ids are never reused.
	counts sync.Map
}

// Slot is one taken place. Release gives it back; it must be called exactly
// once.
type Slot struct {
	count *atomic.Int64
}

// Release gives the slot back.
func (s Slot) Release() {
	s.count.Add(-1)
}

// Acquire takes a slot of the workspace if fewer than limit of its requests
// are in flight. ok is false when the workspace is full (or limit is below
// 1); nothing is taken then.
func (l *Limiter) Acquire(workspaceID string, limit int) (slot Slot, ok bool) {
	count := l.counter(workspaceID)
	// A compare-and-swap loop instead of "add, then undo if over": an add
	// that is undone a moment later could make a parallel request see the
	// workspace full while a slot is actually free.
	for {
		n := count.Load()
		if n >= int64(limit) {
			return Slot{}, false
		}
		if count.CompareAndSwap(n, n+1) {
			return Slot{count: count}, true
		}
	}
}

// InFlight is the number of the workspace's requests in flight right now.
func (l *Limiter) InFlight(workspaceID string) int {
	if c, ok := l.counts.Load(workspaceID); ok {
		return int(c.(*atomic.Int64).Load())
	}
	return 0
}

// counter returns the workspace's counter, making it on first use. Load
// first: after the first request, finding it takes no lock and allocates
// nothing.
func (l *Limiter) counter(workspaceID string) *atomic.Int64 {
	if c, ok := l.counts.Load(workspaceID); ok {
		return c.(*atomic.Int64)
	}
	c, _ := l.counts.LoadOrStore(workspaceID, new(atomic.Int64))
	return c.(*atomic.Int64)
}
