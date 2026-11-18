package config

import "sync/atomic"

// Store holds the current Snapshot. One goroutine (the Poller) replaces it;
// any number of request handlers read it at the same time, without locks.
//
// The zero value is ready to use and holds no snapshot (Current returns nil)
// until the first config arrives.
type Store struct {
	current atomic.Pointer[Snapshot]
}

// Current returns the snapshot in use, or nil before the first one arrives.
// Callers keep using the snapshot they got for the whole request, so one
// request never sees half of one version and half of another.
func (s *Store) Current() *Snapshot {
	return s.current.Load()
}

// swap makes snap the current snapshot in one step. Unexported: only the
// Poller in this package may change the config.
func (s *Store) swap(snap *Snapshot) {
	s.current.Store(snap)
}
