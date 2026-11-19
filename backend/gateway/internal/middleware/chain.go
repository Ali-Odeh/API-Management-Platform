// Package middleware holds the layers wrapped around every API request:
// the request ID, the request log and panic recovery.
//
// A middleware takes the next handler and returns a new handler that does
// something before and/or after calling it.
package middleware

import "net/http"

// Middleware wraps a handler with extra behavior.
type Middleware func(http.Handler) http.Handler

// Chain wraps h in mws. The first middleware is the outermost one: it runs
// first on the way in and last on the way out.
//
//	Chain(h, A, B, C)  ==  A(B(C(h)))
func Chain(h http.Handler, mws ...Middleware) http.Handler {
	for i := len(mws) - 1; i >= 0; i-- {
		h = mws[i](h)
	}
	return h
}
