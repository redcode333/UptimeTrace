// Package config reads the agent's settings from flags and environment variables.
//
// The bearer token is accepted ONLY from UPTIMETRACE_TOKEN or a protected file (-token-file / UPTIMETRACE_TOKEN_FILE),
// never from a flag (flags are visible in process lists), and Config prints with the token redacted.
package config

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const (
	MaxBatch = 500 // contract: 1-500 metrics per envelope

	defaultURL      = "http://127.0.0.1:8000"
	defaultInterval = 60 * time.Second
	defaultBuffer   = 1000
)

var (
	identifier = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`) // contract identifier pattern
	service    = regexp.MustCompile(`^[a-z][a-z0-9_.-]{0,62}$`)            // contract service pattern
	tokenChars = regexp.MustCompile(`^[A-Za-z0-9_-]{16,200}$`)             // what the backend accepts as a bearer token
	hostSafe   = regexp.MustCompile(`[^A-Za-z0-9._:-]+`)
)

type Config struct {
	URL      string
	Token    string // never printed: see String()
	AgentID  string
	HostID   string
	Hostname string
	Service  string

	Interval      time.Duration // collection interval
	FlushInterval time.Duration // how often pending observations are cut into envelopes
	BatchMax      int           // observations per envelope, <= MaxBatch
	BufferMax     int           // envelopes kept while the server is unreachable (oldest dropped first)
	DryRun        bool          // print envelopes to stdout and exit; sends nothing

	RequestTimeout time.Duration
	BackoffBase    time.Duration
	BackoffMax     time.Duration
	DrainTimeout   time.Duration // best-effort delivery of buffered envelopes at shutdown

	CleartextWarning bool // token would travel over plain HTTP to a non-loopback host
}

// String and GoString redact the token, so a Config can be logged or printed with %v / %+v / %#v safely.
func (c Config) String() string {
	tok := "<none>"
	if c.Token != "" {
		tok = "<redacted>"
	}
	return fmt.Sprintf("Config{url=%s agent_id=%s host_id=%s hostname=%s service=%q interval=%s batch_max=%d buffer_max=%d token=%s}",
		c.URL, c.AgentID, c.HostID, c.Hostname, c.Service, c.Interval, c.BatchMax, c.BufferMax, tok)
}

func (c Config) GoString() string { return c.String() }

// Env abstracts the process environment so tests need not touch it.
type Env struct {
	Getenv   func(string) string
	ReadFile func(string) ([]byte, error)
	Hostname func() (string, error)
}

func OSEnv() Env { return Env{Getenv: os.Getenv, ReadFile: os.ReadFile, Hostname: os.Hostname} }

// Load parses args (without the program name). Flag defaults come from UPTIMETRACE_* environment variables.
func Load(args []string, env Env, usage io.Writer) (Config, error) {
	fs := flag.NewFlagSet("agent", flag.ContinueOnError)
	fs.SetOutput(usage)
	get := func(name, def string) string {
		if v := env.Getenv(name); v != "" {
			return v
		}
		return def
	}
	host, _ := env.Hostname()
	url_ := fs.String("url", get("UPTIMETRACE_URL", defaultURL), "API base URL")
	agentID := fs.String("agent-id", get("UPTIMETRACE_AGENT_ID", ""), "stable agent identity (must match the credential); default agent-<host-id>")
	hostID := fs.String("host-id", get("UPTIMETRACE_HOST_ID", ""), "stable host identity (must match the credential); default derived from the hostname")
	hostname := fs.String("hostname", get("UPTIMETRACE_HOSTNAME", host), "display hostname")
	svc := fs.String("service", get("UPTIMETRACE_SERVICE", ""), "optional logical service name")
	interval := fs.Duration("interval", durationEnv(env, "UPTIMETRACE_INTERVAL", defaultInterval), "collection interval")
	flush := fs.Duration("flush-interval", durationEnv(env, "UPTIMETRACE_FLUSH_INTERVAL", 0), "how often pending observations become envelopes (default: the collection interval)")
	batch := fs.Int("batch-max", intEnv(env, "UPTIMETRACE_BATCH_MAX", MaxBatch), "max observations per envelope (<= 500)")
	bufMax := fs.Int("buffer-max", intEnv(env, "UPTIMETRACE_BUFFER_MAX", defaultBuffer), "max envelopes buffered while the server is unreachable")
	tokenFile := fs.String("token-file", get("UPTIMETRACE_TOKEN_FILE", ""), "file containing the bearer token (alternative to UPTIMETRACE_TOKEN)")
	dry := fs.Bool("dry-run", false, "collect, print the envelope(s) to stdout and exit; sends nothing")
	if err := fs.Parse(args); err != nil {
		return Config{}, err
	}

	c := Config{URL: strings.TrimRight(*url_, "/"), AgentID: *agentID, HostID: *hostID, Hostname: *hostname, Service: *svc,
		Interval: *interval, FlushInterval: *flush, BatchMax: *batch, BufferMax: *bufMax, DryRun: *dry,
		RequestTimeout: 10 * time.Second, BackoffBase: time.Second, BackoffMax: time.Minute, DrainTimeout: 10 * time.Second}
	if c.FlushInterval == 0 {
		c.FlushInterval = c.Interval
	}
	if c.HostID == "" {
		c.HostID = strings.Trim(hostSafe.ReplaceAllString(strings.ToLower(c.Hostname), "-"), "-.:_")
	}
	if c.AgentID == "" && c.HostID != "" {
		c.AgentID = "agent-" + c.HostID
	}
	if !c.DryRun {
		tok := strings.TrimSpace(env.Getenv("UPTIMETRACE_TOKEN"))
		if tok == "" && *tokenFile != "" {
			b, err := env.ReadFile(*tokenFile)
			if err != nil {
				return Config{}, fmt.Errorf("cannot read token file %q: %w", *tokenFile, errors.Unwrap(err))
			}
			tok = strings.TrimSpace(string(b))
		}
		c.Token = tok
	}
	return c, c.validate()
}

func (c *Config) validate() error {
	var problems []string
	if u, err := url.Parse(c.URL); err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		problems = append(problems, "url must be an http(s) URL")
	} else {
		if u.User != nil {
			problems = append(problems, "url must not contain credentials")
		}
		h := u.Hostname()
		loop := h == "localhost" || (net.ParseIP(h) != nil && net.ParseIP(h).IsLoopback())
		c.CleartextWarning = u.Scheme == "http" && !loop
	}
	if !identifier.MatchString(c.AgentID) {
		problems = append(problems, "agent-id must match [A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
	}
	if !identifier.MatchString(c.HostID) {
		problems = append(problems, "host-id must match [A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
	}
	if n := len(c.Hostname); n < 1 || n > 253 {
		problems = append(problems, "hostname must be 1-253 characters")
	}
	if c.Service != "" && !service.MatchString(c.Service) {
		problems = append(problems, "service must match [a-z][a-z0-9_.-]{0,62}")
	}
	if c.Interval < time.Second {
		problems = append(problems, "interval must be at least 1s")
	}
	if c.FlushInterval < time.Second {
		problems = append(problems, "flush-interval must be at least 1s")
	}
	if c.BatchMax < 1 || c.BatchMax > MaxBatch {
		problems = append(problems, fmt.Sprintf("batch-max must be 1-%d", MaxBatch))
	}
	if c.BufferMax < 1 {
		problems = append(problems, "buffer-max must be at least 1")
	}
	if !c.DryRun {
		if c.Token == "" {
			problems = append(problems, "no bearer token: set UPTIMETRACE_TOKEN or UPTIMETRACE_TOKEN_FILE (or -token-file)")
		} else if !tokenChars.MatchString(c.Token) {
			problems = append(problems, "the bearer token has an invalid format")
		}
	}
	if len(problems) > 0 {
		return errors.New("invalid configuration: " + strings.Join(problems, "; "))
	}
	return nil
}

func durationEnv(env Env, name string, def time.Duration) time.Duration {
	if v := env.Getenv(name); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			return d
		}
	}
	return def
}

func intEnv(env Env, name string, def int) int {
	if v := env.Getenv(name); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}
