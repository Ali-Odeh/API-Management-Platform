package proxy

// Fuzz tests: Go generates random inputs (starting from the seeds below) and
// checks a rule that must hold for every input. Run one for a while with
//
//	go test -run=^$ -fuzz=FuzzMatch -fuzztime=30s ./internal/proxy
//
// Inputs that break a rule are saved in testdata/fuzz and then run by every
// plain `go test`, so a bug found once is checked forever.

import (
	"net/url"
	"slices"
	"strings"
	"testing"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
)

var pathSeeds = []string{
	"/", "/v1", "/v1/orders", "/v1/orders/", "/v1/orders/7", "/v1/orders-archive",
	"/v1/../admin", "/v1/./x", "/v1//x", "//", "/..", "", "v1", "/V1/orders", "/v1/orders/a%2Fb",
}

// validPrefix is what the control plane allows as path_prefix: absolute, no
// trailing slash (except the root), no empty, "." or ".." segments.
func validPrefix(p string) bool {
	return p == "/" || (strings.HasPrefix(p, "/") && !strings.HasSuffix(p, "/") && isCleanPath(p))
}

// fitsBySegments is the obviously correct, slower version of prefixFits:
// split both into segments and compare them one by one.
func fitsBySegments(prefix, path string) bool {
	if prefix == "/" {
		return true
	}
	ps, xs := strings.Split(prefix, "/"), strings.Split(path, "/")
	if len(xs) < len(ps) {
		return false
	}
	for i := range ps {
		if ps[i] != xs[i] {
			return false
		}
	}
	return true
}

func FuzzPrefixFits(f *testing.F) {
	for _, p := range pathSeeds {
		f.Add("/v1/orders", p)
		f.Add("/", p)
	}
	f.Fuzz(func(t *testing.T, prefix, path string) {
		if !validPrefix(prefix) {
			t.Skip()
		}
		if got, want := prefixFits(prefix, path), fitsBySegments(prefix, path); got != want {
			t.Errorf("prefixFits(%q, %q) = %v, but by segments it is %v", prefix, path, got, want)
		}
	})
}

func FuzzIsCleanPath(f *testing.F) {
	for _, p := range pathSeeds {
		f.Add(p)
	}
	f.Fuzz(func(t *testing.T, p string) {
		if !isCleanPath(p) {
			return
		}
		// A path called clean can never climb out of a prefix.
		if !strings.HasPrefix(p, "/") || strings.Contains(p, "//") {
			t.Fatalf("isCleanPath(%q) = true, but it is not absolute or has an empty segment", p)
		}
		for _, seg := range strings.Split(p, "/") {
			if seg == "." || seg == ".." {
				t.Fatalf("isCleanPath(%q) = true, but it has a %q segment", p, seg)
			}
		}
	})
}

// Route list like a real snapshot: longest prefix first, overlapping
// prefixes, different methods, and a root route.
var fuzzRoutes = []config.Route{
	route("archive", "/v1/orders/archive", "GET"),
	route("orders", "/v1/orders", "GET", "POST"),
	route("v1", "/v1", "GET", "DELETE"),
	route("root", "/", "OPTIONS"),
}

func FuzzMatch(f *testing.F) {
	for _, p := range pathSeeds {
		for _, m := range []string{"GET", "POST", "DELETE", "PUT", "OPTIONS"} {
			f.Add(p, m)
		}
	}
	f.Fuzz(func(t *testing.T, path, method string) {
		got, allowed := match(fuzzRoutes, path, method)

		// Reference: of the routes whose prefix fits and that allow the
		// method, the one with the longest prefix.
		var want *config.Route
		var wantAllowed []string
		for i := range fuzzRoutes {
			r := &fuzzRoutes[i]
			if !fitsBySegments(r.PathPrefix, path) {
				continue
			}
			if slices.Contains(r.Methods, method) {
				if want == nil || len(r.PathPrefix) > len(want.PathPrefix) {
					want = r
				}
			} else {
				wantAllowed = append(wantAllowed, r.Methods...)
			}
		}

		if got != want {
			t.Fatalf("match(%q, %q) = %v, want %v", path, method, got, want)
		}
		if want == nil {
			slices.Sort(wantAllowed)
			if wantAllowed = slices.Compact(wantAllowed); !slices.Equal(allowed, wantAllowed) {
				t.Errorf("allowed = %v, want %v", allowed, wantAllowed)
			}
		}
	})
}

func FuzzStripPrefix(f *testing.F) {
	for _, p := range pathSeeds {
		f.Add("/v1/orders", p)
		f.Add("/", p)
	}
	f.Fuzz(func(t *testing.T, prefix, path string) {
		if !validPrefix(prefix) || !isCleanPath(path) || !prefixFits(prefix, path) {
			t.Skip() // the router only strips clean paths it matched
		}
		u := &url.URL{Path: path}
		stripPrefix(u, prefix)

		switch {
		case !strings.HasPrefix(u.Path, "/"):
			t.Fatalf("strip %q from %q gave %q: not absolute", prefix, path, u.Path)
		case prefix == "/":
			if u.Path != path {
				t.Fatalf("the root prefix changed %q to %q", path, u.Path)
			}
		case path == prefix || path == prefix+"/":
			if u.Path != "/" {
				t.Fatalf("strip %q from %q gave %q, want /", prefix, path, u.Path)
			}
		default:
			// Nothing lost, nothing added: prefix + rest is the original.
			if prefix+u.Path != path {
				t.Fatalf("strip %q from %q gave %q", prefix, path, u.Path)
			}
		}
	})
}
