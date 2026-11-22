package upstream

import (
	"fmt"
	"log/slog"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
)

// service builds a service with targets named a, b, c ... of the given
// weights, at http://<name>:9000.
func service(id string, health config.Health, weights ...int) *config.Service {
	svc := &config.Service{ID: id, WorkspaceID: "ws1", Timeout: time.Second, Health: health}
	for i, w := range weights {
		name := string(rune('a' + i))
		u, _ := url.Parse("http://" + name + ":9000")
		svc.Targets = append(svc.Targets, config.Target{ID: name, URL: u, Weight: w})
	}
	svc.Fingerprint = fmt.Sprint(id, health, weights)
	return svc
}

// fakeClock is a settable time for passive ejection.
type fakeClock struct{ t atomic.Int64 }

func (c *fakeClock) now() time.Time          { return time.Unix(0, c.t.Load()) }
func (c *fakeClock) advance(d time.Duration) { c.t.Add(int64(d)) }

func newTestBalancer(svc *config.Service) (*Balancer, *fakeClock) {
	clock := &fakeClock{}
	clock.t.Store(time.Date(2026, 11, 22, 10, 0, 0, 0, time.UTC).UnixNano())
	return newBalancer(svc, slog.New(slog.DiscardHandler), clock.now), clock
}

func picks(t *testing.T, b *Balancer, n int) string {
	t.Helper()
	var sb strings.Builder
	for range n {
		p, ok := b.Pick()
		if !ok {
			sb.WriteByte('-')
			continue
		}
		sb.WriteString(p.TargetID())
	}
	return sb.String()
}

// The smooth order nginx documents for weights 5, 1, 1: the heavy target
// never gets five in a row.
func TestSmoothWeightedRoundRobinOrder(t *testing.T) {
	b, _ := newTestBalancer(service("s", config.Health{}, 5, 1, 1))
	if got := picks(t, b, 14); got != "aabacaaaabacaa" {
		t.Errorf("order = %s, want aabacaa twice", got)
	}
}

func TestWeightsGiveExactSharesOverManyPicks(t *testing.T) {
	tests := []struct {
		weights []int
		picks   int
		want    map[string]int
	}{
		{[]int{100, 100, 50}, 2500, map[string]int{"a": 1000, "b": 1000, "c": 500}},
		{[]int{3, 1}, 4000, map[string]int{"a": 3000, "b": 1000}},
		{[]int{1}, 10, map[string]int{"a": 10}},
		{[]int{1000, 1}, 1001, map[string]int{"a": 1000, "b": 1}},
	}
	for _, tc := range tests {
		b, _ := newTestBalancer(service("s", config.Health{}, tc.weights...))
		got := map[string]int{}
		for _, id := range picks(t, b, tc.picks) {
			got[string(id)]++
		}
		if fmt.Sprint(got) != fmt.Sprint(tc.want) {
			t.Errorf("weights %v over %d picks = %v, want %v", tc.weights, tc.picks, got, tc.want)
		}
	}
}

func TestUnhealthyTargetsAreSkipped(t *testing.T) {
	b, _ := newTestBalancer(service("s", config.Health{}, 1, 1, 1))
	b.setHealthy(b.targets[1], false, "test")

	if got := picks(t, b, 6); strings.Contains(got, "b") || len(got) != 6 {
		t.Errorf("picks = %s, want only a and c", got)
	}

	b.setHealthy(b.targets[0], false, "test")
	b.setHealthy(b.targets[2], false, "test")
	if _, ok := b.Pick(); ok {
		t.Error("picked a target while every one is unhealthy")
	}

	b.setHealthy(b.targets[1], true, "")
	if got := picks(t, b, 3); got != "bbb" {
		t.Errorf("after b recovered: %s, want bbb", got)
	}
}

func TestPassiveFailuresSetATargetAsideForAWhile(t *testing.T) {
	health := config.Health{PassiveFailures: 3, PassiveEject: 30 * time.Second}
	b, clock := newTestBalancer(service("s", health, 1, 1))
	a := Pick{b: b, m: b.targets[0]}

	a.Done(true)
	a.Done(true)
	a.Done(false) // a success in between starts the count again
	a.Done(true)
	a.Done(true)
	if got := picks(t, b, 4); got != "abab" {
		t.Fatalf("after 2 failures in a row: %s, want a still used", got)
	}

	a.Done(true) // the third in a row
	if got := picks(t, b, 4); got != "bbbb" {
		t.Errorf("after 3 failures in a row: %s, want a set aside", got)
	}
	clock.advance(29 * time.Second)
	if got := picks(t, b, 2); got != "bb" {
		t.Errorf("29 s later: %s, want a still aside", got)
	}
	clock.advance(time.Second)
	if got := picks(t, b, 4); !strings.Contains(got, "a") {
		t.Errorf("30 s later: %s, want a tried again", got)
	}
}

func TestPassiveChecksOffWithoutSettings(t *testing.T) {
	b, _ := newTestBalancer(service("s", config.Health{}, 1))
	a := Pick{b: b, m: b.targets[0]}
	for range 100 {
		a.Done(true)
	}
	if _, ok := b.Pick(); !ok {
		t.Error("a target was set aside although passive checks are off")
	}
}

func TestAllTargetsSetAsideMeansNoPick(t *testing.T) {
	b, _ := newTestBalancer(service("s", config.Health{PassiveFailures: 1, PassiveEject: time.Minute}, 1, 1))
	for _, m := range b.targets {
		Pick{b: b, m: m}.Done(true)
	}
	if p, ok := b.Pick(); ok {
		t.Errorf("picked %s while every target is set aside", p.TargetID())
	}
}

// Under -race: many requests pick and report while health changes. Every
// pick is one of the targets and the shares stay right when nothing fails.
func TestConcurrentPicks(t *testing.T) {
	b, _ := newTestBalancer(service("s", config.Health{PassiveFailures: 1000, PassiveEject: time.Second}, 3, 1))
	counts := map[string]*atomic.Int64{"a": {}, "b": {}}
	var wg sync.WaitGroup
	for range 8 {
		wg.Go(func() {
			for range 1000 {
				p, ok := b.Pick()
				if !ok {
					t.Error("no pick")
					return
				}
				counts[p.TargetID()].Add(1)
				p.Done(false)
			}
		})
	}
	wg.Go(func() {
		for range 100 {
			b.setHealthy(b.targets[0], true, "")
		}
	})
	wg.Wait()
	if a, bb := counts["a"].Load(), counts["b"].Load(); a != 6000 || bb != 2000 {
		t.Errorf("a %d, b %d; want 6000 and 2000", a, bb)
	}
}

func TestPickExposesTheTarget(t *testing.T) {
	b, _ := newTestBalancer(service("s", config.Health{}, 1))
	p, _ := b.Pick()
	if p.TargetID() != "a" || p.URL().String() != "http://a:9000" {
		t.Errorf("pick = %s %s", p.TargetID(), p.URL())
	}
}

// One pick, the cost every request pays (lock, a loop over the targets).
//
//	go test -run=^$ -bench=Pick -benchmem ./internal/upstream
func BenchmarkPick(b *testing.B) {
	for _, n := range []int{1, 3, 10} {
		weights := make([]int, n)
		for i := range weights {
			weights[i] = 100
		}
		bal, _ := newTestBalancer(service("s", config.Health{}, weights...))
		b.Run(fmt.Sprint(n, "targets"), func(b *testing.B) {
			b.ReportAllocs()
			for b.Loop() {
				p, _ := bal.Pick()
				p.Done(false)
			}
		})
	}
}
