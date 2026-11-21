package config

import (
	"errors"
	"sync"
	"testing"
)

func workspace(id, slug string, version int64) *Workspace {
	return &Workspace{ID: id, Slug: slug, Status: WorkspaceActive, Version: version, Hosts: []string{slug}}
}

func TestNewSnapshotIndexesHosts(t *testing.T) {
	def, acme := workspace("w0", DefaultHost, 1), workspace("w1", "acme", 1)
	snap := NewSnapshot(5, def, acme)

	if snap.Seq != 5 || len(snap.Workspaces) != 2 {
		t.Fatalf("snapshot = %+v", snap)
	}
	if snap.ByHost["acme"] != acme || snap.Default() != def {
		t.Error("hosts don't lead to their workspaces")
	}
	if NewSnapshot(1, acme).Default() != nil {
		t.Error("Default() of a config without `default` should be nil")
	}
}

func TestMergeKeepsUnchangedWorkspacesAndReplacesChangedOnes(t *testing.T) {
	acme, globex := workspace("w1", "acme", 1), workspace("w2", "globex", 1)
	current := NewSnapshot(10, acme, globex)
	newAcme := workspace("w1", "acme", 2)

	next, err := merge(current, answer{Seq: 11, Workspaces: []*Workspace{newAcme}})
	if err != nil {
		t.Fatalf("merge() error: %v", err)
	}

	if next.Seq != 11 {
		t.Errorf("Seq = %d, want 11", next.Seq)
	}
	if next.Workspaces["w1"] != newAcme || next.ByHost["acme"] != newAcme {
		t.Error("the changed workspace was not replaced")
	}
	// The same pointer: nothing of globex was copied or rebuilt.
	if next.Workspaces["w2"] != globex || next.ByHost["globex"] != globex {
		t.Error("the unchanged workspace was not reused")
	}
	// The old snapshot is untouched: requests may still be reading it.
	if current.Workspaces["w1"] != acme || current.Seq != 10 {
		t.Error("merge changed the current snapshot")
	}
}

func TestMergeDropsDeletedWorkspaces(t *testing.T) {
	current := NewSnapshot(10, workspace("w1", "acme", 1), workspace("w2", "globex", 1))

	next, err := merge(current, answer{Seq: 12, Deleted: []string{"w2", "unknown"}})
	if err != nil {
		t.Fatalf("merge() error: %v", err)
	}

	if _, ok := next.Workspaces["w2"]; ok {
		t.Error("the deleted workspace is still in the config")
	}
	if _, ok := next.ByHost["globex"]; ok {
		t.Error("the deleted workspace's host still leads somewhere")
	}
	if len(next.Workspaces) != 1 || len(current.Workspaces) != 2 {
		t.Errorf("workspaces: next %d (want 1), current %d (want 2)", len(next.Workspaces), len(current.Workspaces))
	}
}

func TestMergeDropsUnreadableWorkspaces(t *testing.T) {
	current := NewSnapshot(10, workspace("w1", "acme", 1), workspace("w2", "globex", 1))

	next, err := merge(current, answer{Seq: 11, Dropped: []string{"w1"}})
	if err != nil {
		t.Fatalf("merge() error: %v", err)
	}

	if _, ok := next.Workspaces["w1"]; ok || next.ByHost["acme"] != nil {
		t.Error("the dropped workspace is still served")
	}
	if next.Workspaces["w2"] == nil {
		t.Error("the other workspace went too")
	}
}

func TestMergeFullReplacesEverything(t *testing.T) {
	current := NewSnapshot(50, workspace("w1", "acme", 9), workspace("w2", "globex", 9))
	fresh := workspace("w1", "acme", 1)

	// A lower sequence: e.g. the control plane's database was reset. The
	// control plane is the source of truth, so a full answer is taken as is.
	next, err := merge(current, answer{Seq: 3, Full: true, Workspaces: []*Workspace{fresh}})
	if err != nil {
		t.Fatalf("merge() error: %v", err)
	}

	if next.Seq != 3 || len(next.Workspaces) != 1 || next.Workspaces["w1"] != fresh {
		t.Errorf("snapshot = %+v, want only the fresh workspace at seq 3", next)
	}
}

func TestMergeRefusesAnswersThatDoNotFit(t *testing.T) {
	tests := []struct {
		name    string
		current *Snapshot
		answer  answer
	}{
		{name: "partial before any full answer", current: nil, answer: answer{Seq: 4}},
		{name: "partial answer behind ours", current: NewSnapshot(10), answer: answer{Seq: 9}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			next, err := merge(tt.current, tt.answer)
			if !errors.Is(err, errProtocol) {
				t.Fatalf("merge() = %v, %v; want errProtocol", next, err)
			}
		})
	}
}

// Requests read the current snapshot's maps while the poller merges new
// snapshots from it. merge never writes to a map a reader may hold, so
// `go test -race` (in CI) stays quiet.
func TestMergeWhileRequestsRead(t *testing.T) {
	var store Store
	store.swap(NewSnapshot(1, workspace("w1", "acme", 1), workspace("w2", "globex", 1)))

	var wg sync.WaitGroup
	for range 8 {
		wg.Go(func() {
			for range 1000 {
				snap := store.Current()
				for _, ws := range snap.Workspaces {
					_ = ws.Routes
				}
				if snap.ByHost["globex"] == nil {
					t.Error("the unchanged workspace disappeared")
					return
				}
			}
		})
	}
	for seq := int64(2); seq <= 1000; seq++ {
		next, err := merge(store.Current(), answer{Seq: seq, Workspaces: []*Workspace{workspace("w1", "acme", seq)}})
		if err != nil {
			t.Fatalf("merge() error: %v", err)
		}
		store.swap(next)
	}
	wg.Wait()
}
