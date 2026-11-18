// Package collector gathers the host metrics the telemetry contract defines, using only the standard library.
//
// Metric names, kinds, units and labels are the contract's "v1 well-known metrics" and must not change:
//
//	system.cpu.utilization      gauge   percent
//	system.memory.utilization   gauge   percent
//	system.disk.utilization     gauge   percent   label mount
//	system.network.rx_bytes     counter bytes     label interface   (cumulative; rates are derived later, server side)
//	system.network.tx_bytes     counter bytes     label interface
package collector

import (
	"errors"
	"time"
)

const (
	CPU     = "system.cpu.utilization"
	Memory  = "system.memory.utilization"
	Disk    = "system.disk.utilization"
	NetRx   = "system.network.rx_bytes"
	NetTx   = "system.network.tx_bytes"
	Gauge   = "gauge"
	Counter = "counter"
	Percent = "percent"
	Bytes   = "bytes"
)

// Observation is one sampled value. Gauges use Value, counters use Count (cumulative, exact integer).
type Observation struct {
	Name   string
	Kind   string
	Unit   string
	Value  float64
	Count  uint64
	Labels map[string]string
	At     time.Time
}

// Collector samples the host. The first call has no CPU delta, so it returns no CPU observation (never a made-up value).
type Collector interface {
	Collect(now time.Time) ([]Observation, error)
}

var ErrUnsupported = errors.New("this operating system is not supported by the agent")

func gauge(name string, v float64, labels map[string]string, at time.Time) Observation {
	return Observation{Name: name, Kind: Gauge, Unit: Percent, Value: v, Labels: labels, At: at}
}

func counter(name string, n uint64, iface string, at time.Time) Observation {
	return Observation{Name: name, Kind: Counter, Unit: Bytes, Count: n, Labels: map[string]string{"interface": iface}, At: at}
}

// pct returns 100*part/total, clamped to [0,100]; ok is false when total is zero.
func pct(part, total float64) (float64, bool) {
	if total <= 0 {
		return 0, false
	}
	v := 100 * part / total
	if v < 0 {
		v = 0
	}
	if v > 100 {
		v = 100
	}
	return v, true
}
