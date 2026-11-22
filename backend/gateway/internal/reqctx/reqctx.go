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
	"sync/atomic"
	"time"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
)

// Info is the "notes card" of one request. All writes and the final read
// happen in the request's own goroutine (the proxy calls the backend
// synchronously), so it needs no lock; the one field written elsewhere
// (BodyLimit) is atomic.
type Info struct {
	RequestID  string
	Workspace  *config.Workspace // set by tenant: the Host's workspace; nil if none was found
	RouteID    string            // set by the router when a route matched
	TargetID   string            // set by the forwarder: the target the request was sent to
	ConsumerID string            // set by auth when the key was found
	KeyID      string            // which of the consumer's keys was used (never the key itself)
	RateLimit  string            // "" (not checked), "ok", "limited", "limited_workspace" or "unavailable" (Redis down, let through)
	Upstream   time.Duration     // time until the backend's response headers; 0 if not called
	// BodyLimit is the route's body limit, set when the request body went
	// over it (MT.11); 0 otherwise. Atomic: an unannounced body is read by
	// the transport's own goroutine while it is uploaded.
	BodyLimit atomic.Int64
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

// WorkspaceID is the request's workspace for the log, or "" if none was found.
func (i *Info) WorkspaceID() string {
	if i.Workspace == nil {
		return ""
	}
	return i.Workspace.ID
}
