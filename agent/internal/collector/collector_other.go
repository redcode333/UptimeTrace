//go:build !windows && !linux

package collector

// New reports that this platform has no collector (the agent supports Windows and Linux).
func New() (Collector, error) { return nil, ErrUnsupported }
