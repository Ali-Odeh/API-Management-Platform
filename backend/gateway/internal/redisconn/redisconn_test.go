package redisconn

import (
	"strings"
	"testing"
	"time"
)

func TestOpenSetsTheTimeoutAndNoRetries(t *testing.T) {
	// A hostname that can't be resolved, like Redis's container when it is
	// down: building the client must not wait for DNS.
	start := time.Now()
	rdb, err := Open("redis://:pw@redis-is-down.invalid:6379/2", 75*time.Millisecond)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	defer rdb.Close()
	if took := time.Since(start); took > 100*time.Millisecond {
		t.Errorf("Open took %s; it must not block the gateway's start", took)
	}

	o := rdb.Options()
	if o.Addr != "redis-is-down.invalid:6379" || o.DB != 2 || o.Password != "pw" {
		t.Errorf("parsed %s db %d", o.Addr, o.DB)
	}
	// go-redis turns our MaxRetries -1 ("none") into 0 once the client is
	// built; an input of 0 would have meant its default, 3.
	want := 75 * time.Millisecond
	if o.ReadTimeout != want || o.WriteTimeout != want || o.PoolTimeout != want ||
		o.DialTimeout != time.Second || o.MaxRetries != 0 {
		t.Errorf("timeouts read %s write %s pool %s dial %s, retries %d",
			o.ReadTimeout, o.WriteTimeout, o.PoolTimeout, o.DialTimeout, o.MaxRetries)
	}
}

func TestOpenBadURLDoesNotRepeatIt(t *testing.T) {
	_, err := Open("http://:secret@redis:6379", time.Second)
	if err == nil || strings.Contains(err.Error(), "secret") {
		t.Errorf("Open error = %v; want one that does not show the URL", err)
	}
}
