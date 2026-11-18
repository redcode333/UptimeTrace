// Package processor turns observations into contract-valid telemetry envelopes (schema_version "1.0").
package processor

import (
	"crypto/rand"
	"encoding/json"
	"fmt"
	"math"
	"time"

	"uptimetrace/agent/internal/buffer"
	"uptimetrace/agent/internal/collector"
)

const SchemaVersion = "1.0"

// timeFormat is the contract's timestamp form: UTC, 'Z', six fractional digits (the contract allows 1-6).
const timeFormat = "2006-01-02T15:04:05.000000Z"

type metric struct {
	Name      string            `json:"name"`
	Kind      string            `json:"kind"`
	Value     any               `json:"value"`
	Unit      string            `json:"unit"`
	Timestamp string            `json:"timestamp"`
	Labels    map[string]string `json:"labels,omitempty"`
}

type envelope struct {
	SchemaVersion string   `json:"schema_version"`
	EnvelopeID    string   `json:"envelope_id"`
	AgentID       string   `json:"agent_id"`
	HostID        string   `json:"host_id"`
	Hostname      string   `json:"hostname"`
	Timestamp     string   `json:"timestamp"`
	Service       string   `json:"service,omitempty"`
	Metrics       []metric `json:"metrics"`
}

type Builder struct {
	AgentID, HostID, Hostname, Service string
	BatchMax                           int
	NewID                              func() string // injectable for tests
}

// Build cuts obs into envelopes of at most BatchMax observations. Each envelope gets its own id, created ONCE here: a retry
// resends exactly these bytes (the contract: same content, same envelope_id). `now` is the envelope's send/creation time.
func (b Builder) Build(obs []collector.Observation, now time.Time) []buffer.Entry {
	newID := b.NewID
	if newID == nil {
		newID = NewUUID
	}
	max := b.BatchMax
	if max < 1 || max > 500 {
		max = 500
	}
	var out []buffer.Entry
	for start := 0; start < len(obs); start += max {
		end := start + max
		if end > len(obs) {
			end = len(obs)
		}
		env := envelope{SchemaVersion: SchemaVersion, EnvelopeID: newID(), AgentID: b.AgentID, HostID: b.HostID,
			Hostname: b.Hostname, Timestamp: now.UTC().Format(timeFormat), Service: b.Service}
		for _, o := range obs[start:end] {
			env.Metrics = append(env.Metrics, toMetric(o))
		}
		body, err := json.Marshal(env)
		if err != nil { // cannot happen for these types; never send a half-built envelope
			continue
		}
		out = append(out, buffer.Entry{ID: env.EnvelopeID, Body: body, Observations: end - start, Created: now})
	}
	return out
}

func toMetric(o collector.Observation) metric {
	m := metric{Name: o.Name, Kind: o.Kind, Unit: o.Unit, Timestamp: o.At.UTC().Format(timeFormat), Labels: o.Labels}
	if o.Kind == collector.Counter {
		m.Value = o.Count // exact unsigned integer, never a float
	} else {
		m.Value = Percent(o.Value)
	}
	return m
}

// Percent clamps to the contract's 0..100 range and rounds to two decimals; NaN/Inf become 0.
func Percent(v float64) float64 {
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return 0
	}
	return math.Round(math.Min(100, math.Max(0, v))*100) / 100
}

// NewUUID returns a random (version 4) UUID in the lowercase hyphenated form the contract requires.
func NewUUID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic("crypto/rand unavailable: " + err.Error())
	}
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}
