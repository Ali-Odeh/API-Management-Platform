package ratelimit

import (
	"strings"
	"testing"
	"time"
)

func TestNewClientUsesShortTimeoutsAndNoRetries(t *testing.T) {
	// A hostname that can't be resolved, like Redis's container when it is
	// down: building the client must not wait for DNS.
	start := time.Now()
	rdb, err := NewClient("redis://:pw@redis-is-down.invalid:6379/2")
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	defer rdb.Close()
	if took := time.Since(start); took > 100*time.Millisecond {
		t.Errorf("NewClient took %s; it must not block the gateway's start", took)
	}

	o := rdb.Options()
	if o.Addr != "redis-is-down.invalid:6379" || o.DB != 2 || o.Password != "pw" {
		t.Errorf("parsed %s db %d", o.Addr, o.DB)
	}
	// go-redis turns our MaxRetries -1 ("none") into 0 once the client is
	// built; an input of 0 would have meant its default, 3.
	if o.ReadTimeout != DefaultTimeout || o.WriteTimeout != DefaultTimeout || o.PoolTimeout != DefaultTimeout ||
		o.DialTimeout != time.Second || o.MaxRetries != 0 {
		t.Errorf("timeouts read %s write %s pool %s dial %s, retries %d",
			o.ReadTimeout, o.WriteTimeout, o.PoolTimeout, o.DialTimeout, o.MaxRetries)
	}
}

func TestNewClientBadURLDoesNotRepeatIt(t *testing.T) {
	_, err := NewClient("http://:secret@redis:6379")
	if err == nil || strings.Contains(err.Error(), "secret") {
		t.Errorf("NewClient error = %v; want one that does not show the URL", err)
	}
}
