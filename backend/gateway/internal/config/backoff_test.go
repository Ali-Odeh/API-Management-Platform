package config

import (
	"context"
	"testing"
	"time"
)

func TestBackoffGrowsCapsAndResets(t *testing.T) {
	b := backoff{min: time.Second, max: 30 * time.Second}

	// Each wait is between half and all of 1, 2, 4, 8, 16, then 30 (capped).
	for _, full := range []time.Duration{1, 2, 4, 8, 16, 30, 30, 30} {
		full *= time.Second
		got := b.next()
		if got < full/2 || got > full {
			t.Fatalf("next() = %s, want between %s and %s", got, full/2, full)
		}
	}

	b.reset()
	if got := b.next(); got < 500*time.Millisecond || got > time.Second {
		t.Errorf("after reset, next() = %s, want between 500ms and 1s", got)
	}
}

// Many failures in a row (a long outage) must never overflow into a
// negative or zero wait.
func TestBackoffNeverOverflows(t *testing.T) {
	b := backoff{min: time.Second, max: 30 * time.Second}
	for range 200 {
		if got := b.next(); got <= 0 || got > 30*time.Second {
			t.Fatalf("next() = %s after %d attempts, want 0 < wait <= 30s", got, b.attempt)
		}
	}
	b = backoff{min: time.Second, max: 30 * time.Second, attempt: 100}
	if got := b.next(); got < 15*time.Second || got > 30*time.Second {
		t.Errorf("next() with a huge attempt count = %s, want 15s to 30s", got)
	}
}

func TestSleep(t *testing.T) {
	if !sleep(t.Context(), 0) {
		t.Error("sleep(0) = false, want true while running")
	}
	if !sleep(t.Context(), time.Millisecond) {
		t.Error("sleep(1ms) = false, want true while running")
	}

	cancelled, cancel := context.WithCancel(t.Context())
	cancel()
	if sleep(cancelled, 0) {
		t.Error("sleep(0) = true after cancel, want false")
	}
	start := time.Now()
	if sleep(cancelled, time.Hour) {
		t.Error("sleep(1h) = true after cancel, want false")
	}
	if time.Since(start) > time.Second {
		t.Error("sleep did not return at once after cancel")
	}
}
