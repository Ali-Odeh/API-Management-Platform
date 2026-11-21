package config

import (
	"errors"
	"fmt"
	"maps"
	"slices"
)

// errProtocol marks an answer that doesn't fit the snapshot it would be
// merged into. The poller then asks for everything again (since=0) instead
// of guessing.
var errProtocol = errors.New("config protocol error")

// merge builds the snapshot that follows current once answer a is applied.
//
// A full answer replaces everything (also with a lower sequence: after a
// database reset the control plane is the source of truth). Otherwise the
// new snapshot reuses every unchanged *Workspace of current, replaces the
// changed ones and drops the deleted ones, so applying a change in one
// workspace costs the same however many workspaces there are.
//
// current is never modified: request handlers may be reading it.
func merge(current *Snapshot, a answer) (*Snapshot, error) {
	if a.Full {
		return NewSnapshot(a.Seq, a.Workspaces...), nil
	}
	if current == nil {
		return nil, fmt.Errorf("%w: a partial answer before any full one", errProtocol)
	}
	if a.Seq < current.Seq {
		return nil, fmt.Errorf("%w: a partial answer at seq %d, behind ours (%d)", errProtocol, a.Seq, current.Seq)
	}

	next := &Snapshot{
		Seq:        a.Seq,
		Workspaces: maps.Clone(current.Workspaces), // a new map holding the same pointers
		ByHost:     make(map[string]*Workspace, len(current.Workspaces)),
	}
	for _, id := range slices.Concat(a.Deleted, a.Dropped) {
		delete(next.Workspaces, id)
	}
	for _, ws := range a.Workspaces {
		next.Workspaces[ws.ID] = ws
	}
	next.indexHosts()
	return next, nil
}
