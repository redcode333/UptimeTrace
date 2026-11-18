// Command agent collects host metrics and reports them to the UptimeTrace API (telemetry contract v1).
//
//	set UPTIMETRACE_TOKEN=utk_...        (or -token-file / UPTIMETRACE_TOKEN_FILE; never a flag)
//	agent -url http://127.0.0.1:8000 -host-id my-host -agent-id agent-my-host -interval 15s
//	agent -dry-run                       (print the envelope(s) that would be sent; contacts nothing)
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"runtime"
	"syscall"
	"time"

	"uptimetrace/agent/internal/buffer"
	"uptimetrace/agent/internal/collector"
	"uptimetrace/agent/internal/config"
	"uptimetrace/agent/internal/exporter"
	"uptimetrace/agent/internal/processor"
)

const version = "0.1.0"

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	code := run(ctx, os.Args[1:], config.OSEnv(), os.Stdout, os.Stderr)
	stop()
	os.Exit(code)
}

// run is main without process globals: ctx cancellation is the shutdown signal.
func run(ctx context.Context, args []string, env config.Env, stdout, stderr io.Writer) int {
	cfg, err := config.Load(args, env, stderr)
	if err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0
		}
		fmt.Fprintln(stderr, "error:", err)
		return 2
	}
	log := slog.New(slog.NewTextHandler(stderr, &slog.HandlerOptions{Level: slog.LevelInfo}))
	col, err := collector.New()
	if err != nil {
		log.Error("cannot start collector", "os", runtime.GOOS, "error", err)
		return 1
	}
	builder := processor.Builder{AgentID: cfg.AgentID, HostID: cfg.HostID, Hostname: cfg.Hostname, Service: cfg.Service, BatchMax: cfg.BatchMax}

	if cfg.DryRun {
		return dryRun(cfg, col, builder, stdout, log)
	}

	log.Info("agent starting", "version", version, "os", runtime.GOOS+"/"+runtime.GOARCH, "config", cfg.String())
	if cfg.CleartextWarning {
		log.Warn("the bearer token is sent over plain HTTP to a non-local host; use https")
	}

	buf := buffer.New(cfg.BufferMax)
	exp := &exporter.Exporter{URL: cfg.URL + "/api/v1/telemetry", Token: cfg.Token, Client: &http.Client{Timeout: cfg.RequestTimeout},
		Buf: buf, Log: log, BackoffBase: cfg.BackoffBase, BackoffMax: cfg.BackoffMax, UserAgent: "uptimetrace-agent/" + version,
		Rand: exporter.DefaultRand, Sleep: exporter.DefaultSleep}
	done := make(chan struct{})
	go func() { defer close(done); exp.Run(ctx) }()

	var pending []collector.Observation
	collect := func() {
		now := time.Now()
		obs, err := col.Collect(now)
		if err != nil {
			log.Warn("collection failed; will try again next interval", "error", err)
			return
		}
		pending = append(pending, obs...)
		if len(pending) >= cfg.BatchMax {
			flush(&pending, builder, buf, log, now)
		}
	}

	collect() // baseline sample (CPU needs two samples to produce a value)
	collectTick := time.NewTicker(cfg.Interval)
	flushTick := time.NewTicker(cfg.FlushInterval)
	defer collectTick.Stop()
	defer flushTick.Stop()
loop:
	for {
		select {
		case <-ctx.Done():
			break loop
		case <-collectTick.C:
			collect()
		case <-flushTick.C:
			flush(&pending, builder, buf, log, time.Now())
		}
	}

	log.Info("shutting down: flushing pending observations and draining the buffer", "buffered", buf.Len())
	flush(&pending, builder, buf, log, time.Now())
	<-done
	dctx, cancel := context.WithTimeout(context.Background(), cfg.DrainTimeout)
	exp.Drain(dctx)
	cancel()
	log.Info("agent stopped", "sent", exp.Sent, "dropped_rejected", exp.Dropped, "retries", exp.Retries,
		"dropped_overflow", buf.Dropped(), "undelivered", buf.Len())
	return 0
}

func flush(pending *[]collector.Observation, b processor.Builder, buf *buffer.Buffer, log *slog.Logger, now time.Time) {
	if len(*pending) == 0 {
		return
	}
	entries := b.Build(*pending, now)
	*pending = (*pending)[:0]
	if dropped := buf.Push(entries...); dropped > 0 {
		log.Warn("buffer full: oldest envelopes dropped", "dropped", dropped, "buffered", buf.Len())
	}
}

// dryRun takes two samples one second apart (so CPU has a value) and prints the envelope(s), one JSON document per line.
func dryRun(cfg config.Config, col collector.Collector, b processor.Builder, stdout io.Writer, log *slog.Logger) int {
	if _, err := col.Collect(time.Now()); err != nil {
		log.Error("collection failed", "error", err)
		return 1
	}
	time.Sleep(time.Second)
	now := time.Now()
	obs, err := col.Collect(now)
	if err != nil {
		log.Error("collection failed", "error", err)
		return 1
	}
	for _, e := range b.Build(obs, now) {
		fmt.Fprintln(stdout, string(e.Body))
	}
	return 0
}
