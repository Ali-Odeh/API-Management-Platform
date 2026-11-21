package middleware

// Benchmark of the key check: SHA-256 of the key plus one map lookup, with
// as many keys as a large customer base would have. Run by hand:
//
//	go test -run=^$ -bench=. -benchmem ./internal/middleware

import (
	"fmt"
	"testing"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
)

func BenchmarkAuthenticate(b *testing.B) {
	for _, n := range []int{100, 100_000} {
		ws := &config.Workspace{Keys: make(map[string]config.Key, n)}
		for i := range n {
			ws.Keys[hashAPIKey(fmt.Sprintf("ak_live_%032d", i))] = config.Key{ID: "k", ConsumerStatus: config.ConsumerActive}
		}
		valid := []string{fmt.Sprintf("ak_live_%032d", n/2)}

		b.Run(fmt.Sprintf("keys=%d", n), func(b *testing.B) {
			for b.Loop() {
				authenticate(ws, valid)
			}
		})
	}
}
