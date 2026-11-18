// Package buffer is the bounded FIFO of built envelopes waiting for delivery.
package buffer

import (
	"sync"
	"time"
)

// Entry is an envelope exactly as it will be sent. It is never rebuilt, so every retry carries the same envelope_id.
type Entry struct {
	ID           string
	Body         []byte
	Observations int
	Created      time.Time
}

type Buffer struct {
	mu      sync.Mutex
	q       []Entry
	max     int
	dropped uint64
	wake    chan struct{}
}

func New(max int) *Buffer {
	if max < 1 {
		max = 1
	}
	return &Buffer{max: max, wake: make(chan struct{}, 1)}
}

// Push appends entries. When the buffer is full the OLDEST envelopes are dropped first (fresh data matters most) and
// counted; it returns how many were dropped by this call.
func (b *Buffer) Push(entries ...Entry) (dropped int) {
	b.mu.Lock()
	b.q = append(b.q, entries...)
	if over := len(b.q) - b.max; over > 0 {
		b.q = append([]Entry(nil), b.q[over:]...)
		b.dropped += uint64(over)
		dropped = over
	}
	b.mu.Unlock()
	select {
	case b.wake <- struct{}{}:
	default:
	}
	return dropped
}

// Head returns the oldest entry without removing it.
func (b *Buffer) Head() (Entry, bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if len(b.q) == 0 {
		return Entry{}, false
	}
	return b.q[0], true
}

// Remove deletes the entry with the given id (no-op if it was already dropped to make room).
func (b *Buffer) Remove(id string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	for i := range b.q {
		if b.q[i].ID == id {
			b.q = append(b.q[:i], b.q[i+1:]...)
			return
		}
	}
}

func (b *Buffer) Len() int {
	b.mu.Lock()
	defer b.mu.Unlock()
	return len(b.q)
}

func (b *Buffer) Dropped() uint64 {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.dropped
}

// Wake signals that entries may be waiting.
func (b *Buffer) Wake() <-chan struct{} { return b.wake }
