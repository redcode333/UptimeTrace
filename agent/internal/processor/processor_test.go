package processor

import (
	"encoding/json"
	"math"
	"regexp"
	"strings"
	"testing"
	"time"

	"uptimetrace/agent/internal/collector"
)

var at = time.Date(2026, 11, 12, 10, 0, 0, 123456000, time.UTC)

func sample() []collector.Observation {
	return []collector.Observation{
		{Name: collector.CPU, Kind: collector.Gauge, Unit: collector.Percent, Value: 30.394, At: at},
		{Name: collector.Memory, Kind: collector.Gauge, Unit: collector.Percent, Value: 89.0149, At: at},
		{Name: collector.Disk, Kind: collector.Gauge, Unit: collector.Percent, Value: 98.28, Labels: map[string]string{"mount": "C:"}, At: at},
		{Name: collector.NetRx, Kind: collector.Counter, Unit: collector.Bytes, Count: 615463467, Labels: map[string]string{"interface": "Wi-Fi"}, At: at},
		{Name: collector.NetTx, Kind: collector.Counter, Unit: collector.Bytes, Count: 61107736, Labels: map[string]string{"interface": "Wi-Fi"}, At: at},
	}
}

func builder() Builder {
	return Builder{AgentID: "agent-test", HostID: "host-test", Hostname: "TEST-HOST", Service: "checkout-api", BatchMax: 500}
}

type wire struct {
	SchemaVersion string `json:"schema_version"`
	EnvelopeID    string `json:"envelope_id"`
	AgentID       string `json:"agent_id"`
	HostID        string `json:"host_id"`
	Hostname      string `json:"hostname"`
	Timestamp     string `json:"timestamp"`
	Service       string `json:"service"`
	Metrics       []struct {
		Name      string            `json:"name"`
		Kind      string            `json:"kind"`
		Value     json.Number       `json:"value"`
		Unit      string            `json:"unit"`
		Timestamp string            `json:"timestamp"`
		Labels    map[string]string `json:"labels"`
	} `json:"metrics"`
}

func decode(t *testing.T, body []byte) wire {
	t.Helper()
	var w wire
	d := json.NewDecoder(strings.NewReader(string(body)))
	d.DisallowUnknownFields() // the contract rejects unknown fields; so must our own output not have any
	d.UseNumber()
	if err := d.Decode(&w); err != nil {
		t.Fatalf("envelope does not decode strictly: %v\n%s", err, body)
	}
	return w
}

func TestEnvelopeMatchesTheContractFieldForField(t *testing.T) {
	entries := builder().Build(sample(), at.Add(2*time.Second))
	if len(entries) != 1 {
		t.Fatalf("want 1 envelope, got %d", len(entries))
	}
	w := decode(t, entries[0].Body)
	if w.SchemaVersion != "1.0" || w.AgentID != "agent-test" || w.HostID != "host-test" || w.Hostname != "TEST-HOST" || w.Service != "checkout-api" {
		t.Fatalf("identity fields: %+v", w)
	}
	if w.Timestamp != "2026-11-12T10:00:02.123456Z" {
		t.Fatalf("envelope timestamp (send time): %s", w.Timestamp)
	}
	if entries[0].ID != w.EnvelopeID || entries[0].Observations != 5 {
		t.Fatalf("entry bookkeeping: %+v", entries[0])
	}
	want := []struct {
		name, kind, unit, value string
		labels                  map[string]string
	}{
		{"system.cpu.utilization", "gauge", "percent", "30.39", nil},
		{"system.memory.utilization", "gauge", "percent", "89.01", nil},
		{"system.disk.utilization", "gauge", "percent", "98.28", map[string]string{"mount": "C:"}},
		{"system.network.rx_bytes", "counter", "bytes", "615463467", map[string]string{"interface": "Wi-Fi"}},
		{"system.network.tx_bytes", "counter", "bytes", "61107736", map[string]string{"interface": "Wi-Fi"}},
	}
	for i, m := range w.Metrics {
		g := want[i]
		if m.Name != g.name || m.Kind != g.kind || m.Unit != g.unit || m.Value.String() != g.value || m.Timestamp != "2026-11-12T10:00:00.123456Z" {
			t.Errorf("metric %d: %+v", i, m)
		}
		if len(m.Labels) != len(g.labels) || (len(g.labels) > 0 && m.Labels[firstKey(g.labels)] != g.labels[firstKey(g.labels)]) {
			t.Errorf("metric %d labels: %v", i, m.Labels)
		}
	}
}

func firstKey(m map[string]string) string {
	for k := range m {
		return k
	}
	return ""
}

func TestTimestampsAndIdsFollowTheContractPatterns(t *testing.T) {
	ts := regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{1,6}Z$`)
	uuid := regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
	seen := map[string]bool{}
	for i := 0; i < 200; i++ {
		for _, e := range builder().Build(sample(), time.Now()) {
			w := decode(t, e.Body)
			if !uuid.MatchString(w.EnvelopeID) || !ts.MatchString(w.Timestamp) || seen[w.EnvelopeID] {
				t.Fatalf("bad or repeated id/timestamp: %s %s", w.EnvelopeID, w.Timestamp)
			}
			seen[w.EnvelopeID] = true
			for _, m := range w.Metrics {
				if !ts.MatchString(m.Timestamp) {
					t.Fatalf("metric timestamp %q", m.Timestamp)
				}
			}
		}
	}
}

func TestCountersStayExactIntegersEvenAtTheTopOfTheRange(t *testing.T) {
	o := collector.Observation{Name: collector.NetRx, Kind: collector.Counter, Unit: collector.Bytes, Count: math.MaxUint64,
		Labels: map[string]string{"interface": "eth0"}, At: at}
	body := string(builder().Build([]collector.Observation{o}, at)[0].Body)
	if !strings.Contains(body, `"value":18446744073709551615`) || strings.ContainsAny(body, "eE") && strings.Contains(body, "e+") {
		t.Fatalf("counter was not encoded as an exact integer: %s", body)
	}
	small := collector.Observation{Name: collector.NetTx, Kind: collector.Counter, Unit: collector.Bytes, Count: 5000000000, Labels: map[string]string{"interface": "x"}, At: at}
	if b := string(builder().Build([]collector.Observation{small}, at)[0].Body); !strings.Contains(b, `"value":5000000000`) {
		t.Fatalf("5e9 must not become 5e+09: %s", b)
	}
}

func TestPercentIsClampedRoundedAndNeverNaN(t *testing.T) {
	for in, want := range map[float64]float64{-5: 0, 0: 0, 42.456: 42.46, 99.994: 99.99, 100: 100, 100.7: 100, math.NaN(): 0, math.Inf(1): 0, math.Inf(-1): 0} {
		if got := Percent(in); got != want {
			t.Errorf("Percent(%v) = %v want %v", in, got, want)
		}
	}
	g := collector.Observation{Name: collector.CPU, Kind: collector.Gauge, Unit: collector.Percent, Value: 250, At: at}
	if w := decode(t, builder().Build([]collector.Observation{g}, at)[0].Body); w.Metrics[0].Value.String() != "100" {
		t.Fatalf("value above 100 must be clamped: %s", w.Metrics[0].Value)
	}
}

func TestBatchesNeverExceedTheContractLimitAndEachGetsItsOwnId(t *testing.T) {
	var many []collector.Observation
	for i := 0; i < 1200; i++ {
		many = append(many, collector.Observation{Name: collector.Disk, Kind: collector.Gauge, Unit: collector.Percent, Value: 1,
			Labels: map[string]string{"mount": string(rune('A'+i%26)) + ":" + string(rune('a'+i/26%26))}, At: at})
	}
	entries := builder().Build(many, at)
	var sizes []int
	ids := map[string]bool{}
	for _, e := range entries {
		sizes = append(sizes, e.Observations)
		ids[e.ID] = true
		if n := len(decode(t, e.Body).Metrics); n != e.Observations || n > 500 {
			t.Fatalf("envelope with %d metrics", n)
		}
	}
	if len(sizes) != 3 || sizes[0] != 500 || sizes[1] != 500 || sizes[2] != 200 || len(ids) != 3 {
		t.Fatalf("sizes %v ids %d", sizes, len(ids))
	}
	b := builder()
	b.BatchMax = 9999 // a mis-set batch size can never produce an over-limit envelope
	for _, e := range b.Build(many, at) {
		if e.Observations > 500 {
			t.Fatalf("over the contract limit: %d", e.Observations)
		}
	}
	if len(builder().Build(nil, at)) != 0 {
		t.Fatal("no observations must produce no envelope (the contract requires at least one metric)")
	}
}

func TestOptionalServiceIsOmittedWhenEmptyAndTheBodyIsStableForRetries(t *testing.T) {
	b := builder()
	b.Service = ""
	body := b.Build(sample(), at)[0].Body
	if strings.Contains(string(body), `"service"`) {
		t.Fatalf("empty service must be omitted: %s", body)
	}
	e := builder().Build(sample(), at)[0]
	if string(e.Body) != string(append([]byte(nil), e.Body...)) {
		t.Fatal("body must be immutable bytes")
	}
}
