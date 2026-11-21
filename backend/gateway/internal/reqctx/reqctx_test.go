package reqctx

import (
	"context"
	"testing"

	"github.com/Ali-Odeh/API-Management-Platform/backend/gateway/internal/config"
)

func TestWithAndFrom(t *testing.T) {
	info := &Info{RequestID: "abc"}
	ctx := With(context.Background(), info)

	if !Has(ctx) {
		t.Fatal("Has() = false after With")
	}
	got := From(ctx)
	if got != info {
		t.Fatal("From() returned a different Info than the one stored")
	}
	got.RouteID = "r1" // writes reach the same card
	if info.RouteID != "r1" {
		t.Error("a write through From() was lost")
	}
}

func TestFromWithoutInfoIsSafe(t *testing.T) {
	ctx := context.Background()
	if Has(ctx) {
		t.Fatal("Has() = true on an empty context")
	}
	info := From(ctx)
	if info == nil {
		t.Fatal("From() returned nil")
	}
	info.RouteID = "ignored" // must not panic
}

func TestWorkspaceID(t *testing.T) {
	info := &Info{}
	if got := info.WorkspaceID(); got != "" {
		t.Errorf("WorkspaceID() = %q before tenant ran, want empty", got)
	}
	info.Workspace = &config.Workspace{ID: "w1"}
	if got := info.WorkspaceID(); got != "w1" {
		t.Errorf("WorkspaceID() = %q, want w1", got)
	}
}
