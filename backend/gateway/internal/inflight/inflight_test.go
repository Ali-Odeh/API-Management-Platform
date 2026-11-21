package inflight

import (
	"sync"
	"sync/atomic"
	"testing"
)

func TestAcquireUpToTheLimitThenRefuse(t *testing.T) {
	var l Limiter
	var slots []Slot
	for i := range 3 {
		slot, ok := l.Acquire("ws1", 3)
		if !ok {
			t.Fatalf("request %d refused below the limit", i+1)
		}
		slots = append(slots, slot)
	}
	if _, ok := l.Acquire("ws1", 3); ok {
		t.Fatal("a 4th request was let in with a limit of 3")
	}
	if got := l.InFlight("ws1"); got != 3 {
		t.Errorf("InFlight = %d, want 3 (a refused request takes nothing)", got)
	}

	slots[0].Release()
	if _, ok := l.Acquire("ws1", 3); !ok {
		t.Error("refused after a slot was released")
	}
}

func TestWorkspacesHaveSeparateCounts(t *testing.T) {
	var l Limiter
	if _, ok := l.Acquire("busy", 1); !ok {
		t.Fatal("first request refused")
	}
	if _, ok := l.Acquire("busy", 1); ok {
		t.Fatal("busy should be full")
	}
	if _, ok := l.Acquire("calm", 1); !ok {
		t.Error("another workspace was refused because busy is full")
	}
	if l.InFlight("never-seen") != 0 {
		t.Error("an unknown workspace has requests in flight")
	}
}

// The limit comes with each request: lowering it refuses new requests
// until enough running ones finish; raising it lets them in at once.
func TestAChangedLimitAppliesToTheNextRequest(t *testing.T) {
	var l Limiter
	a, _ := l.Acquire("ws1", 3)
	b, _ := l.Acquire("ws1", 3)

	if _, ok := l.Acquire("ws1", 2); ok {
		t.Fatal("let in over a lowered limit")
	}
	a.Release()
	if _, ok := l.Acquire("ws1", 2); !ok {
		t.Fatal("refused under the lowered limit")
	}
	if _, ok := l.Acquire("ws1", 5); !ok {
		t.Error("refused under a raised limit")
	}
	b.Release()
}

func TestALimitBelowOneRefusesEverything(t *testing.T) {
	var l Limiter
	for _, limit := range []int{0, -1} {
		if _, ok := l.Acquire("ws1", limit); ok {
			t.Errorf("limit %d let a request in", limit)
		}
	}
}

// Many requests at the same moment never take more slots than the limit,
// and every slot comes back. Run with -race in CI.
func TestConcurrentAcquireNeverOvershoots(t *testing.T) {
	var l Limiter
	const limit, workers, rounds = 10, 64, 500

	var current, peak, refused atomic.Int64
	var wg sync.WaitGroup
	start := make(chan struct{})
	for range workers {
		wg.Go(func() {
			<-start
			for range rounds {
				slot, ok := l.Acquire("ws1", limit)
				if !ok {
					refused.Add(1)
					continue
				}
				raise(&peak, current.Add(1))
				current.Add(-1)
				slot.Release()
			}
		})
	}
	close(start)
	wg.Wait()

	if peak.Load() > limit {
		t.Errorf("%d requests in flight at once, limit %d", peak.Load(), limit)
	}
	if got := l.InFlight("ws1"); got != 0 {
		t.Errorf("InFlight after everything finished = %d, want 0", got)
	}
	if refused.Load() == 0 {
		t.Log("no request was refused; the run may not have been concurrent enough to prove much")
	}
}

// raise sets peak to n if n is larger, safely under concurrent callers.
func raise(peak *atomic.Int64, n int64) {
	for {
		m := peak.Load()
		if n <= m || peak.CompareAndSwap(m, n) {
			return
		}
	}
}

// Taking and giving back a slot, the cost every request pays. Run by hand:
//
//	go test -run=^$ -bench=AcquireRelease ./internal/inflight
func BenchmarkAcquireRelease(b *testing.B) {
	var l Limiter
	b.ReportAllocs()
	for b.Loop() {
		slot, ok := l.Acquire("ws1", 200)
		if !ok {
			b.Fatal("refused")
		}
		slot.Release()
	}
}

func BenchmarkAcquireReleaseParallel(b *testing.B) {
	var l Limiter
	b.ReportAllocs()
	b.RunParallel(func(pb *testing.PB) {
		for pb.Next() {
			if slot, ok := l.Acquire("ws1", 1_000_000); ok {
				slot.Release()
			}
		}
	})
}
