//go:build windows

package collector

import (
	"fmt"
	"syscall"
	"time"
	"unsafe"
)

var (
	kernel32                 = syscall.NewLazyDLL("kernel32.dll")
	procGetSystemTimes       = kernel32.NewProc("GetSystemTimes")
	procGlobalMemoryStatusEx = kernel32.NewProc("GlobalMemoryStatusEx")
	procGetLogicalDrives     = kernel32.NewProc("GetLogicalDrives")
	procGetDriveTypeW        = kernel32.NewProc("GetDriveTypeW")
	procGetDiskFreeSpaceExW  = kernel32.NewProc("GetDiskFreeSpaceExW")
	iphlpapi                 = syscall.NewLazyDLL("iphlpapi.dll")
	procGetIfTable2          = iphlpapi.NewProc("GetIfTable2")
	procFreeMibTable         = iphlpapi.NewProc("FreeMibTable")
)

const (
	driveFixed      = 3
	ifOperStatusUp  = 1
	hardwareIfFlag  = 0x01 // MIB_IF_ROW2.InterfaceAndOperStatusFlags: HardwareInterface
	mibIfTableSkip  = 8    // MIB_IF_TABLE2 { ULONG NumEntries; <pad to 8>; MIB_IF_ROW2 Table[] }
	expectedRowSize = 1352 // sizeof(MIB_IF_ROW2) on 64-bit Windows; checked by a test
)

type filetime struct{ Low, High uint32 }

func (f filetime) u64() uint64 { return uint64(f.High)<<32 | uint64(f.Low) }

type memoryStatusEx struct {
	Length               uint32
	MemoryLoad           uint32
	TotalPhys            uint64
	AvailPhys            uint64
	TotalPageFile        uint64
	AvailPageFile        uint64
	TotalVirtual         uint64
	AvailVirtual         uint64
	AvailExtendedVirtual uint64
}

// mibIfRow2 mirrors MIB_IF_ROW2 (netioapi.h) field for field; natural Go alignment reproduces the C layout on 64-bit Windows.
type mibIfRow2 struct {
	InterfaceLuid               uint64
	InterfaceIndex              uint32
	InterfaceGuid               [16]byte
	Alias                       [257]uint16
	Description                 [257]uint16
	PhysicalAddressLength       uint32
	PhysicalAddress             [32]byte
	PermanentPhysicalAddress    [32]byte
	Mtu                         uint32
	Type                        uint32
	TunnelType                  uint32
	MediaType                   uint32
	PhysicalMediumType          uint32
	AccessType                  uint32
	DirectionType               uint32
	InterfaceAndOperStatusFlags uint8
	OperStatus                  uint32
	AdminStatus                 uint32
	MediaConnectState           uint32
	NetworkGuid                 [16]byte
	ConnectionType              uint32
	TransmitLinkSpeed           uint64
	ReceiveLinkSpeed            uint64
	InOctets                    uint64
	InUcastPkts                 uint64
	InNUcastPkts                uint64
	InDiscards                  uint64
	InErrors                    uint64
	InUnknownProtos             uint64
	InUcastOctets               uint64
	InMulticastOctets           uint64
	InBroadcastOctets           uint64
	OutOctets                   uint64
	OutUcastPkts                uint64
	OutNUcastPkts               uint64
	OutDiscards                 uint64
	OutErrors                   uint64
	OutUcastOctets              uint64
	OutMulticastOctets          uint64
	OutBroadcastOctets          uint64
	OutQLen                     uint64
}

type windowsCollector struct {
	havePrev            bool
	prevIdle, prevTotal uint64
}

func New() (Collector, error) { return &windowsCollector{}, nil }

func (w *windowsCollector) Collect(now time.Time) ([]Observation, error) {
	var out []Observation
	if v, ok, err := w.cpu(); err != nil {
		return nil, fmt.Errorf("cpu: %w", err)
	} else if ok {
		out = append(out, gauge(CPU, v, nil, now))
	}
	if v, err := memoryUtilisation(); err != nil {
		return nil, fmt.Errorf("memory: %w", err)
	} else {
		out = append(out, gauge(Memory, v, nil, now))
	}
	disks, err := diskUtilisation()
	if err != nil {
		return nil, fmt.Errorf("disk: %w", err)
	}
	for mount, v := range disks {
		out = append(out, gauge(Disk, v, map[string]string{"mount": mount}, now))
	}
	nics, err := networkCounters()
	if err != nil {
		return nil, fmt.Errorf("network: %w", err)
	}
	for _, n := range nics {
		out = append(out, counter(NetRx, n.rx, n.name, now), counter(NetTx, n.tx, n.name, now))
	}
	return out, nil
}

// cpu returns whole-host busy % over the interval since the previous call (kernel time includes idle time).
func (w *windowsCollector) cpu() (float64, bool, error) {
	var idle, kernel, user filetime
	r, _, e := procGetSystemTimes.Call(uintptr(unsafe.Pointer(&idle)), uintptr(unsafe.Pointer(&kernel)), uintptr(unsafe.Pointer(&user)))
	if r == 0 {
		return 0, false, e
	}
	total := kernel.u64() + user.u64()
	i := idle.u64()
	defer func() { w.prevIdle, w.prevTotal, w.havePrev = i, total, true }()
	if !w.havePrev || total <= w.prevTotal {
		return 0, false, nil // first sample, or no time elapsed: no value rather than an invented one
	}
	dTotal, dIdle := float64(total-w.prevTotal), float64(i-w.prevIdle)
	v, ok := pct(dTotal-dIdle, dTotal)
	return v, ok, nil
}

func memoryUtilisation() (float64, error) {
	m := memoryStatusEx{Length: uint32(unsafe.Sizeof(memoryStatusEx{}))}
	r, _, e := procGlobalMemoryStatusEx.Call(uintptr(unsafe.Pointer(&m)))
	if r == 0 {
		return 0, e
	}
	// AvailPhys includes the standby (reclaimable) cache, matching the contract's "excluding reclaimable cache".
	v, ok := pct(float64(m.TotalPhys-m.AvailPhys), float64(m.TotalPhys))
	if !ok {
		return 0, fmt.Errorf("total physical memory reported as zero")
	}
	return v, nil
}

func diskUtilisation() (map[string]float64, error) {
	mask, _, _ := procGetLogicalDrives.Call()
	out := map[string]float64{}
	for i := 0; i < 26; i++ {
		if mask&(1<<uint(i)) == 0 {
			continue
		}
		root, _ := syscall.UTF16PtrFromString(fmt.Sprintf("%c:\\", 'A'+i))
		if t, _, _ := procGetDriveTypeW.Call(uintptr(unsafe.Pointer(root))); t != driveFixed {
			continue
		}
		var avail, total, free uint64
		r, _, _ := procGetDiskFreeSpaceExW.Call(uintptr(unsafe.Pointer(root)), uintptr(unsafe.Pointer(&avail)),
			uintptr(unsafe.Pointer(&total)), uintptr(unsafe.Pointer(&free)))
		if r == 0 {
			continue // e.g. a locked or unreadable volume: skip it rather than fail the whole sample
		}
		if v, ok := pct(float64(total-free), float64(total)); ok {
			out[fmt.Sprintf("%c:", 'A'+i)] = v
		}
	}
	return out, nil
}

type nic struct {
	name   string
	rx, tx uint64
}

// networkCounters returns 64-bit cumulative octet counters of the operational hardware adapters (GetIfTable2).
func networkCounters() ([]nic, error) {
	var table unsafe.Pointer
	if r, _, _ := procGetIfTable2.Call(uintptr(unsafe.Pointer(&table))); r != 0 || table == nil {
		return nil, fmt.Errorf("GetIfTable2 failed (status %d)", r)
	}
	defer procFreeMibTable.Call(uintptr(table))
	n := int(*(*uint32)(table))
	size := unsafe.Sizeof(mibIfRow2{})
	var out []nic
	for i := 0; i < n; i++ {
		row := (*mibIfRow2)(unsafe.Add(table, mibIfTableSkip+uintptr(i)*size))
		if row.OperStatus != ifOperStatusUp || row.InterfaceAndOperStatusFlags&hardwareIfFlag == 0 {
			continue
		}
		name := syscall.UTF16ToString(row.Alias[:])
		if name == "" {
			continue
		}
		out = append(out, nic{name: name, rx: row.InOctets, tx: row.OutOctets})
	}
	return out, nil
}
