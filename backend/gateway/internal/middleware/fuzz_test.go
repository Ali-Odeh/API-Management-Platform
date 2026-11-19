package middleware

import (
	"regexp"
	"testing"
)

// The same rule as validRequestID, written as a regular expression: an
// independent second opinion.
var requestIDRule = regexp.MustCompile(`^[A-Za-z0-9._-]{1,128}$`)

// A client-chosen request ID ends up in log lines and headers, so
// validRequestID must accept exactly the safe ones: nothing more, nothing
// less. Run longer with
//
//	go test -run=^$ -fuzz=FuzzValidRequestID -fuzztime=30s ./internal/middleware
func FuzzValidRequestID(f *testing.F) {
	for _, s := range []string{"abc-123", "", "a b", "a\nb", "<script>", "ü", "a.b_c-D9", "\x00"} {
		f.Add(s)
	}
	f.Fuzz(func(t *testing.T, id string) {
		if got, want := validRequestID(id), requestIDRule.MatchString(id); got != want {
			t.Errorf("validRequestID(%q) = %v, want %v", id, got, want)
		}
	})
}
