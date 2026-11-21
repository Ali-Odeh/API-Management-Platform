package middleware

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"log/slog"
	"net/http"
	"strings"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/proxy"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/reqctx"
	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/respond"
)

const (
	// APIKeyHeader is where clients send their key. Only the header counts:
	// keys in URLs leak into logs, browser history and proxies.
	APIKeyHeader = "X-API-Key"
	// ConsumerIDHeader tells the backend who called. Only the gateway sets
	// it; a value sent by the client is always removed.
	ConsumerIDHeader = "X-Consumer-ID"

	// Real keys are 40 characters ("ak_live_" + 32 hex). Anything far longer
	// is rejected before hashing.
	maxAPIKeyLen = 256
)

// Auth checks the API key of requests to routes with auth_required, after
// the router has matched the route:
//
//	no key                   → 401 missing_api_key
//	unknown or revoked key   → 401 invalid_api_key
//	key of a blocked consumer → 403 consumer_blocked
//	valid key                → on to the backend, with X-Consumer-ID
//
// The key is looked up in the same config snapshot the router used, by its
// SHA-256 hash (the control plane never stores the raw key). The key itself
// never reaches the backend or the log.
func Auth(log *slog.Logger) Middleware {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// Removed on every route, public ones too: the backend must not
			// get the secret, and nobody may pose as another consumer.
			keys := r.Header.Values(APIKeyHeader)
			r.Header.Del(APIKeyHeader)
			r.Header.Del(ConsumerIDHeader)

			m, ok := proxy.FromContext(r.Context())
			if !ok {
				// Only possible if main wires Auth before the router.
				log.Error("auth called without a matched route", "path", r.URL.Path)
				respond.Error(w, http.StatusInternalServerError, "internal_error")
				return
			}
			if !m.Route.AuthRequired {
				next.ServeHTTP(w, r)
				return
			}

			key, status, code := authenticate(m.Workspace, keys)
			if key != nil {
				info := reqctx.From(r.Context()) // for the request log
				info.ConsumerID = key.ConsumerID
				info.KeyID = key.ID
			}
			if status != 0 {
				if status == http.StatusUnauthorized {
					// HTTP requires a 401 to say how to authenticate.
					w.Header().Set("WWW-Authenticate", `ApiKey header="`+APIKeyHeader+`"`)
				}
				respond.Error(w, status, code)
				return
			}

			r.Header.Set(ConsumerIDHeader, key.ConsumerID)
			// The next layers (the rate limit) need the consumer and its limit.
			next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), consumerKey{}, *key)))
		})
	}
}

// consumerKey is the context key for the authenticated consumer's key entry.
type consumerKey struct{}

// consumerFrom returns the key entry auth accepted for this request; ok is
// false on public routes, where nobody is authenticated.
func consumerFrom(ctx context.Context) (key config.Key, ok bool) {
	key, ok = ctx.Value(consumerKey{}).(config.Key)
	return key, ok
}

// authenticate finds the key in the workspace. A key of another workspace is
// unknown here, so it gets 401 like any wrong key. status is 0 when the request
// may go on; key is set whenever the key was found, even for a blocked
// consumer, so the log shows who was turned away.
func authenticate(ws *config.Workspace, sent []string) (key *config.Key, status int, code string) {
	if len(sent) == 0 || strings.TrimSpace(sent[0]) == "" {
		return nil, http.StatusUnauthorized, "missing_api_key"
	}
	// Two keys in one request is ambiguous: refuse rather than guess.
	if len(sent) > 1 || len(sent[0]) > maxAPIKeyLen {
		return nil, http.StatusUnauthorized, "invalid_api_key"
	}

	found, ok := ws.Keys[hashAPIKey(sent[0])]
	if !ok {
		// Unknown and revoked keys get the same answer, so a caller can't
		// tell which keys once existed.
		return nil, http.StatusUnauthorized, "invalid_api_key"
	}
	if found.ConsumerStatus != config.ConsumerActive {
		return &found, http.StatusForbidden, "consumer_blocked"
	}
	return &found, 0, ""
}

// hashAPIKey is the control plane's hash_api_key: SHA-256 of the key's
// bytes as lowercase hex.
func hashAPIKey(raw string) string {
	sum := sha256.Sum256([]byte(raw))
	return hex.EncodeToString(sum[:])
}
