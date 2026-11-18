package config

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strconv"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/settings"
)

const (
	// Extra time on top of CONFIG_WAIT before a held request counts as lost.
	// The control plane answers by CONFIG_WAIT at the latest.
	clientTimeoutMargin = 10 * time.Second
	// Upper bound for a snapshot body, so a broken answer can't eat memory.
	maxSnapshotBytes = 64 << 20 // 64 MiB
	// Never ask more often than this, even if the control plane answers
	// instantly every time (a bug or a proxy that ignores ?wait=). Without it
	// such a bug would turn the loop into a busy loop.
	defaultMinInterval = 250 * time.Millisecond
)

// Poller keeps a Store up to date by long-polling the control plane.
type Poller struct {
	client      *http.Client
	url         string // .../internal/config?wait=30
	token       settings.Secret
	store       *Store
	log         *slog.Logger
	backoff     backoff
	minInterval time.Duration
}

// NewPoller builds a poller that writes into store. Call Run to start it.
func NewPoller(cfg settings.Settings, store *Store, log *slog.Logger) *Poller {
	query := url.Values{"wait": {strconv.Itoa(int(cfg.ConfigWait / time.Second))}}
	return &Poller{
		client:      &http.Client{Timeout: cfg.ConfigWait + clientTimeoutMargin},
		url:         cfg.ControlPlaneURL + "/internal/config?" + query.Encode(),
		token:       cfg.InternalToken,
		store:       store,
		log:         log,
		backoff:     backoff{min: time.Second, max: 30 * time.Second},
		minInterval: defaultMinInterval,
	}
}

// Run polls until ctx is cancelled. It never gives up: on any error it keeps
// the current snapshot, waits (with backoff) and tries again, so the gateway
// keeps serving while the control plane is down.
func (p *Poller) Run(ctx context.Context) {
	p.log.Info("config poller started", "url", p.url)
	defer p.log.Info("config poller stopped")

	for {
		started := time.Now()
		err := p.poll(ctx)
		if ctx.Err() != nil {
			return // shutting down; the error is just the cancelled request
		}

		wait := p.minInterval - time.Since(started)
		if err != nil {
			wait = p.backoff.next()
			p.log.Warn("config poll failed, keeping the current config",
				"error", err, "retry_in", wait.Round(time.Millisecond).String(), "version", p.currentVersion())
		} else {
			p.backoff.reset()
		}

		if !sleep(ctx, wait) {
			return
		}
	}
}

// poll makes one request and applies its answer. nil means the gateway is up
// to date (a new snapshot was stored, or 304 said nothing changed).
func (p *Poller) poll(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, p.url, nil)
	if err != nil {
		return fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("X-Internal-Token", p.token.Reveal())
	if current := p.store.Current(); current != nil {
		req.Header.Set("If-None-Match", strconv.Quote(strconv.FormatInt(current.Version, 10)))
	}

	resp, err := p.client.Do(req)
	if err != nil {
		return fmt.Errorf("request: %w", err)
	}
	defer resp.Body.Close()

	switch resp.StatusCode {
	case http.StatusOK:
		return p.apply(resp.Body)
	case http.StatusNotModified:
		return nil
	case http.StatusUnauthorized:
		return errors.New("control plane answered 401: INTERNAL_TOKEN does not match the control plane's")
	default:
		// Read a little of the body so the connection can be reused.
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4<<10))
		return fmt.Errorf("control plane answered %d", resp.StatusCode)
	}
}

// apply parses a 200 body and makes it the current snapshot.
func (p *Poller) apply(body io.Reader) error {
	snap, skipped, err := parseSnapshot(io.LimitReader(body, maxSnapshotBytes))
	if err != nil {
		return fmt.Errorf("read snapshot: %w", err)
	}
	for _, reason := range skipped {
		p.log.Warn("config entry skipped", "reason", reason.Error())
	}

	previous := p.currentVersion()
	p.store.swap(snap)
	p.log.Info("config updated",
		"version", snap.Version, "previous_version", previous,
		"routes", len(snap.Routes), "keys", len(snap.Keys), "skipped", len(skipped))
	return nil
}

// currentVersion is for logs: the version in use, or 0 before the first one.
func (p *Poller) currentVersion() int64 {
	if current := p.store.Current(); current != nil {
		return current.Version
	}
	return 0
}

// sleep waits for d, or returns false at once if ctx is cancelled first.
func sleep(ctx context.Context, d time.Duration) bool {
	if d <= 0 {
		return ctx.Err() == nil
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}
