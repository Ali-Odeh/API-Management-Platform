package middleware

import (
	"crypto/rand"
	"net/http"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
)

// RequestIDHeader carries the request ID to the backend and back to the client.
const RequestIDHeader = "X-Request-ID"

const maxRequestIDLen = 128

// RequestID gives every request an ID, like a tracking number.
//
// A safe ID sent by the client is kept, so a customer can report "request
// abc-123 failed" and it can be found (Kong's style). Anything else is
// replaced by a new random ID. The ID is set on the request (the proxy
// forwards it to the backend), on the response (the client sees it) and in
// the request's reqctx.Info (the log line shows it).
func RequestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get(RequestIDHeader)
		if !validRequestID(id) {
			id = rand.Text() // 26 characters, 128 random bits
		}
		r.Header.Set(RequestIDHeader, id)
		w.Header().Set(RequestIDHeader, id)

		ctx := reqctx.With(r.Context(), &reqctx.Info{RequestID: id})
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

// validRequestID accepts 1 to 128 characters from A-Z a-z 0-9 . _ -, so a
// client-chosen ID can never break a log line or a header.
func validRequestID(id string) bool {
	if id == "" || len(id) > maxRequestIDLen {
		return false
	}
	for i := 0; i < len(id); i++ {
		c := id[i]
		ok := c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' ||
			c == '.' || c == '_' || c == '-'
		if !ok {
			return false
		}
	}
	return true
}
