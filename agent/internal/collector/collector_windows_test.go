//go:build windows

package collector

import (
	"runtime"
	"testing"
	"time"
	"unsafe"
)

// The hand-written MIB_IF_ROW2 mirror only works if it is exactly the size Windows uses; a wrong layout would silently
// read garbage counters, so this guard fails loudly instead.
func TestIfRowLayoutMatchesTheWindowsStructure(t *testing.T) {
	if runtime.GOARCH != "amd64" && runtime.GOARCH != "arm64" {
		t.Skip("layout verified for 64-bit Windows only")
	}
	if got := unsafe.Sizeof(mibIfRow2{}); got != expectedRowSize {
		t.Fatalf("sizeof(mibIfRow2) = %d, Windows uses %d", got, expectedRowSize)
	}
	var r mibIfRow2
	if off := unsafe.Offsetof(r.InOctets); off != 1208 {
		t.Fatalf("InOctets offset %d, want 1208", off)
	}
	if off := unsafe.Offsetof(r.OutOctets); off != 1280 {
		t.Fatalf("OutOctets offset %d, want 1280", off)
	}
}

func TestLiveWindowsCollectionIsContractShapedAndSane(t *testing.T) {
	c, err := New()
	if err != nil {
		t.Fatal(err)
	}
	first, err := c.Collect(time.Now())
	if err != nil {
		t.Fatal(err)
	}
	for _, o := range first {
		if o.Name == CPU {
			t.Fatal("the first sample has no CPU delta: it must not invent a value")
		}
	}
	time.Sleep(1200 * time.Millisecond)
	second, err := c.Collect(time.Now())
	if err != nil {
		t.Fatal(err)
	}

	var cpu, mem, disks, rx, tx int
	for _, o := range second {
		switch o.Name {
		case CPU:
			cpu++
			expect(t, o, Gauge, Percent, "")
			expectPct(t, o)
		case Memory:
			mem++
			expect(t, o, Gauge, Percent, "")
			expectPct(t, o)
			if o.Value <= 0 {
				t.Errorf("memory utilisation %v", o.Value)
			}
		case Disk:
			disks++
			expect(t, o, Gauge, Percent, "mount")
			expectPct(t, o)
			if m := o.Labels["mount"]; len(m) != 2 || m[1] != ':' {
				t.Errorf("mount label %q", m)
			}
		case NetRx:
			rx++
			expect(t, o, Counter, Bytes, "interface")
		case NetTx:
			tx++
			expect(t, o, Counter, Bytes, "interface")
		default:
			t.Errorf("unexpected metric %q (custom names are not allowed)", o.Name)
		}
		if o.At.IsZero() {
			t.Errorf("%s has no sample time", o.Name)
		}
	}
	if cpu != 1 || mem != 1 || disks < 1 {
		t.Fatalf("cpu=%d mem=%d disks=%d", cpu, mem, disks)
	}
	if rx != tx {
		t.Fatalf("every interface reports both directions: rx=%d tx=%d", rx, tx)
	}
}

func TestNetworkCountersAreCumulativeAndMonotonic(t *testing.T) {
	a, err := networkCounters()
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(300 * time.Millisecond)
	b, err := networkCounters()
	if err != nil {
		t.Fatal(err)
	}
	prev := map[string]nic{}
	for _, n := range a {
		prev[n.name] = n
	}
	for _, n := range b {
		if p, ok := prev[n.name]; ok && (n.rx < p.rx || n.tx < p.tx) {
			t.Errorf("%s went backwards without a reset: %+v -> %+v", n.name, p, n)
		}
	}
}

func expect(t *testing.T, o Observation, kind, unit, label string) {
	t.Helper()
	if o.Kind != kind || o.Unit != unit {
		t.Errorf("%s: kind/unit %s/%s want %s/%s", o.Name, o.Kind, o.Unit, kind, unit)
	}
	if label == "" && len(o.Labels) != 0 {
		t.Errorf("%s must have no labels, has %v", o.Name, o.Labels)
	}
	if label != "" && (len(o.Labels) != 1 || o.Labels[label] == "") {
		t.Errorf("%s needs exactly the %q label, has %v", o.Name, label, o.Labels)
	}
}

func expectPct(t *testing.T, o Observation) {
	t.Helper()
	if o.Value < 0 || o.Value > 100 {
		t.Errorf("%s = %v is outside 0..100", o.Name, o.Value)
	}
}
