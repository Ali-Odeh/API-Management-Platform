package config

import (
	"sync"
	"testing"
	"time"
)

func TestStoreStartsEmpty(t *testing.T) {
	var s Store // the zero value is ready to use
	if got := s.Current(); got != nil {
		t.Fatalf("Current() = %+v, want nil before the first config", got)
	}
}

func TestStoreSwap(t *testing.T) {
	var s Store
	first, second := &Snapshot{Seq: 1}, &Snapshot{Seq: 2}

	s.swap(first)
	if s.Current() != first {
		t.Fatal("Current() is not the first snapshot")
	}
	s.swap(second)
	if s.Current() != second {
		t.Fatal("Current() is not the second snapshot")
	}
}

// Many readers while the config keeps changing. `go test -race` (in CI)
// fails this test if Store were not safe for concurrent use.
func TestStoreConcurrentReadsDuringSwaps(t *testing.T) {
	var s Store
	s.swap(&Snapshot{Seq: 0})

	var wg sync.WaitGroup
	for range 8 {
		wg.Go(func() {
			last := int64(-1)
			for range 1000 {
				v := s.Current().Seq
				if v < last {
					t.Errorf("sequence went back from %d to %d", last, v)
					return
				}
				last = v
			}
		})
	}
	for v := int64(1); v <= 1000; v++ {
		s.swap(&Snapshot{Seq: v})
	}
	wg.Wait()
}

func TestStoreChangedIsClosedByTheNextSwap(t *testing.T) {
	var s Store
	changed := s.Changed()
	if s.Changed() != changed {
		t.Error("two waiters before a swap got different channels")
	}
	select {
	case <-changed:
		t.Fatal("closed before any swap")
	default:
	}

	s.swap(&Snapshot{Seq: 1})

	select {
	case <-changed:
	default:
		t.Fatal("not closed by the swap")
	}
	next := s.Changed()
	select {
	case <-next:
		t.Fatal("a channel asked for after the swap is already closed")
	default:
	}
	s.swap(&Snapshot{Seq: 2}) // also fine with nobody waiting afterwards
	s.swap(&Snapshot{Seq: 3})
}

func TestStoreSeqAndLastSync(t *testing.T) {
	var s Store
	if s.Seq() != 0 || !s.LastSync().IsZero() {
		t.Fatalf("empty store: seq %d, last sync %v", s.Seq(), s.LastSync())
	}
	s.swap(&Snapshot{Seq: 7})
	at := time.Unix(1_700_000_000, 0)
	s.markSynced(at)
	if s.Seq() != 7 || !s.LastSync().Equal(at) {
		t.Errorf("seq %d, last sync %v", s.Seq(), s.LastSync())
	}
}
