package ratelimit

import "testing"

func TestNewClientUsesTheLimitChecksTimeout(t *testing.T) {
	rdb, err := NewClient("redis://redis-is-down.invalid:6379/2")
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	defer rdb.Close()
	if o := rdb.Options(); o.ReadTimeout != DefaultTimeout || o.WriteTimeout != DefaultTimeout {
		t.Errorf("timeouts read %s write %s, want %s", o.ReadTimeout, o.WriteTimeout, DefaultTimeout)
	}
}
