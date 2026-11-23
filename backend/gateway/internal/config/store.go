package config

import (
	"sync"
	"sync/atomic"
	"time"
)

// Store holds the current Snapshot. One goroutine (the Poller) replaces it;
// any number of request handlers read it at the same time, without locks.
//
// The zero value is ready to use and holds no snapshot (Current returns nil)
// until the first config arrives.
type Store struct {
	current atomic.Pointer[Snapshot]

	mu      sync.Mutex
	changed chan struct{} // closed at the next swap; nil until someone waits

	lastSync atomic.Int64 // Unix nanoseconds of the last successful poll; 0: none yet
}

// LastSync is when the control plane last answered a poll (a new config or
// "nothing changed"); zero before the first answer. For the metrics.
func (s *Store) LastSync() time.Time {
	if n := s.lastSync.Load(); n != 0 {
		return time.Unix(0, n)
	}
	return time.Time{}
}

// Seq is the sequence of the config in use; 0 before the first one.
func (s *Store) Seq() int64 {
	if snap := s.Current(); snap != nil {
		return snap.Seq
	}
	return 0
}

func (s *Store) markSynced(at time.Time) { s.lastSync.Store(at.UnixNano()) }

// Current returns the snapshot in use, or nil before the first one arrives.
// Callers keep using the snapshot they got for the whole request, so one
// request never sees half of one version and half of another.
func (s *Store) Current() *Snapshot {
	return s.current.Load()
}

// Changed returns a channel that is closed when the snapshot is next
// replaced. Ask for it before reading Current, so no change is missed:
//
//	changed := store.Changed()
//	use(store.Current())
//	<-changed // a newer snapshot is in
func (s *Store) Changed() <-chan struct{} {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.changed == nil {
		s.changed = make(chan struct{})
	}
	return s.changed
}

// swap makes snap the current snapshot in one step and wakes everyone
// waiting on Changed. Unexported: only the Poller in this package may change
// the config.
func (s *Store) swap(snap *Snapshot) {
	s.current.Store(snap)
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.changed != nil {
		close(s.changed)
		s.changed = nil
	}
}
