package server

import (
	"context"
	"fmt"
	"net"
	"net/http"
)

// Probe calls /health on a gateway listening on listenAddr on this machine
// and returns nil only for a 200 answer.
//
// It exists because the final Docker image has no shell, curl or wget, so
// the binary checks itself (`gateway healthcheck`).
func Probe(ctx context.Context, listenAddr string) error {
	host, port, err := net.SplitHostPort(listenAddr)
	if err != nil {
		return fmt.Errorf("listen address %q: %w", listenAddr, err)
	}
	// ":8080" or "0.0.0.0:8080" means "every interface", so loopback works.
	if host == "" || host == "0.0.0.0" || host == "::" {
		host = "127.0.0.1"
	}
	url := "http://" + net.JoinHostPort(host, port) + "/health"

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return fmt.Errorf("build request: %w", err)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return fmt.Errorf("call %s: %w", url, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("call %s: status %d", url, resp.StatusCode)
	}
	return nil
}
