package collector

// Pure parsers for Linux /proc text. They have no build tag so they are unit-tested on every platform.

import (
	"bufio"
	"fmt"
	"strconv"
	"strings"
)

// parseCPUStat reads the aggregate "cpu " line of /proc/stat and returns (idle, total) jiffies.
// idle = idle + iowait; total = user+nice+system+idle+iowait+irq+softirq+steal (guest time is already inside user).
func parseCPUStat(text string) (idle, total uint64, err error) {
	sc := bufio.NewScanner(strings.NewReader(text))
	for sc.Scan() {
		f := strings.Fields(sc.Text())
		if len(f) < 5 || f[0] != "cpu" {
			continue
		}
		var v [8]uint64
		for i := 0; i < 8 && i+1 < len(f); i++ {
			n, e := strconv.ParseUint(f[i+1], 10, 64)
			if e != nil {
				return 0, 0, fmt.Errorf("bad /proc/stat field %q", f[i+1])
			}
			v[i] = n
		}
		for _, n := range v {
			total += n
		}
		return v[3] + v[4], total, nil
	}
	return 0, 0, fmt.Errorf("no aggregate cpu line in /proc/stat")
}

// parseMemInfo returns MemTotal and MemAvailable in bytes (MemAvailable excludes reclaimable cache, as the contract asks).
func parseMemInfo(text string) (total, available uint64, err error) {
	var haveT, haveA bool
	sc := bufio.NewScanner(strings.NewReader(text))
	for sc.Scan() {
		f := strings.Fields(sc.Text())
		if len(f) < 2 {
			continue
		}
		n, e := strconv.ParseUint(f[1], 10, 64)
		if e != nil {
			continue
		}
		switch f[0] {
		case "MemTotal:":
			total, haveT = n*1024, true
		case "MemAvailable:":
			available, haveA = n*1024, true
		}
	}
	if !haveT || !haveA {
		return 0, 0, fmt.Errorf("MemTotal/MemAvailable not found in /proc/meminfo")
	}
	return total, available, nil
}

type netStat struct {
	name   string
	rx, tx uint64
}

// parseNetDev reads /proc/net/dev; the loopback interface is skipped.
func parseNetDev(text string) ([]netStat, error) {
	var out []netStat
	sc := bufio.NewScanner(strings.NewReader(text))
	for sc.Scan() {
		line := sc.Text()
		i := strings.Index(line, ":")
		if i < 0 {
			continue // the two header lines
		}
		name := strings.TrimSpace(line[:i])
		f := strings.Fields(line[i+1:])
		if name == "" || name == "lo" || len(f) < 9 {
			continue
		}
		rx, e1 := strconv.ParseUint(f[0], 10, 64)
		tx, e2 := strconv.ParseUint(f[8], 10, 64)
		if e1 != nil || e2 != nil {
			return nil, fmt.Errorf("bad counters for interface %q", name)
		}
		out = append(out, netStat{name, rx, tx})
	}
	return out, nil
}

var realFS = map[string]bool{"ext2": true, "ext3": true, "ext4": true, "xfs": true, "btrfs": true, "zfs": true, "f2fs": true, "vfat": true, "exfat": true, "ntfs": true}

// parseMounts returns the mount points of real, block-device-backed filesystems from /proc/mounts (first mount wins).
func parseMounts(text string) []string {
	seen := map[string]bool{}
	var out []string
	sc := bufio.NewScanner(strings.NewReader(text))
	for sc.Scan() {
		f := strings.Fields(sc.Text())
		if len(f) < 3 || !strings.HasPrefix(f[0], "/") || !realFS[f[2]] || seen[f[1]] {
			continue
		}
		seen[f[1]] = true
		out = append(out, strings.ReplaceAll(f[1], `\040`, " "))
	}
	return out
}
