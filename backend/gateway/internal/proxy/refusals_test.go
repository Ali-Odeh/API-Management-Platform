package proxy

import (
	"encoding/json"
	"log/slog"
	"net/netip"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/egress"
)

// lockedBuilder is a strings.Builder many goroutines may write to.
type lockedBuilder struct {
	mu sync.Mutex
	b  strings.Builder
}

func (l *lockedBuilder) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.b.Write(p)
}

func (l *lockedBuilder) lines(t *testing.T) []map[string]any {
	t.Helper()
	l.mu.Lock()
	defer l.mu.Unlock()
	var out []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(l.b.String()), "\n") {
		if line == "" {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Fatalf("not JSON: %q", line)
		}
		out = append(out, m)
	}
	return out
}

func blockedHost(host string) *egress.BlockedError {
	return &egress.BlockedError{Host: host, Addr: netip.MustParseAddr("172.18.0.2")}
}

// A tenant hammering an internal upstream gets one warning per interval per
// (workspace, host), with the count of the held-back ones; other hosts and
// workspaces have their own interval.
func TestRefusalWarningsAreThrottledPerWorkspaceAndHost(t *testing.T) {
	var logs lockedBuilder
	clock := time.Date(2026, 11, 22, 10, 0, 0, 0, time.UTC)
	r := newRefusalLog(slog.New(slog.NewJSONHandler(&logs, nil)))
	r.now = func() time.Time { return clock }

	for range 100 {
		r.warn("ws1", "r1", blockedHost("redis")) // 1 logged, 99 held back
	}
	r.warn("ws1", "r2", blockedHost("postgres")) // another host: logged
	r.warn("ws2", "r9", blockedHost("redis"))    // another workspace: logged
	clock = clock.Add(refusalWarnEvery)
	r.warn("ws1", "r1", blockedHost("redis")) // next interval: logged, reporting 99

	lines := logs.lines(t)
	if len(lines) != 4 {
		t.Fatalf("%d warnings, want 4: %v", len(lines), lines)
	}
	if lines[0]["upstream"] != "redis" || lines[0]["address"] != "172.18.0.2" || lines[0]["skipped_warnings"] != 0.0 {
		t.Errorf("first warning = %v", lines[0])
	}
	if lines[1]["upstream"] != "postgres" || lines[2]["workspace_id"] != "ws2" {
		t.Errorf("other host / workspace = %v, %v", lines[1], lines[2])
	}
	if lines[3]["skipped_warnings"] != 99.0 {
		t.Errorf("next interval reports %v held back, want 99", lines[3]["skipped_warnings"])
	}
}

// Under -race: many goroutines refused at once write a single warning.
func TestRefusalWarningsConcurrent(t *testing.T) {
	var logs lockedBuilder
	r := newRefusalLog(slog.New(slog.NewJSONHandler(&logs, nil)))

	var wg sync.WaitGroup
	for range 50 {
		wg.Go(func() { r.warn("ws1", "r1", blockedHost("redis")) })
	}
	wg.Wait()

	if n := len(logs.lines(t)); n != 1 {
		t.Errorf("%d warnings from 50 goroutines, want 1", n)
	}
}
