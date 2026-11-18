//go:build linux

// runtime-helper is invoked only through an operator-owned, read-only mount.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"io"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"strings"
	"syscall"
)

const (
	requestLimit           = 32 << 20
	fileLimit              = 20 << 20
	outputLimit            = 1 << 20
	setupOutputLimit       = 64 << 10
	listLimit              = 1000
	archiveLimit     int64 = 1 << 40
	archiveFiles           = 100000
)

var rejected = errors.New("operation rejected")
var uuidPattern = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)

// Paths are private test seams, never flags, request fields or environment variables.
type config struct{ workspace, identity, secrets, staging string }
type fileInput struct {
	Path string `json:"path"`
	B64  []byte `json:"b64"`
}
type request struct {
	Kind        string            `json:"kind"`
	Cmd         []string          `json:"cmd,omitempty"`
	Timeout     float64           `json:"timeout,omitempty"`
	Workdir     string            `json:"workdir,omitempty"`
	ExecEnv     map[string]string `json:"exec_env,omitempty"`
	User        string            `json:"user,omitempty"`
	Files       []fileInput       `json:"files,omitempty"`
	Path        string            `json:"path,omitempty"`
	Limit       int64             `json:"limit,omitempty"`
	Secrets     map[string]string `json:"secrets,omitempty"`
	Paths       []string          `json:"paths,omitempty"`
	MaxBytes    int64             `json:"max_bytes,omitempty"`
	MaxFiles    int               `json:"max_files,omitempty"`
	StreamBytes int64             `json:"stream_bytes,omitempty"`
}

func main() {
	c := config{workspace: "/workspace", identity: "/run/workspace-identity", secrets: "/run/secrets", staging: "/tmp"}
	if err := run(os.Args[1:], os.Stdin, os.Stdout, c); err != nil {
		_, _ = io.WriteString(os.Stderr, "runtime helper: operation rejected\n")
		os.Exit(1)
	}
}
func run(args []string, in io.Reader, out io.Writer, c config) error {
	if len(args) == 1 && args[0] == "hold" {
		signals := make(chan os.Signal, 1)
		signal.Notify(signals, syscall.SIGTERM, syscall.SIGINT)
		defer signal.Stop(signals)
		<-signals
		return nil
	}
	if len(args) == 2 && args[0] == "install" {
		return install(args[1], "")
	}
	if len(args) == 0 || args[0] != "run" {
		return rejected
	}
	flags := flag.NewFlagSet("run", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	uid := flags.String("uid", "", "")
	allocation := flags.String("allocation", "", "")
	generation := flags.String("generation", "", "")
	operation := flags.String("operation", "", "")
	if flags.Parse(args[1:]) != nil || flags.NArg() != 0 || !uuidPattern.MatchString(*operation) {
		return rejected
	}
	if err := fence(c.identity, map[string]string{"uid": *uid, "allocation": *allocation, "generation": *generation}); err != nil {
		return rejected
	}
	input := bufio.NewReaderSize(in, 64<<10)
	header, err := readHeader(input)
	if err != nil {
		return rejected
	}
	var q request
	decoder := json.NewDecoder(bytes.NewReader(header))
	decoder.DisallowUnknownFields()
	if !uniqueJSON(header) || decoder.Decode(&q) != nil {
		return rejected
	}
	if decoder.Decode(new(any)) != io.EOF {
		return rejected
	}
	if q.User != "" && q.User != "user" && q.User != "1000" && q.User != "1000:1000" {
		return rejected
	}
	// Require complete non-stream requests before any side effect. The controller
	// must close stdin, so trailing bytes cannot be mistaken for a second command.
	if q.Kind != "restore" {
		if _, err := input.ReadByte(); err != io.EOF {
			return rejected
		}
	}
	if err := fence(c.identity, map[string]string{"uid": *uid, "allocation": *allocation, "generation": *generation}); err != nil {
		return rejected
	}
	switch q.Kind {
	case "exec", "setup":
		r, err := execute(c.workspace, q)
		if err != nil {
			return rejected
		}
		return json.NewEncoder(out).Encode(r)
	case "put_files", "get_files", "list_files":
		fs, err := openTree(c.workspace)
		if err != nil {
			return rejected
		}
		defer fs.Close()
		r, err := fs.files(q)
		if err != nil {
			return rejected
		}
		return json.NewEncoder(out).Encode(r)
	case "inject_secrets":
		fs, err := openTree(c.secrets)
		if err != nil {
			return rejected
		}
		defer fs.Close()
		if err := fs.inject(q.Secrets); err != nil {
			return rejected
		}
		return json.NewEncoder(out).Encode(map[string]any{})
	case "snapshot":
		fs, err := openTree(c.workspace)
		if err != nil {
			return rejected
		}
		defer fs.Close()
		return snapshot(fs, q, out)
	case "restore":
		return restore(c, q, input, out, func() error {
			return fence(c.identity, map[string]string{"uid": *uid, "allocation": *allocation, "generation": *generation})
		})
	default:
		return rejected
	}
}
func fence(path string, expected map[string]string) error {
	for _, name := range []string{"uid", "allocation", "generation"} {
		want := expected[name]
		if want == "" || len(want) > 256 || strings.ContainsAny(want, "\x00\r\n") {
			return rejected
		}
		// Kubernetes projected-volume entries intentionally use kubelet-owned symlinks.
		// This entire mount must be read-only and owned by the platform.
		f, err := os.Open(path + "/" + name)
		if err != nil {
			return rejected
		}
		b, readErr := io.ReadAll(io.LimitReader(f, 1025))
		f.Close()
		if readErr != nil || len(b) > 1024 {
			return rejected
		}
		actual := strings.TrimSpace(string(b))
		switch name {
		case "uid":
			if !uuidPattern.MatchString(want) || !uuidPattern.MatchString(actual) || !strings.EqualFold(actual, want) {
				return rejected
			}
		case "allocation":
			a, e := allocationUUID(actual)
			w, f := allocationUUID(want)
			if e != nil || f != nil || a != w {
				return rejected
			}
		case "generation":
			if !positiveGeneration(want) || !positiveGeneration(actual) || actual != want {
				return rejected
			}
		}
	}
	return nil
}
func readHeader(r *bufio.Reader) ([]byte, error) {
	var b []byte
	for {
		part, err := r.ReadSlice('\n')
		if len(b)+len(part) > requestLimit {
			return nil, rejected
		}
		b = append(b, part...)
		if err == nil {
			return b, nil
		}
		if err != bufio.ErrBufferFull {
			return nil, rejected
		}
	}
}
func install(destination, source string) error {
	return installInto(destination, source, "/runtime")
}
func installInto(destination, source, root string) error {
	if destination != "/runtime/helper" {
		return rejected
	}
	if source == "" {
		source = "/proc/self/exe"
	}
	f, err := os.Open(source)
	if err != nil {
		return rejected
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil || !st.Mode().IsRegular() || st.Size() == 0 || st.Size() > 64<<20 {
		return rejected
	}
	fs, err := openTree(root)
	if err != nil {
		return rejected
	}
	defer fs.Close()
	return fs.write("helper", io.LimitReader(f, st.Size()), st.Size(), 0555)
}

// Reject duplicate object keys and deep nesting before decoding the protocol.
func uniqueJSON(b []byte) bool {
	d := json.NewDecoder(bytes.NewReader(b))
	var value func(int) bool
	value = func(depth int) bool {
		if depth > 64 {
			return false
		}
		token, err := d.Token()
		if err != nil {
			return false
		}
		delim, ok := token.(json.Delim)
		if !ok {
			return true
		}
		switch delim {
		case '{':
			seen := map[string]bool{}
			for d.More() {
				token, err = d.Token()
				key, ok := token.(string)
				if err != nil || !ok || seen[key] {
					return false
				}
				seen[key] = true
				if !value(depth + 1) {
					return false
				}
			}
			token, err = d.Token()
			return err == nil && token == json.Delim('}')
		case '[':
			for d.More() {
				if !value(depth + 1) {
					return false
				}
			}
			token, err = d.Token()
			return err == nil && token == json.Delim(']')
		default:
			return false
		}
	}
	if !value(0) {
		return false
	}
	_, err := d.Token()
	return err == io.EOF
}

func allocationUUID(s string) (string, error) {
	if uuidPattern.MatchString(s) {
		return strings.ToLower(strings.ReplaceAll(s, "-", "")), nil
	}
	if len(s) != 32 {
		return "", rejected
	}
	for _, c := range s {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f' || c >= 'A' && c <= 'F') {
			return "", rejected
		}
	}
	return strings.ToLower(s), nil
}
func positiveGeneration(s string) bool {
	if len(s) == 0 || s[0] == '0' {
		return false
	}
	for _, c := range s {
		if c < '0' || c > '9' {
			return false
		}
	}
	n, err := strconv.ParseUint(s, 10, 64)
	return err == nil && n > 0
}
