package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"uptimetrace/agent/internal/config"
)

const tok = "utk_END2ENDTESTTOKEN0123456789abcdef"

type recorder struct {
	mu   sync.Mutex
	got  []map[string]any
	auth []string
}

func (r *recorder) ServeHTTP(w http.ResponseWriter, req *http.Request) {
	b, _ := io.ReadAll(req.Body)
	var doc map[string]any
	_ = json.Unmarshal(b, &doc)
	r.mu.Lock()
	r.got = append(r.got, doc)
	r.auth = append(r.auth, req.Header.Get("Authorization"))
	r.mu.Unlock()
	w.WriteHeader(http.StatusAccepted)
	io.WriteString(w, `{"status":"accepted"}`)
}

func (r *recorder) count() int { r.mu.Lock(); defer r.mu.Unlock(); return len(r.got) }

func env(vars map[string]string) config.Env {
	e := config.OSEnv()
	e.Getenv = func(k string) string { return vars[k] }
	return e
}

func start(t *testing.T, args []string, url string) (cancel func(), result <-chan int, logs *bytes.Buffer) {
	t.Helper()
	ctx, c := context.WithCancel(context.Background())
	logs = &bytes.Buffer{}
	res := make(chan int, 1)
	vars := map[string]string{"UPTIMETRACE_TOKEN": tok, "UPTIMETRACE_URL": url}
	go func() { res <- run(ctx, args, env(vars), io.Discard, logs) }()
	return c, res, logs
}

func TestAgentEndToEndDeliversAuthenticatedContractEnvelopesAndStopsGracefully(t *testing.T) {
	rec := &recorder{}
	srv := httptest.NewServer(rec)
	defer srv.Close()
	cancel, res, logs := start(t, []string{"-host-id", "e2e-host", "-agent-id", "agent-e2e", "-interval", "1s", "-flush-interval", "1s"}, srv.URL)
	deadline := time.After(10 * time.Second)
	for rec.count() < 2 {
		select {
		case <-deadline:
			cancel()
			t.Fatalf("no envelopes arrived; logs:\n%s", logs)
		case <-time.After(20 * time.Millisecond):
		}
	}
	cancel()
	select {
	case code := <-res:
		if code != 0 {
			t.Fatalf("exit code %d; logs:\n%s", code, logs)
		}
	case <-time.After(15 * time.Second):
		t.Fatal("agent did not shut down")
	}
	for i, a := range rec.auth {
		if a != "Bearer "+tok {
			t.Fatalf("request %d authorization %q", i, a)
		}
	}
	d := rec.got[0]
	if d["schema_version"] != "1.0" || d["agent_id"] != "agent-e2e" || d["host_id"] != "e2e-host" || d["envelope_id"] == "" {
		t.Fatalf("envelope: %v", d)
	}
	if strings.Contains(logs.String(), tok) || !strings.Contains(logs.String(), "agent stopped") {
		t.Fatalf("logs must not contain the token and must report the stop:\n%s", logs)
	}
	ids := map[any]bool{}
	for _, e := range rec.got {
		ids[e["envelope_id"]] = true
	}
	if len(ids) != len(rec.got) {
		t.Fatal("two envelopes shared an id")
	}
}

func TestShutdownFlushesObservationsThatWereStillPending(t *testing.T) {
	rec := &recorder{}
	srv := httptest.NewServer(rec)
	defer srv.Close()
	// flush interval of an hour: nothing is cut into an envelope until shutdown forces it
	cancel, res, logs := start(t, []string{"-host-id", "flush-host", "-agent-id", "agent-flush", "-interval", "1s", "-flush-interval", "1h"}, srv.URL)
	time.Sleep(2500 * time.Millisecond)
	if rec.count() != 0 {
		t.Fatalf("nothing should be sent before the flush interval, got %d", rec.count())
	}
	cancel()
	if code := <-res; code != 0 {
		t.Fatalf("exit %d\n%s", code, logs)
	}
	if rec.count() < 1 {
		t.Fatalf("pending observations were lost at shutdown; logs:\n%s", logs)
	}
	metrics, _ := rec.got[0]["metrics"].([]any)
	if len(metrics) < 3 {
		t.Fatalf("flushed envelope has %d metrics", len(metrics))
	}
}

func TestDryRunPrintsEnvelopesAndContactsNothing(t *testing.T) {
	var out, errb bytes.Buffer
	code := run(context.Background(), []string{"-dry-run", "-host-id", "dry-host", "-agent-id", "agent-dry"}, env(nil), &out, &errb)
	if code != 0 {
		t.Fatalf("exit %d: %s", code, errb.String())
	}
	var d map[string]any
	if err := json.Unmarshal(bytes.TrimSpace(out.Bytes()), &d); err != nil {
		t.Fatalf("stdout is not one JSON envelope: %v\n%s", err, out.String())
	}
	if d["host_id"] != "dry-host" || len(d["metrics"].([]any)) < 3 {
		t.Fatalf("%v", d)
	}
}

func TestMisconfigurationExitsNonZeroWithoutPrintingSecrets(t *testing.T) {
	var out, errb bytes.Buffer
	code := run(context.Background(), []string{"-batch-max", "501"}, env(map[string]string{"UPTIMETRACE_TOKEN": tok}), &out, &errb)
	if code != 2 || strings.Contains(errb.String(), tok) || !strings.Contains(errb.String(), "batch-max") {
		t.Fatalf("code %d stderr %q", code, errb.String())
	}
	errb.Reset()
	if code := run(context.Background(), nil, env(nil), &out, &errb); code != 2 || !strings.Contains(errb.String(), "no bearer token") {
		t.Fatalf("missing token: code %d %q", code, errb.String())
	}
}
