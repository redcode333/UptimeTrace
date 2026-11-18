// Package exporter delivers buffered envelopes to the API with bounded retries.
//
// Delivery rules (contract README, "Rule of thumb for agents"): retry 429, 5xx and network errors with exponential backoff
// and jitter; never retry any other 4xx, because resending the same bytes cannot succeed. The bearer token is only ever put
// in the Authorization header: it is not logged, not part of any error text, and not in the URL.
package exporter

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"math/rand"
	"net/http"
	"strconv"
	"time"

	"uptimetrace/agent/internal/buffer"
)

type Outcome int

const (
	Delivered Outcome = iota
	Retry             // transient: try again later with the SAME envelope
	Drop              // permanent: the server will never accept these bytes
)

type Exporter struct {
	URL         string // full endpoint, e.g. http://host:8000/api/v1/telemetry
	Token       string
	Client      *http.Client
	Buf         *buffer.Buffer
	Log         *slog.Logger
	BackoffBase time.Duration
	BackoffMax  time.Duration
	UserAgent   string
	Rand        func() float64 // [0,1); injectable for tests
	Sleep       func(ctx context.Context, d time.Duration) bool

	Sent, Dropped, Retries uint64 // counters, read after Run returns
}

// Backoff is exponential with full jitter: a uniform value in [0, min(max, base*2^attempt)].
func Backoff(base, max time.Duration, attempt int, rnd float64) time.Duration {
	ceiling := max
	if attempt < 30 { // beyond that base<<attempt would overflow; the cap applies anyway
		if c := base << uint(attempt); c > 0 && c < max {
			ceiling = c
		}
	}
	return time.Duration(float64(ceiling) * rnd)
}

// Send makes one attempt. retryAfter is the server's Retry-After for a 429/503 (zero when absent).
func (e *Exporter) Send(ctx context.Context, en buffer.Entry) (out Outcome, retryAfter time.Duration, detail string) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, e.URL, bytes.NewReader(en.Body))
	if err != nil {
		return Drop, 0, "cannot build request"
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+e.Token)
	req.Header.Set("User-Agent", e.UserAgent)
	resp, err := e.Client.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return Retry, 0, "interrupted"
		}
		var ue interface{ Timeout() bool }
		if errors.As(err, &ue) && ue.Timeout() {
			return Retry, 0, "request timed out"
		}
		return Retry, 0, "network error" // deliberately generic: error text can embed the URL
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	switch {
	case resp.StatusCode == http.StatusAccepted || resp.StatusCode == http.StatusOK:
		return Delivered, 0, ""
	case resp.StatusCode == http.StatusTooManyRequests || resp.StatusCode >= 500 || resp.StatusCode == http.StatusRequestTimeout:
		return Retry, parseRetryAfter(resp.Header.Get("Retry-After")), "HTTP " + strconv.Itoa(resp.StatusCode) + errorCode(body)
	default:
		return Drop, 0, "HTTP " + strconv.Itoa(resp.StatusCode) + errorCode(body)
	}
}

// Run delivers the buffer until ctx is cancelled.
func (e *Exporter) Run(ctx context.Context) {
	attempt := 0
	for ctx.Err() == nil {
		en, ok := e.Buf.Head()
		if !ok {
			select {
			case <-ctx.Done():
				return
			case <-e.Buf.Wake():
			}
			continue
		}
		out, retryAfter, detail := e.Send(ctx, en)
		switch out {
		case Delivered:
			e.Buf.Remove(en.ID)
			e.Sent++
			attempt = 0
		case Drop:
			e.Buf.Remove(en.ID)
			e.Dropped++
			attempt = 0
			e.Log.Error("envelope rejected permanently and dropped", "envelope_id", en.ID, "detail", detail, "hint", hintFor(detail))
		case Retry:
			if ctx.Err() != nil {
				return
			}
			e.Retries++
			wait := Backoff(e.BackoffBase, e.BackoffMax, attempt, e.Rand())
			if retryAfter > wait {
				wait = retryAfter
			}
			attempt++
			e.Log.Warn("delivery failed, will retry the same envelope", "envelope_id", en.ID, "detail", detail,
				"attempt", attempt, "retry_in", wait.Round(time.Millisecond), "buffered", e.Buf.Len())
			if !e.Sleep(ctx, wait) {
				return
			}
		}
	}
}

// Drain makes a best-effort pass at shutdown: each buffered envelope is tried once, in order, until ctx expires.
// It never sleeps between attempts, so a dead server costs one request timeout at most.
func (e *Exporter) Drain(ctx context.Context) {
	for ctx.Err() == nil {
		en, ok := e.Buf.Head()
		if !ok {
			return
		}
		out, _, detail := e.Send(ctx, en)
		switch out {
		case Delivered:
			e.Buf.Remove(en.ID)
			e.Sent++
		case Drop:
			e.Buf.Remove(en.ID)
			e.Dropped++
			e.Log.Error("envelope rejected permanently and dropped", "envelope_id", en.ID, "detail", detail)
		default:
			e.Log.Warn("shutdown: server unreachable, undelivered envelopes are lost", "buffered", e.Buf.Len(), "detail", detail)
			return
		}
	}
}

// DefaultSleep waits d or until ctx is cancelled (false).
func DefaultSleep(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

func DefaultRand() float64 { return rand.Float64() }

func parseRetryAfter(v string) time.Duration {
	if n, err := strconv.Atoi(v); err == nil && n > 0 && n <= 3600 {
		return time.Duration(n) * time.Second
	}
	return 0
}

// errorCode extracts the contract's error.code ("unauthorized", "validation_error", ...) from a response body.
func errorCode(body []byte) string {
	var r struct {
		Error struct {
			Code string `json:"code"`
		} `json:"error"`
	}
	if json.Unmarshal(body, &r) == nil && r.Error.Code != "" {
		return " (" + r.Error.Code + ")"
	}
	return ""
}

func hintFor(detail string) string {
	switch {
	case bytes.Contains([]byte(detail), []byte("401")):
		return "check the bearer token"
	case bytes.Contains([]byte(detail), []byte("403")):
		return "the credential is not allowed to send this agent_id / host_id"
	}
	return ""
}
