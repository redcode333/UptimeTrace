//go:build linux

package collector

import (
	"fmt"
	"os"
	"syscall"
	"time"
)

type linuxCollector struct {
	havePrev            bool
	prevIdle, prevTotal uint64
}

func New() (Collector, error) { return &linuxCollector{}, nil }

func (l *linuxCollector) Collect(now time.Time) ([]Observation, error) {
	var out []Observation

	stat, err := os.ReadFile("/proc/stat")
	if err != nil {
		return nil, fmt.Errorf("cpu: %w", err)
	}
	idle, total, err := parseCPUStat(string(stat))
	if err != nil {
		return nil, fmt.Errorf("cpu: %w", err)
	}
	if l.havePrev && total > l.prevTotal {
		dTotal, dIdle := float64(total-l.prevTotal), float64(idle-l.prevIdle)
		if v, ok := pct(dTotal-dIdle, dTotal); ok {
			out = append(out, gauge(CPU, v, nil, now))
		}
	}
	l.prevIdle, l.prevTotal, l.havePrev = idle, total, true

	mi, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return nil, fmt.Errorf("memory: %w", err)
	}
	mt, ma, err := parseMemInfo(string(mi))
	if err != nil {
		return nil, fmt.Errorf("memory: %w", err)
	}
	v, ok := pct(float64(mt-ma), float64(mt))
	if !ok {
		return nil, fmt.Errorf("memory: MemTotal is zero")
	}
	out = append(out, gauge(Memory, v, nil, now))

	if mounts, err := os.ReadFile("/proc/mounts"); err == nil {
		for _, m := range parseMounts(string(mounts)) {
			var fs syscall.Statfs_t
			if syscall.Statfs(m, &fs) != nil || fs.Blocks == 0 {
				continue
			}
			if v, ok := pct(float64(fs.Blocks-fs.Bfree), float64(fs.Blocks)); ok {
				out = append(out, gauge(Disk, v, map[string]string{"mount": m}, now))
			}
		}
	}

	nd, err := os.ReadFile("/proc/net/dev")
	if err != nil {
		return nil, fmt.Errorf("network: %w", err)
	}
	nics, err := parseNetDev(string(nd))
	if err != nil {
		return nil, fmt.Errorf("network: %w", err)
	}
	for _, n := range nics {
		out = append(out, counter(NetRx, n.rx, n.name, now), counter(NetTx, n.tx, n.name, now))
	}
	return out, nil
}
