package buffer

import (
	"fmt"
	"testing"
)

func e(id string) Entry { return Entry{ID: id, Body: []byte(id), Observations: 1} }

func TestFIFOHeadDoesNotRemove(t *testing.T) {
	b := New(10)
	b.Push(e("a"), e("b"), e("c"))
	if h, ok := b.Head(); !ok || h.ID != "a" || b.Len() != 3 {
		t.Fatalf("head %v len %d", h.ID, b.Len())
	}
	b.Remove("a")
	if h, _ := b.Head(); h.ID != "b" || b.Len() != 2 {
		t.Fatalf("after remove: %s", h.ID)
	}
	if _, ok := New(3).Head(); ok {
		t.Fatal("empty buffer has no head")
	}
}

func TestBoundedDropsTheOldestFirstAndCountsThem(t *testing.T) {
	b := New(3)
	for i := 1; i <= 5; i++ {
		b.Push(e(fmt.Sprint(i)))
	}
	if b.Len() != 3 || b.Dropped() != 2 {
		t.Fatalf("len %d dropped %d", b.Len(), b.Dropped())
	}
	if h, _ := b.Head(); h.ID != "3" {
		t.Fatalf("oldest two should be gone, head is %s", h.ID)
	}
	if d := b.Push(e("6"), e("7"), e("8")); d != 3 || b.Len() != 3 {
		t.Fatalf("a push larger than the free space returns its drop count: %d", d)
	}
	if h, _ := b.Head(); h.ID != "6" {
		t.Fatalf("head %s", h.ID)
	}
}

func TestRemoveByIdIsANoOpWhenTheEntryWasAlreadyDropped(t *testing.T) {
	b := New(2)
	b.Push(e("a"), e("b"))
	b.Push(e("c")) // "a" is dropped while it might still be in flight
	b.Remove("a")
	if b.Len() != 2 {
		t.Fatalf("len %d", b.Len())
	}
	if h, _ := b.Head(); h.ID != "b" {
		t.Fatalf("head %s", h.ID)
	}
}

func TestPushWakesTheConsumerWithoutBlocking(t *testing.T) {
	b := New(5)
	b.Push(e("a"))
	b.Push(e("b")) // second signal must not block when the first has not been consumed
	select {
	case <-b.Wake():
	default:
		t.Fatal("expected a wake-up")
	}
}
