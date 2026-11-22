package usage

import (
	"context"
	"log/slog"
	"sync/atomic"
	"time"
)

// Defaults of a Recorder. A second's events at 1,000 requests/s fit the
// buffer ten times over; a batch of 500 is one pipelined round trip.
const (
	DefaultBufferSize   = 10_000
	DefaultBatchSize    = 500
	DefaultFlushEvery   = time.Second
	DefaultWriteTimeout = 2 * time.Second
	// At most one "events dropped" warning per this interval.
	dropWarnEvery = 10 * time.Second
)

// Appender writes a batch of events. *Stream fits it; tests pass a fake.
type Appender interface {
	Append(ctx context.Context, events []Event) error
}

// Recorder buffers events and writes them in batches from one goroutine
// (Run). Record is safe for many goroutines and never blocks.
type Recorder struct {
	out          Appender
	log          *slog.Logger
	events       chan Event
	batchSize    int
	flushEvery   time.Duration
	writeTimeout time.Duration

	dropped atomic.Int64 // events lost: buffer full or the write failed

	written atomic.Int64 // events written to the stream

	// Owned by Run's goroutine: the drop warning's throttle.
	now           func() time.Time
	lastWarn      time.Time
	droppedAtWarn int64 // Dropped() when the last warning was written
}

// Options tune a Recorder; zero fields take the defaults above.
type Options struct {
	BufferSize   int
	BatchSize    int
	FlushEvery   time.Duration
	WriteTimeout time.Duration
}

// NewRecorder returns a Recorder that writes to out. Nothing is written
// until Run is started.
func NewRecorder(out Appender, log *slog.Logger, o Options) *Recorder {
	return &Recorder{
		out:          out,
		log:          log,
		events:       make(chan Event, or(o.BufferSize, DefaultBufferSize)),
		batchSize:    or(o.BatchSize, DefaultBatchSize),
		flushEvery:   or(o.FlushEvery, DefaultFlushEvery),
		writeTimeout: or(o.WriteTimeout, DefaultWriteTimeout),
		now:          time.Now,
	}
}

func or[T int | time.Duration](v, fallback T) T {
	if v > 0 {
		return v
	}
	return fallback
}

// Record queues e for writing. When the buffer is full (Redis has been slow
// for a while), e is dropped and counted: a request never waits for its
// usage event.
func (r *Recorder) Record(e Event) {
	select {
	case r.events <- e:
	default:
		r.dropped.Add(1)
	}
}

// Dropped is how many events were lost since the start.
func (r *Recorder) Dropped() int64 { return r.dropped.Load() }

// Written is how many events reached the stream since the start.
func (r *Recorder) Written() int64 { return r.written.Load() }

// Run writes queued events until ctx ends: a batch when it is full or when
// FlushEvery has passed. Then it writes what is still queued (each batch
// with its own WriteTimeout) and returns. Stop the requests first: events
// recorded after Run returned stay in the buffer and are lost.
func (r *Recorder) Run(ctx context.Context) {
	ticker := time.NewTicker(r.flushEvery)
	defer ticker.Stop()
	batch := make([]Event, 0, r.batchSize)
	for {
		select {
		case e := <-r.events:
			batch = append(batch, e)
			if len(batch) == r.batchSize {
				batch = r.flush(batch)
			}
		case <-ticker.C:
			batch = r.flush(batch)
		case <-ctx.Done():
			r.drain(batch)
			return
		}
	}
}

// drain writes batch and everything still in the buffer.
func (r *Recorder) drain(batch []Event) {
	for {
		select {
		case e := <-r.events:
			batch = append(batch, e)
			if len(batch) == r.batchSize {
				batch = r.flush(batch)
			}
		default:
			r.flush(batch)
			return
		}
	}
}

// flush writes batch and returns it emptied for reuse.
func (r *Recorder) flush(batch []Event) []Event {
	if len(batch) == 0 {
		return batch
	}
	// Not the Run context: a shutdown must still be able to write.
	ctx, cancel := context.WithTimeout(context.Background(), r.writeTimeout)
	defer cancel()
	err := r.out.Append(ctx, batch)
	if err != nil {
		r.dropped.Add(int64(len(batch)))
	} else {
		r.written.Add(int64(len(batch)))
	}
	r.reportDrops(err)
	clear(batch) // drop the strings for the garbage collector
	return batch[:0]
}

// reportDrops logs a warning when events were lost since the last one (a
// failed write, or a full buffer while Redis was slow), at most once per
// dropWarnEvery; the next one counts everything lost in between.
func (r *Recorder) reportDrops(err error) {
	dropped := r.dropped.Load()
	now := r.now()
	if dropped == r.droppedAtWarn || (!r.lastWarn.IsZero() && now.Sub(r.lastWarn) < dropWarnEvery) {
		return
	}
	attrs := []any{"dropped", dropped - r.droppedAtWarn, "dropped_total", dropped}
	if err != nil {
		attrs = append(attrs, "error", err.Error())
	}
	r.log.Warn("usage events dropped (Redis unavailable or too slow)", attrs...)
	r.lastWarn, r.droppedAtWarn = now, dropped
}
