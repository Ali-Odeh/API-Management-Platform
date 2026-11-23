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
	generationHeader    = "X-Config-Generation"
	// Upper bound for an answer's body: big enough for a full config of many
	// workspaces, small enough that a broken answer can't exhaust memory.
	defaultMaxAnswerBytes = 1 << 30 // 1 GiB
	// Never ask more often than this, even if the control plane answers
	// instantly every time (a bug or a proxy that ignores ?wait=). Without it
	// such a bug would turn the loop into a busy loop.
	defaultMinInterval = 250 * time.Millisecond
)

// Poller keeps a Store up to date by long-polling the control plane.
//
// It asks "what changed after my sequence?" (?since=<seq>) and merges each
// answer into a new snapshot. At start, and after any answer that doesn't
// fit what it holds (resync), it asks for everything (since=0).
type Poller struct {
	client      *http.Client
	url         string // .../internal/config
	wait        string // seconds the control plane may hold a request
	token       settings.Secret
	store       *Store
	log         *slog.Logger
	backoff     backoff
	minInterval time.Duration
	maxBytes    int64
	resync      bool // the next request asks for everything (since=0)
	generation  string
}

// NewPoller builds a poller that writes into store. Call Run to start it.
func NewPoller(cfg settings.Settings, store *Store, log *slog.Logger) *Poller {
	return &Poller{
		client:      &http.Client{Timeout: cfg.ConfigWait + clientTimeoutMargin},
		url:         cfg.ControlPlaneURL + "/internal/config",
		wait:        strconv.Itoa(int(cfg.ConfigWait / time.Second)),
		token:       cfg.InternalToken,
		store:       store,
		log:         log,
		backoff:     backoff{min: time.Second, max: 30 * time.Second},
		minInterval: defaultMinInterval,
		maxBytes:    defaultMaxAnswerBytes,
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
				"error", err, "retry_in", wait.Round(time.Millisecond).String(), "seq", p.currentSeq())
		} else {
			p.backoff.reset()
			p.store.markSynced(time.Now())
		}

		if !sleep(ctx, wait) {
			return
		}
	}
}

// poll makes one request and applies its answer. nil means the gateway is up
// to date (a new snapshot was stored, or 304 said nothing changed).
func (p *Poller) poll(ctx context.Context) error {
	query := url.Values{"since": {strconv.FormatInt(p.since(), 10)}, "wait": {p.wait}}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, p.url+"?"+query.Encode(), nil)
	if err != nil {
		return fmt.Errorf("build request: %w", err)
	}
	req.Header.Set("X-Internal-Token", p.token.Reveal())
	if p.generation != "" {
		req.Header.Set(generationHeader, p.generation)
	}

	resp, err := p.client.Do(req)
	if err != nil {
		return fmt.Errorf("request: %w", err)
	}
	defer resp.Body.Close()

	switch resp.StatusCode {
	case http.StatusOK:
		return p.apply(resp.Body, resp.Header.Get(generationHeader))
	case http.StatusNotModified:
		if p.since() == 0 {
			return fmt.Errorf("%w: 304 when a full answer was requested", errProtocol)
		}
		return nil
	case http.StatusUnauthorized:
		return errors.New("control plane answered 401: INTERNAL_TOKEN does not match the control plane's")
	default:
		// Read a little of the body so the connection can be reused.
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4<<10))
		return fmt.Errorf("control plane answered %d", resp.StatusCode)
	}
}

// since is the sequence to ask from: the current one, or 0 for everything
// (at start, or to recover from an answer that didn't fit).
func (p *Poller) since() int64 {
	if p.resync {
		return 0
	}
	return p.currentSeq()
}

// apply parses a 200 body, merges it into the current snapshot and makes
// the result current.
func (p *Poller) apply(body io.Reader, generation string) error {
	a, skipped, err := parseAnswer(&cappedReader{r: body, left: p.maxBytes})
	if err != nil {
		p.resync = true
		return fmt.Errorf("read config: %w", err)
	}
	if !a.Full && (p.resync || generation != p.generation) {
		p.resync = true
		return fmt.Errorf("%w: expected a full answer", errProtocol)
	}
	for _, reason := range skipped {
		p.log.Warn("config entry skipped", "reason", reason.Error())
	}

	current := p.store.Current()
	next, err := merge(current, a)
	if err != nil {
		p.resync = true // ask for everything next time
		return err
	}
	// A workspace dropped from a partial answer may have been a one-off: a
	// full answer brings it back if it reads fine now. (From a full answer
	// it comes back with its next change; asking again would only loop.)
	p.resync = len(a.Dropped) > 0 && !a.Full
	p.generation = generation
	p.store.swap(next)
	p.log.Info("config updated",
		"seq", next.Seq, "previous_seq", seqOf(current), "full", a.Full,
		"changed", len(a.Workspaces), "deleted", len(a.Deleted),
		"workspaces", len(next.Workspaces), "skipped", len(skipped))
	return nil
}

// currentSeq is the sequence in use, or 0 before the first config.
func (p *Poller) currentSeq() int64 {
	return seqOf(p.store.Current())
}

func seqOf(snap *Snapshot) int64 {
	if snap == nil {
		return 0
	}
	return snap.Seq
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

// errAnswerTooLarge: the body went past the poller's maxBytes.
var errAnswerTooLarge = errors.New("config answer too large")

// cappedReader reads at most left bytes, then fails with errAnswerTooLarge
// if anything follows. A body of exactly the limit still reads to its EOF.
type cappedReader struct {
	r    io.Reader
	left int64
}

func (c *cappedReader) Read(p []byte) (int, error) {
	if c.left <= 0 {
		var probe [1]byte
		n, err := c.r.Read(probe[:])
		if n > 0 {
			return 0, errAnswerTooLarge
		}
		return 0, err
	}
	if int64(len(p)) > c.left {
		p = p[:c.left]
	}
	n, err := c.r.Read(p)
	c.left -= int64(n)
	return n, err
}
