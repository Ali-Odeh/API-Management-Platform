// Package respond writes the gateway's own JSON responses, so every answer
// the gateway produces itself (not the backend's) has the same shape.
// Errors are always {"error": "<code>"} (PLAN.md section 7).
package respond

import (
	"encoding/json"
	"net/http"
)

// JSON writes body as JSON with the given status code.
func JSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

// Error writes {"error": code} with the given status code. code is a short,
// stable, machine-readable name such as "route_not_found".
func Error(w http.ResponseWriter, status int, code string) {
	JSON(w, status, map[string]string{"error": code})
}
