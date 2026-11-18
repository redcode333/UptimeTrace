package exporter

import (
	"bytes"
	"context"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"uptimetrace/agent/internal/buffer"
)

const token = "utk_SECRETSECRETSECRETSECRET0123456789"

type rec struct {
	auth, ctype, ua, body string
}

// script serves the given statuses in order (the last repeats) and records every request.
type script struct {
	mu       sync.Mutex
	statuses []int
	headers  map[string]string
	reqs     []rec
}

func (s *script) handler(w http.ResponseWriter, r *http.Request) {
	b, _ := io.ReadAll(r.Body)
	s.mu.Lock()
	i := len(s.reqs)
	s.reqs = append(s.reqs, rec{r.Header.Get("Authorization"), r.Header.Get("Content-Type"), r.Header.Get("User-Agent"), string(b)})
	st := s.statuses[len(s.statuses)-1]
	if i < len(s.statuses) {
		st = s.statuses[i]
	}
	s.mu.Unlock()
	for k, v := range s.headers {
		w.Header().Set(k, v)
	}
	w.WriteHeader(st)
	if st >= 400 {
		io.WriteString(w, `{"error":{"code":"x","message":"msg"}}`)
	}
}

func (s *script) count() int { s.mu.Lock(); defer s.mu.Unlock(); return len(s.reqs) }

type harness struct {
	exp    *Exporter
	buf    *buffer.Buffer
	logs   *bytes.Buffer
	sleeps []time.Duration
}

func newHarness(url string) *harness {
	h := &harness{buf: buffer.New(100), logs: &bytes.Buffer{}}
	h.exp = &Exporter{URL: url + "/api/v1/telemetry", Token: token, Client: &http.Client{Timeout: 2 * time.Second}, Buf: h.buf,
		Log: slog.New(slog.NewTextHandler(h.logs, &slog.HandlerOptions{Level: slog.LevelDebug})), BackoffBase: time.Second, BackoffMax: time.Minute,
		UserAgent: "uptimetrace-agent/test", Rand: func() float64 { return 1 }}
	h.exp.Sleep = func(ctx context.Context, d time.Duration) bool {
		h.sleeps = append(h.sleeps, d)
		return ctx.Err() == nil
	}
	return h
}

func entry(id string) buffer.Entry {
	return buffer.Entry{ID: id, Body: []byte(`{"envelope_id":"` + id + `"}`), Observations: 1}
}

// runUntilEmpty runs the delivery loop until the buffer is empty (or 5 s), then stops it.
func (h *harness) runUntilEmpty(t *testing.T) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { h.exp.Run(ctx); close(done) }()
	deadline := time.After(5 * time.Second)
	for h.buf.Len() > 0 {
		select {
		case <-deadline:
			cancel()
			t.Fatalf("buffer not drained: %d left; logs:\n%s", h.buf.Len(), h.logs)
		case <-time.After(5 * time.Millisecond):
		}
	}
	cancel()
	<-done
}

func TestSuccessSendsBearerTokenJsonAndTheExactBytes(t *testing.T) {
	s := &script{statuses: []int{202}}
	srv := httptest.NewServer(http.HandlerFunc(s.handler))
	defer srv.Close()
	h := newHarness(srv.URL)
	h.buf.Push(entry("e1"))
	h.runUntilEmpty(t)
	if s.count() != 1 {
		t.Fatalf("requests: %d", s.count())
	}
	r := s.reqs[0]
	if r.auth != "Bearer "+token || r.ctype != "application/json" || r.ua != "uptimetrace-agent/test" || r.body != string(entry("e1").Body) {
		t.Fatalf("request: %+v", r)
	}
	if h.exp.Sent != 1 || h.exp.Dropped != 0 || h.exp.Retries != 0 {
		t.Fatalf("counters %+v", h.exp)
	}
}

func TestPermanentClientErrorsAreNeverRetried(t *testing.T) {
	for _, code := range []int{400, 401, 403, 409, 413, 415, 422} {
		s := &script{statuses: []int{code, 202}}
		srv := httptest.NewServer(http.HandlerFunc(s.handler))
		h := newHarness(srv.URL)
		h.buf.Push(entry("bad"), entry("good"))
		h.runUntilEmpty(t)
		srv.Close()
		if s.count() != 2 || s.reqs[0].body == s.reqs[1].body {
			t.Fatalf("%d: want exactly one attempt for the rejected envelope then the next one; got %d requests", code, s.count())
		}
		if h.exp.Dropped != 1 || h.exp.Sent != 1 || h.exp.Retries != 0 || len(h.sleeps) != 0 {
			t.Fatalf("%d: counters %+v sleeps %v", code, h.exp, h.sleeps)
		}
	}
}

func TestTransientFailuresAreRetriedWithTheSameEnvelopeAndGrowingBackoff(t *testing.T) {
	s := &script{statuses: []int{500, 503, 500, 202}}
	srv := httptest.NewServer(http.HandlerFunc(s.handler))
	defer srv.Close()
	h := newHarness(srv.URL)
	h.buf.Push(entry("retry-me"))
	h.runUntilEmpty(t)
	if s.count() != 4 {
		t.Fatalf("requests %d", s.count())
	}
	for _, r := range s.reqs {
		if r.body != string(entry("retry-me").Body) { // identical bytes, hence the same envelope_id, on every attempt
			t.Fatalf("a retry changed the envelope: %s", r.body)
		}
	}
	// Rand pinned to 1 so the jittered wait equals its ceiling: base 1s doubling
	if len(h.sleeps) != 3 || h.sleeps[0] != time.Second || h.sleeps[1] != 2*time.Second || h.sleeps[2] != 4*time.Second {
		t.Fatalf("backoff %v", h.sleeps)
	}
	if h.exp.Sent != 1 || h.exp.Retries != 3 || h.exp.Dropped != 0 {
		t.Fatalf("counters %+v", h.exp)
	}
}

func TestRetryAfterIsHonouredAndNetworkErrorsAreRetried(t *testing.T) {
	s := &script{statuses: []int{429, 202}, headers: map[string]string{"Retry-After": "7"}}
	srv := httptest.NewServer(http.HandlerFunc(s.handler))
	h := newHarness(srv.URL)
	h.exp.Rand = func() float64 { return 0 } // jitter 0: only Retry-After can make the wait 7s
	h.buf.Push(entry("rl"))
	h.runUntilEmpty(t)
	srv.Close()
	if len(h.sleeps) != 1 || h.sleeps[0] != 7*time.Second {
		t.Fatalf("Retry-After ignored: %v", h.sleeps)
	}

	dead := httptest.NewServer(http.HandlerFunc(s.handler))
	url := dead.URL
	dead.Close() // nothing listens any more: a real connection failure, no mock
	h2 := newHarness(url)
	en := entry("net")
	if out, _, detail := h2.exp.Send(context.Background(), en); out != Retry || detail != "network error" {
		t.Fatalf("connection refused must be a retry, got %v %q", out, detail)
	}
}

func TestBackoffIsExponentialJitteredCappedAndOverflowSafe(t *testing.T) {
	base, max := time.Second, time.Minute
	for attempt, want := range map[int]time.Duration{0: time.Second, 1: 2 * time.Second, 2: 4 * time.Second, 5: 32 * time.Second, 6: time.Minute, 10: time.Minute, 29: time.Minute, 30: time.Minute, 1000: time.Minute} {
		if got := Backoff(base, max, attempt, 1); got != want {
			t.Errorf("attempt %d: %v want %v", attempt, got, want)
		}
		if got := Backoff(base, max, attempt, 0); got != 0 {
			t.Errorf("attempt %d with zero jitter: %v", attempt, got)
		}
		if got := Backoff(base, max, attempt, 0.5); got < 0 || got > want {
			t.Errorf("attempt %d jitter out of range: %v", attempt, got)
		}
	}
}

func TestTheTokenNeverReachesTheLogsOnAnyPath(t *testing.T) {
	for _, statuses := range [][]int{{401}, {403}, {500, 202}, {429, 202}, {422}} {
		s := &script{statuses: statuses}
		srv := httptest.NewServer(http.HandlerFunc(s.handler))
		h := newHarness(srv.URL)
		h.buf.Push(entry("x"))
		h.runUntilEmpty(t)
		srv.Close()
		if out := h.logs.String(); strings.Contains(out, token) || strings.Contains(out, "SECRET") || strings.Contains(strings.ToLower(out), "authorization") {
			t.Fatalf("token or auth header leaked into logs for %v:\n%s", statuses, out)
		}
	}
	dead := httptest.NewServer(http.HandlerFunc((&script{statuses: []int{202}}).handler))
	url := dead.URL
	dead.Close()
	h := newHarness(url)
	h.buf.Push(entry("y"))
	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancel()
	h.exp.Sleep = DefaultSleep
	h.exp.BackoffBase = 10 * time.Millisecond
	h.exp.Run(ctx)
	if strings.Contains(h.logs.String(), token) {
		t.Fatal("token leaked on the network-error path")
	}
}

func TestAuthenticationFailureIsLoggedWithAHintAndDoesNotLoopForever(t *testing.T) {
	s := &script{statuses: []int{401}}
	srv := httptest.NewServer(http.HandlerFunc(s.handler))
	defer srv.Close()
	h := newHarness(srv.URL)
	h.buf.Push(entry("a"), entry("b"), entry("c"))
	h.runUntilEmpty(t)
	if s.count() != 3 || h.exp.Dropped != 3 || h.exp.Retries != 0 {
		t.Fatalf("requests %d counters %+v", s.count(), h.exp)
	}
	if !strings.Contains(h.logs.String(), "check the bearer token") || !strings.Contains(h.logs.String(), "HTTP 401") {
		t.Fatalf("expected an actionable log line:\n%s", h.logs)
	}
}

func TestRunStopsPromptlyWhenCancelledDuringBackoff(t *testing.T) {
	s := &script{statuses: []int{500}}
	srv := httptest.NewServer(http.HandlerFunc(s.handler))
	defer srv.Close()
	h := newHarness(srv.URL)
	h.exp.Sleep = DefaultSleep
	h.exp.BackoffBase, h.exp.BackoffMax = time.Hour, time.Hour
	h.exp.Rand = func() float64 { return 1 }
	h.buf.Push(entry("stuck"))
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { h.exp.Run(ctx); close(done) }()
	for s.count() == 0 {
		time.Sleep(2 * time.Millisecond)
	}
	time.Sleep(20 * time.Millisecond) // now inside a one-hour backoff
	start := time.Now()
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("Run did not stop on cancellation")
	}
	if time.Since(start) > time.Second || h.buf.Len() != 1 {
		t.Fatalf("slow stop %v, buffered %d (the undelivered envelope must stay buffered for Drain)", time.Since(start), h.buf.Len())
	}
}

func TestDrainDeliversEverythingWhenUpAndGivesUpAfterOneFailureWhenDown(t *testing.T) {
	s := &script{statuses: []int{202}}
	srv := httptest.NewServer(http.HandlerFunc(s.handler))
	h := newHarness(srv.URL)
	h.buf.Push(entry("1"), entry("2"), entry("3"))
	h.exp.Drain(context.Background())
	srv.Close()
	if h.buf.Len() != 0 || s.count() != 3 || h.exp.Sent != 3 {
		t.Fatalf("drain with server up: left %d requests %d", h.buf.Len(), s.count())
	}

	down := httptest.NewServer(http.HandlerFunc(s.handler))
	url := down.URL
	down.Close()
	h2 := newHarness(url)
	h2.buf.Push(entry("a"), entry("b"))
	start := time.Now()
	h2.exp.Drain(context.Background())
	if h2.buf.Len() != 2 || time.Since(start) > 3*time.Second || !strings.Contains(h2.logs.String(), "undelivered envelopes are lost") {
		t.Fatalf("drain with server down: left %d after %v\n%s", h2.buf.Len(), time.Since(start), h2.logs)
	}
}
