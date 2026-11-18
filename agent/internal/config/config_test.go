package config

import (
	"bytes"
	"errors"
	"flag"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"
)

const tok = "utk_0123456789abcdefghijklmnopqrstuvwxyz"

func env(vars map[string]string, files map[string]string) Env {
	return Env{
		Getenv: func(k string) string { return vars[k] },
		ReadFile: func(p string) ([]byte, error) {
			if c, ok := files[p]; ok {
				return []byte(c), nil
			}
			return nil, &os.PathError{Op: "open", Path: p, Err: os.ErrNotExist}
		},
		Hostname: func() (string, error) { return "LAPTOP-UR6BTA5M", nil },
	}
}

func load(t *testing.T, args []string, vars map[string]string, files map[string]string) (Config, error) {
	t.Helper()
	var sink bytes.Buffer
	return Load(args, env(vars, files), &sink)
}

func TestDefaultsDeriveStableIdentityFromTheHostname(t *testing.T) {
	c, err := load(t, nil, map[string]string{"UPTIMETRACE_TOKEN": tok}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if c.HostID != "laptop-ur6bta5m" || c.AgentID != "agent-laptop-ur6bta5m" || c.Hostname != "LAPTOP-UR6BTA5M" {
		t.Fatalf("identity: %+v", c)
	}
	if c.Interval != 60*time.Second || c.FlushInterval != c.Interval || c.BatchMax != 500 || c.URL != "http://127.0.0.1:8000" {
		t.Fatalf("defaults: %v", c)
	}
}

func TestExplicitIdentityAndEnvironmentOverridesWin(t *testing.T) {
	c, err := load(t, []string{"-host-id", "edge-01", "-interval", "15s", "-batch-max", "100"},
		map[string]string{"UPTIMETRACE_TOKEN": tok, "UPTIMETRACE_AGENT_ID": "agent-edge-01", "UPTIMETRACE_SERVICE": "checkout-api"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if c.HostID != "edge-01" || c.AgentID != "agent-edge-01" || c.Service != "checkout-api" || c.Interval != 15*time.Second || c.BatchMax != 100 {
		t.Fatalf("%+v", c)
	}
}

func TestTokenComesFromTheEnvironmentOrAProtectedFileNeverAFlag(t *testing.T) {
	if c, err := load(t, []string{"-token-file", "/etc/ut.token"}, nil, map[string]string{"/etc/ut.token": tok + "\r\n"}); err != nil || c.Token != tok {
		t.Fatalf("token file: %v %q", err, c.Token)
	}
	if _, err := load(t, []string{"-token", tok}, nil, nil); err == nil {
		t.Fatal("a -token flag must not exist (flags are visible in process lists)")
	}
	if _, err := load(t, nil, nil, nil); err == nil || !strings.Contains(err.Error(), "no bearer token") {
		t.Fatalf("missing token must be an error, got %v", err)
	}
	if _, err := load(t, []string{"-token-file", "/missing"}, nil, nil); err == nil || strings.Contains(err.Error(), tok) {
		t.Fatalf("unreadable token file: %v", err)
	}
	if _, err := load(t, nil, map[string]string{"UPTIMETRACE_TOKEN": "short"}, nil); err == nil || strings.Contains(err.Error(), "short") {
		t.Fatalf("a malformed token is rejected without being echoed: %v", err)
	}
	if c, err := load(t, []string{"-dry-run"}, nil, nil); err != nil || c.Token != "" {
		t.Fatalf("dry-run needs no token: %v", err)
	}
}

func TestTheTokenIsNeverPrintedByAnyFormatting(t *testing.T) {
	c, err := load(t, nil, map[string]string{"UPTIMETRACE_TOKEN": tok}, nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, s := range []string{c.String(), fmt.Sprintf("%v", c), fmt.Sprintf("%+v", c), fmt.Sprintf("%#v", c), fmt.Sprint(c), fmt.Sprintf("%v", &c)} {
		if strings.Contains(s, tok) || strings.Contains(s, "utk_") {
			t.Fatalf("token leaked in %q", s)
		}
		if !strings.Contains(s, "<redacted>") {
			t.Fatalf("expected a redaction marker in %q", s)
		}
	}
}

func TestValidationRejectsWhatTheContractWouldReject(t *testing.T) {
	good := map[string]string{"UPTIMETRACE_TOKEN": tok}
	for name, args := range map[string][]string{
		"batch over contract limit": {"-batch-max", "501"}, "batch zero": {"-batch-max", "0"}, "interval under 1s": {"-interval", "500ms"},
		"flush under 1s": {"-flush-interval", "100ms"}, "bad host id": {"-host-id", "-bad"}, "bad agent id": {"-agent-id", "has space"},
		"host id too long": {"-host-id", strings.Repeat("a", 129)}, "bad service": {"-service", "Bad_Service"},
		"url with credentials": {"-url", "http://u:p@127.0.0.1:8000"}, "non-http url": {"-url", "ftp://example.com"}, "buffer zero": {"-buffer-max", "0"},
	} {
		if _, err := load(t, args, good, nil); err == nil {
			t.Errorf("%s: expected an error", name)
		}
	}
	if _, err := load(t, []string{"-help"}, good, nil); !errors.Is(err, flag.ErrHelp) {
		t.Errorf("-help should surface flag.ErrHelp, got %v", err)
	}
}

func TestCleartextWarningOnlyForNonLoopbackHTTP(t *testing.T) {
	for url, want := range map[string]bool{
		"http://127.0.0.1:8000": false, "http://localhost:8000": false, "http://[::1]:8000": false,
		"http://10.0.0.5:8000": true, "http://api.example.com": true, "https://api.example.com": false,
	} {
		c, err := load(t, []string{"-url", url}, map[string]string{"UPTIMETRACE_TOKEN": tok}, nil)
		if err != nil || c.CleartextWarning != want {
			t.Errorf("%s: warn=%v err=%v want %v", url, c.CleartextWarning, err, want)
		}
	}
}
