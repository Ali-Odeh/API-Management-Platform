package respond

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestJSON(t *testing.T) {
	rec := httptest.NewRecorder()

	JSON(rec, http.StatusCreated, map[string]int{"n": 1})

	if rec.Code != http.StatusCreated {
		t.Errorf("status = %d, want 201", rec.Code)
	}
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("Content-Type = %q, want application/json", ct)
	}
	if got := rec.Body.String(); got != `{"n":1}`+"\n" {
		t.Errorf("body = %q", got)
	}
}

func TestError(t *testing.T) {
	rec := httptest.NewRecorder()

	Error(rec, http.StatusNotFound, "route_not_found")

	if rec.Code != http.StatusNotFound {
		t.Errorf("status = %d, want 404", rec.Code)
	}
	if got := rec.Body.String(); got != `{"error":"route_not_found"}`+"\n" {
		t.Errorf("body = %q", got)
	}
}
