package collector

import "testing"

const procStat = `cpu  4705 150 1120 16250 520 30 45 10 0 0
cpu0 2352 75 560 8125 260 15 22 5 0 0
intr 123456
`

const memInfo = `MemTotal:       16384000 kB
MemFree:         1024000 kB
MemAvailable:    8192000 kB
Buffers:          204800 kB
`

const netDev = `Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 1000000   5000    0    0    0     0          0         0  1000000    5000    0    0    0     0       0          0
  eth0: 123456789  90000    0    0    0     0          0         0 98765432   70000    0    0    0     0       0          0
 wlan0: 5000000000 8000    0    0    0     0          0         0 4000000000  6000    0    0    0     0       0          0
`

const mounts = `sysfs /sys sysfs rw 0 0
/dev/nvme0n1p2 / ext4 rw,relatime 0 0
tmpfs /run tmpfs rw 0 0
/dev/nvme0n1p1 /boot/efi vfat rw 0 0
/dev/sdb1 /mnt/my\040data xfs rw 0 0
/dev/nvme0n1p2 / ext4 rw 0 0
overlay /var/lib/docker/overlay2/x overlay rw 0 0
//server/share /mnt/share cifs rw 0 0
`

func TestParseCPUStat(t *testing.T) {
	idle, total, err := parseCPUStat(procStat)
	if err != nil {
		t.Fatal(err)
	}
	if idle != 16250+520 || total != 4705+150+1120+16250+520+30+45+10 {
		t.Fatalf("idle %d total %d", idle, total)
	}
	if _, _, err := parseCPUStat("cpu0 1 2 3 4 5\n"); err == nil {
		t.Fatal("the aggregate line is required")
	}
	if _, _, err := parseCPUStat("cpu  1 2 x 4 5\n"); err == nil {
		t.Fatal("garbage must be an error, not a zero")
	}
}

func TestParseMemInfoUsesMemAvailableNotMemFree(t *testing.T) {
	total, avail, err := parseMemInfo(memInfo)
	if err != nil || total != 16384000*1024 || avail != 8192000*1024 {
		t.Fatalf("%d %d %v", total, avail, err)
	}
	if v, _ := pct(float64(total-avail), float64(total)); v != 50 {
		t.Fatalf("used%% = %v", v)
	}
	if _, _, err := parseMemInfo("MemTotal: 100 kB\n"); err == nil {
		t.Fatal("missing MemAvailable must be an error")
	}
}

func TestParseNetDevSkipsLoopbackAndReadsRxAndTx(t *testing.T) {
	n, err := parseNetDev(netDev)
	if err != nil || len(n) != 2 {
		t.Fatalf("%v %v", n, err)
	}
	if n[0] != (netStat{"eth0", 123456789, 98765432}) || n[1] != (netStat{"wlan0", 5000000000, 4000000000}) {
		t.Fatalf("%+v", n)
	}
}

func TestParseMountsKeepsOnlyRealBlockFilesystemsOnce(t *testing.T) {
	got := parseMounts(mounts)
	want := []string{"/", "/boot/efi", "/mnt/my data"}
	if len(got) != len(want) {
		t.Fatalf("%v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("%v", got)
		}
	}
}

func TestPctIsClampedAndRefusesAZeroTotal(t *testing.T) {
	for _, c := range []struct {
		part, total, want float64
		ok                bool
	}{{50, 100, 50, true}, {0, 100, 0, true}, {150, 100, 100, true}, {-5, 100, 0, true}, {1, 0, 0, false}, {1, -3, 0, false}} {
		if v, ok := pct(c.part, c.total); v != c.want || ok != c.ok {
			t.Errorf("pct(%v,%v) = %v,%v", c.part, c.total, v, ok)
		}
	}
}
