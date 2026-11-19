// Package reqctx carries per-request facts through the request context: the
// request ID, and the details the request log needs from deeper layers.
//
// The outer middleware puts an empty *Info into the context; the router, the
// forwarder and (from step 3.6) auth fill it in; the logger reads it once the
// answer is done. It lives in its own small package so all of them can use
// it without importing each other.
package reqctx

import (
	"context"
	"time"
)

// Info is the "notes card" of one request. All writes and the final read
// happen in the request's own goroutine (the proxy calls the backend
// synchronously), so it needs no lock.
type Info struct {
	RequestID  string
	RouteID    string        // set by the router when a route matched
	ConsumerID string        // set by auth when the key was found
	KeyID      string        // which of the consumer's keys was used (never the key itself)
	RateLimit  string        // "" (not limited), "ok", "limited" or "unavailable" (Redis down, let through)
	Upstream   time.Duration // time until the backend's response headers; 0 if not called
}

type key struct{}

// With returns a context that carries info.
func With(ctx context.Context, info *Info) context.Context {
	return context.WithValue(ctx, key{}, info)
}

// From returns the request's Info. It never returns nil: without one in the
// context it returns a throwaway Info, so writers never need a nil check.
func From(ctx context.Context) *Info {
	if info, ok := ctx.Value(key{}).(*Info); ok {
		return info
	}
	return &Info{}
}

// Has reports whether ctx already carries an Info.
func Has(ctx context.Context) bool {
	_, ok := ctx.Value(key{}).(*Info)
	return ok
}
