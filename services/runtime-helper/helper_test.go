package main

import (
	"archive/tar"
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"
)

const testOperation = "12345678-1234-1234-1234-123456789abc"

func fixture(t *testing.T) config {
	t.Helper()
	c := config{workspace: t.TempDir(), identity: t.TempDir(), secrets: t.TempDir(), staging: t.TempDir()}
	for k, v := range map[string]string{"uid": "11111111-1111-4111-8111-111111111111", "allocation": "22222222-2222-4222-8222-222222222222", "generation": "3"} {
		if err := os.WriteFile(filepath.Join(c.identity, k), []byte(v+"\n"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	return c
}
func invoke(t *testing.T, c config, v any, stream []byte) ([]byte, error) {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	b = append(b, '\n')
	b = append(b, stream...)
	var out bytes.Buffer
	err = run([]string{"run", "--uid", "11111111-1111-4111-8111-111111111111", "--allocation", "22222222-2222-4222-8222-222222222222", "--generation", "3", "--operation", testOperation}, bytes.NewReader(b), &out, c)
	return out.Bytes(), err
}
func result(t *testing.T, b []byte) map[string]any {
	t.Helper()
	var v map[string]any
	if err := json.Unmarshal(b, &v); err != nil {
		t.Fatalf("response %q: %v", b, err)
	}
	return v
}
func mustWrite(t *testing.T, p string, b []byte) {
	t.Helper()
	if err := os.WriteFile(p, b, 0600); err != nil {
		t.Fatal(err)
	}
}
func tarBytes(t *testing.T, hs []*tar.Header, contents []string) []byte {
	t.Helper()
	var b bytes.Buffer
	w := tar.NewWriter(&b)
	for i, h := range hs {
		if err := w.WriteHeader(h); err != nil {
			t.Fatal(err)
		}
		if h.Typeflag == tar.TypeReg {
			if _, err := io.WriteString(w, contents[i]); err != nil {
				t.Fatal(err)
			}
		}
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	return b.Bytes()
}

func TestFenceRejectsEveryMismatchBeforeReadingInput(t *testing.T) {
	for _, key := range []string{"uid", "allocation", "generation"} {
		t.Run(key, func(t *testing.T) {
			c := fixture(t)
			mustWrite(t, filepath.Join(c.identity, key), []byte("replacement"))
			in := &panicReader{}
			err := run([]string{"run", "--uid", "11111111-1111-4111-8111-111111111111", "--allocation", "22222222-2222-4222-8222-222222222222", "--generation", "3", "--operation", testOperation}, in, io.Discard, c)
			if err == nil {
				t.Fatal("identity mismatch accepted")
			}
			es, _ := os.ReadDir(c.workspace)
			if len(es) != 0 {
				t.Fatal("workspace touched")
			}
		})
	}
}

type panicReader struct{}

func (*panicReader) Read([]byte) (int, error) { panic("mismatched target consumed request") }

func TestPutReadListAndAtomicReplace(t *testing.T) {
	c := fixture(t)
	for _, text := range []string{"one", "two"} {
		b, err := invoke(t, c, map[string]any{"kind": "put_files", "files": []any{map[string]any{"path": "a/b.txt", "b64": base64.StdEncoding.EncodeToString([]byte(text))}}}, nil)
		if err != nil {
			t.Fatal(err)
		}
		if result(t, b)["size"] != float64(3) {
			t.Fatalf("%s", b)
		}
	}
	b, err := invoke(t, c, map[string]any{"kind": "get_files", "path": "a/b.txt"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if result(t, b)["b64"] != "dHdv" {
		t.Fatalf("%s", b)
	}
	b, err = invoke(t, c, map[string]any{"kind": "list_files", "path": "."}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(result(t, b)["entries"].([]any)) != 2 {
		t.Fatalf("%s", b)
	}
}
func TestFilesRejectTraversalLinksAndOversize(t *testing.T) {
	c := fixture(t)
	outside := t.TempDir()
	mustWrite(t, filepath.Join(outside, "victim"), []byte("safe"))
	if err := os.Symlink(outside, filepath.Join(c.workspace, "link")); err != nil {
		t.Fatal(err)
	}
	for _, p := range []string{"/tmp/escape", "../escape", "a/../escape", "x\x00y", "link/victim"} {
		for _, kind := range []string{"put_files", "get_files", "list_files"} {
			t.Run(kind+p, func(t *testing.T) {
				_, err := invoke(t, c, map[string]any{"kind": kind, "path": p, "files": []any{map[string]any{"path": p, "b64": "eA=="}}}, nil)
				if err == nil {
					t.Fatal("unsafe path accepted")
				}
			})
		}
	}
	b, _ := os.ReadFile(filepath.Join(outside, "victim"))
	if string(b) != "safe" {
		t.Fatal("outside mutated")
	}
	_, err := invoke(t, c, map[string]any{"kind": "put_files", "files": []any{map[string]any{"path": "large", "b64": base64.StdEncoding.EncodeToString(make([]byte, (20<<20)+1))}}}, nil)
	if err == nil {
		t.Fatal("oversize accepted")
	}
}
func TestConcurrentSymlinkReplacementCannotEscape(t *testing.T) {
	c := fixture(t)
	outside := t.TempDir()
	mustWrite(t, filepath.Join(outside, "victim"), []byte("safe"))
	var stop atomic.Bool
	done := make(chan struct{})
	go func() {
		defer close(done)
		for !stop.Load() {
			os.Rename(filepath.Join(c.workspace, "race"), filepath.Join(c.workspace, "old"))
			os.Symlink(outside, filepath.Join(c.workspace, "race"))
			os.Remove(filepath.Join(c.workspace, "race"))
			os.Rename(filepath.Join(c.workspace, "old"), filepath.Join(c.workspace, "race"))
			os.Mkdir(filepath.Join(c.workspace, "race"), 0700)
		}
	}()
	for i := 0; i < 200; i++ {
		invoke(t, c, map[string]any{"kind": "put_files", "files": []any{map[string]any{"path": "race/victim", "b64": "Y2hhbmdlZA=="}}}, nil)
	}
	stop.Store(true)
	<-done
	b, _ := os.ReadFile(filepath.Join(outside, "victim"))
	if string(b) != "safe" {
		t.Fatal("symlink race escaped workspace")
	}
}
func TestListBoundedDepthAndCount(t *testing.T) {
	c := fixture(t)
	os.MkdirAll(filepath.Join(c.workspace, "a/b/c"), 0700)
	mustWrite(t, filepath.Join(c.workspace, "a/b/c/hidden"), []byte("x"))
	for i := 0; i < 1100; i++ {
		mustWrite(t, filepath.Join(c.workspace, fmt.Sprintf("f%04d", i)), nil)
	}
	b, err := invoke(t, c, map[string]any{"kind": "list_files"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	entries := result(t, b)["entries"].([]any)
	if len(entries) != 1000 {
		t.Fatalf("got %d", len(entries))
	}
	for _, e := range entries {
		if strings.Contains(e.(map[string]any)["path"].(string), "hidden") {
			t.Fatal("depth exceeded")
		}
	}
}
func TestExecMergedOutputEnvCwdAndLimit(t *testing.T) {
	c := fixture(t)
	os.Mkdir(filepath.Join(c.workspace, "sub"), 0700)
	b, err := invoke(t, c, map[string]any{"kind": "exec", "cmd": []string{"/bin/sh", "-c", "printf '%s:' \"$HELLO\"; pwd; printf err >&2; exit 7"}, "exec_env": map[string]string{"HELLO": "world"}, "workdir": "sub", "timeout": 2}, nil)
	if err != nil {
		t.Fatal(err)
	}
	r := result(t, b)
	raw, _ := base64.StdEncoding.DecodeString(r["output_b64"].(string))
	if r["exit_code"] != float64(7) || !bytes.Contains(raw, []byte("world:")) || !bytes.Contains(raw, []byte("/sub")) || !bytes.Contains(raw, []byte("err")) {
		t.Fatalf("%s %q", b, raw)
	}
	b, err = invoke(t, c, map[string]any{"kind": "exec", "cmd": []string{"/bin/sh", "-c", "head -c 1200000 /dev/zero"}, "timeout": 2}, nil)
	if err != nil {
		t.Fatal(err)
	}
	r = result(t, b)
	raw, _ = base64.StdEncoding.DecodeString(r["output_b64"].(string))
	if len(raw) != (1<<20) || r["truncated"] != true {
		t.Fatalf("bad bound: %d %v", len(raw), r["truncated"])
	}
	_, err = invoke(t, c, map[string]any{"kind": "exec", "cmd": []string{"/bin/true"}, "user": "root"}, nil)
	if err == nil {
		t.Fatal("root accepted")
	}
}
func TestExecTimeoutKillsProcessGroup(t *testing.T) {
	c := fixture(t)
	b, err := invoke(t, c, map[string]any{"kind": "exec", "cmd": []string{"/bin/sh", "-c", "sleep 30 & echo $!; wait"}, "timeout": 0.15}, nil)
	if err != nil {
		t.Fatal(err)
	}
	r := result(t, b)
	if r["timed_out"] != true {
		t.Fatalf("%s", b)
	}
	raw, _ := base64.StdEncoding.DecodeString(r["output_b64"].(string))
	pid, _ := strconv.Atoi(strings.TrimSpace(string(raw)))
	if pid <= 1 {
		t.Fatalf("missing child pid: %q", raw)
	}
	// A killed, unreaped orphan may remain a zombie until PID1 reaps it; it must never run.
	data, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if err == nil {
		fields := strings.Fields(string(data))
		if len(fields) < 3 || fields[2] != "Z" {
			t.Fatalf("descendant still alive %s", data)
		}
	}
}
func TestSetupLimitAndSecretsPrivate(t *testing.T) {
	c := fixture(t)
	b, err := invoke(t, c, map[string]any{"kind": "setup", "cmd": []string{"/bin/sh", "-c", "head -c 100000 /dev/zero"}, "timeout": 2}, nil)
	if err != nil {
		t.Fatal(err)
	}
	r := result(t, b)
	raw, _ := base64.StdEncoding.DecodeString(r["output_b64"].(string))
	if len(raw) != 64<<10 || r["truncated"] != true {
		t.Fatal("setup output unbounded")
	}
	b, err = invoke(t, c, map[string]any{"kind": "inject_secrets", "secrets": map[string]string{"TOKEN": "secret-value"}}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(b, []byte("secret-value")) {
		t.Fatal("secret echoed")
	}
	p := filepath.Join(c.secrets, "TOKEN")
	data, _ := os.ReadFile(p)
	s, _ := os.Stat(p)
	if string(data) != "secret-value" || s.Mode().Perm() != 0400 {
		t.Fatal("bad secret mode/content")
	}
	_, err = invoke(t, c, map[string]any{"kind": "inject_secrets", "secrets": map[string]string{"../escape": "x"}}, nil)
	if err == nil {
		t.Fatal("secret traversal accepted")
	}
}
func TestSnapshotRestoreRoundTripAndRequestedPaths(t *testing.T) {
	c := fixture(t)
	os.MkdirAll(filepath.Join(c.workspace, "dir"), 0700)
	mustWrite(t, filepath.Join(c.workspace, "dir/file"), []byte("contents"))
	mustWrite(t, filepath.Join(c.workspace, "excluded"), []byte("no"))
	b, err := invoke(t, c, map[string]any{"kind": "snapshot", "paths": []string{"dir"}, "max_bytes": 1 << 20, "max_files": 100}, nil)
	if err != nil {
		t.Fatal(err)
	}
	dest := fixture(t)
	_, err = invoke(t, dest, map[string]any{"kind": "restore", "stream_bytes": len(b), "max_bytes": 1 << 20, "max_files": 100}, b)
	if err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(filepath.Join(dest.workspace, "dir/file"))
	if string(got) != "contents" {
		t.Fatalf("%q", got)
	}
	if _, err := os.Stat(filepath.Join(dest.workspace, "excluded")); !os.IsNotExist(err) {
		t.Fatal("unexpected file")
	}
}
func TestRestoreRejectsArchiveBeforeExistingTargetMutation(t *testing.T) {
	cases := []struct {
		name string
		hs   []*tar.Header
		data []string
	}{
		{"traversal", []*tar.Header{{Name: "../outside", Mode: 0600, Size: 1, Typeflag: tar.TypeReg}}, []string{"x"}},
		{"absolute", []*tar.Header{{Name: "/outside", Mode: 0600, Size: 1, Typeflag: tar.TypeReg}}, []string{"x"}},
		{"symlink", []*tar.Header{{Name: "x", Typeflag: tar.TypeSymlink, Linkname: "/tmp"}}, []string{""}},
		{"hardlink", []*tar.Header{{Name: "x", Typeflag: tar.TypeLink, Linkname: "kept"}}, []string{""}},
		{"fifo", []*tar.Header{{Name: "x", Typeflag: tar.TypeFifo}}, []string{""}},
		{"device", []*tar.Header{{Name: "x", Typeflag: tar.TypeChar}}, []string{""}},
		{"duplicate", []*tar.Header{{Name: "kept", Size: 3, Typeflag: tar.TypeReg}, {Name: "kept", Size: 3, Typeflag: tar.TypeReg}}, []string{"bad", "bad"}},
		{"late-invalid", []*tar.Header{{Name: "kept", Size: 3, Typeflag: tar.TypeReg}, {Name: "../escape", Size: 3, Typeflag: tar.TypeReg}}, []string{"bad", "bad"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c := fixture(t)
			mustWrite(t, filepath.Join(c.workspace, "kept"), []byte("safe"))
			stream := tarBytes(t, tc.hs, tc.data)
			_, err := invoke(t, c, map[string]any{"kind": "restore", "stream_bytes": len(stream), "max_bytes": 1 << 20, "max_files": 100}, stream)
			if err == nil {
				t.Fatal("malicious archive accepted")
			}
			b, _ := os.ReadFile(filepath.Join(c.workspace, "kept"))
			if string(b) != "safe" {
				t.Fatal("rejected archive mutated destination")
			}
		})
	}
}
func TestRestoreBoundsAndTruncation(t *testing.T) {
	stream := tarBytes(t, []*tar.Header{{Name: "a", Size: 2000, Typeflag: tar.TypeReg}, {Name: "b", Size: 1, Typeflag: tar.TypeReg}}, []string{strings.Repeat("x", 2000), "b"})
	for _, tc := range []struct{ size, maxBytes, maxFiles int }{{len(stream) - 1, 1 << 20, 100}, {len(stream), 1000, 100}, {len(stream), 1 << 20, 1}} {
		c := fixture(t)
		_, err := invoke(t, c, map[string]any{"kind": "restore", "stream_bytes": tc.size, "max_bytes": tc.maxBytes, "max_files": tc.maxFiles}, stream)
		if err == nil {
			t.Fatalf("invalid stream accepted %+v", tc)
		}
	}
}
func TestStrictBoundedRequest(t *testing.T) {
	c := fixture(t)
	for _, input := range []string{"{}\n", "{\"kind\":\"exec\",\"cmd\":[\"/bin/true\"],\"unknown\":1}\n", "{\"kind\":\"list_files\"}\ntrailing", strings.Repeat(" ", 32<<20) + "{}\n"} {
		var b bytes.Buffer
		err := run([]string{"run", "--uid", "11111111-1111-4111-8111-111111111111", "--allocation", "22222222-2222-4222-8222-222222222222", "--generation", "3", "--operation", testOperation}, strings.NewReader(input), &b, c)
		if err == nil {
			t.Fatal("invalid request accepted")
		}
	}
}
func TestInstallOnlyFixedDestination(t *testing.T) {
	if err := install("/tmp/untrusted", ""); err == nil {
		t.Fatal("arbitrary destination accepted")
	}
}

// Keep syscall/time imports available for timeout/race follow-up regression tests.
var _ = syscall.Kill
var _ = time.Second

func TestRequestRejectsDuplicateJSONKeys(t *testing.T) {
	c := fixture(t)
	var out bytes.Buffer
	err := run([]string{"run", "--uid", "11111111-1111-4111-8111-111111111111", "--allocation", "22222222-2222-4222-8222-222222222222", "--generation", "3", "--operation", testOperation}, strings.NewReader("{\"kind\":\"put_files\",\"kind\":\"list_files\"}\n"), &out, c)
	if err == nil {
		t.Fatal("duplicate operation key accepted")
	}
}

type changingReader struct {
	r      *strings.Reader
	once   bool
	change func()
}

func (r *changingReader) Read(b []byte) (int, error) {
	if !r.once {
		r.once = true
		r.change()
	}
	return r.r.Read(b)
}
func TestFenceRecheckedAfterUntrustedInput(t *testing.T) {
	c := fixture(t)
	input := &changingReader{r: strings.NewReader("{\"kind\":\"put_files\",\"files\":[{\"path\":\"unsafe\",\"b64\":\"eA==\"}]}\n"), change: func() { mustWrite(t, filepath.Join(c.identity, "generation"), []byte("4")) }}
	err := run([]string{"run", "--uid", "11111111-1111-4111-8111-111111111111", "--allocation", "22222222-2222-4222-8222-222222222222", "--generation", "3", "--operation", testOperation}, input, io.Discard, c)
	if err == nil {
		t.Fatal("generation changed during input was accepted")
	}
	if _, err := os.Stat(filepath.Join(c.workspace, "unsafe")); !os.IsNotExist(err) {
		t.Fatal("stale operation wrote file")
	}
}
func TestExecUsesRequestedPATH(t *testing.T) {
	c := fixture(t)
	os.Mkdir(filepath.Join(c.workspace, "bin"), 0700)
	p := filepath.Join(c.workspace, "bin", "custom-command")
	mustWrite(t, p, []byte("#!/bin/sh\nprintf custom"))
	os.Chmod(p, 0700)
	b, err := invoke(t, c, map[string]any{"kind": "exec", "cmd": []string{"custom-command"}, "exec_env": map[string]string{"PATH": filepath.Join(c.workspace, "bin")}, "timeout": 2}, nil)
	if err != nil {
		t.Fatal(err)
	}
	r := result(t, b)
	if r["output_b64"] != "Y3VzdG9t" {
		t.Fatalf("%s", b)
	}
}
func TestInstallCopiesExecutableAtomicallyAndRejectsSymlink(t *testing.T) {
	target := t.TempDir()
	source, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	if err = installInto("/runtime/helper", source, target); err != nil {
		t.Fatal(err)
	}
	src, _ := os.ReadFile(source)
	got, err := os.ReadFile(filepath.Join(target, "helper"))
	if err != nil || !bytes.Equal(src, got) {
		t.Fatal("executable copy differs")
	}
	st, _ := os.Stat(filepath.Join(target, "helper"))
	if st.Mode().Perm() != 0555 {
		t.Fatal("executable permissions")
	}
	os.Remove(filepath.Join(target, "helper"))
	os.Symlink(filepath.Join(t.TempDir(), "outside"), filepath.Join(target, "helper"))
	if err = installInto("/runtime/helper", source, target); err == nil {
		t.Fatal("install symlink accepted")
	}
}
func TestGetRejectsFIFONonblocking(t *testing.T) {
	c := fixture(t)
	if err := syscall.Mkfifo(filepath.Join(c.workspace, "pipe"), 0600); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { _, err := invoke(t, c, map[string]any{"kind": "get_files", "path": "pipe"}, nil); done <- err }()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("FIFO accepted")
		}
	case <-time.After(time.Second):
		t.Fatal("FIFO open blocked")
	}
}
func TestRestoreRejectsHierarchyAndTrailingArchive(t *testing.T) {
	cases := [][]*tar.Header{
		{{Name: "file/child", Size: 1, Typeflag: tar.TypeReg}, {Name: "file", Size: 1, Typeflag: tar.TypeReg}},
		{{Name: "file", Size: 1, Typeflag: tar.TypeReg}, {Name: "file/child", Size: 1, Typeflag: tar.TypeReg}},
	}
	for _, hs := range cases {
		c := fixture(t)
		stream := tarBytes(t, hs, []string{"x", "y"})
		_, err := invoke(t, c, map[string]any{"kind": "restore", "stream_bytes": len(stream), "max_bytes": 1 << 20}, stream)
		if err == nil {
			t.Fatal("file/directory hierarchy accepted")
		}
		es, _ := os.ReadDir(c.workspace)
		if len(es) != 0 {
			t.Fatal("invalid hierarchy created destination entries")
		}
	}
	c := fixture(t)
	part := tarBytes(t, []*tar.Header{{Name: "file", Size: 1, Typeflag: tar.TypeReg}}, []string{"x"})
	stream := append(append([]byte{}, part...), part...)
	if _, err := invoke(t, c, map[string]any{"kind": "restore", "stream_bytes": len(stream), "max_bytes": 1 << 20}, stream); err == nil {
		t.Fatal("second trailing tar stream accepted")
	}
}
func TestSnapshotLimitsLinksAndOverlappingPaths(t *testing.T) {
	c := fixture(t)
	os.Mkdir(filepath.Join(c.workspace, "dir"), 0700)
	mustWrite(t, filepath.Join(c.workspace, "dir/file"), []byte("x"))
	stream, err := invoke(t, c, map[string]any{"kind": "snapshot", "paths": []string{"dir/file", "dir", "dir"}, "max_bytes": 1 << 20}, nil)
	if err != nil {
		t.Fatal(err)
	}
	seen := map[string]bool{}
	tr := tar.NewReader(bytes.NewReader(stream))
	for {
		h, e := tr.Next()
		if e == io.EOF {
			break
		}
		if e != nil {
			t.Fatal(e)
		}
		if seen[h.Name] {
			t.Fatal("snapshot duplicate members")
		}
		seen[h.Name] = true
	}
	if !seen["dir/file"] {
		t.Fatal("selection missing")
	}
	for _, req := range []map[string]any{{"kind": "snapshot", "max_bytes": 1024}, {"kind": "snapshot", "max_files": 1}} {
		if _, err = invoke(t, c, req, nil); err == nil {
			t.Fatal("snapshot exceeded bound")
		}
	}
	os.Symlink("file", filepath.Join(c.workspace, "dir/link"))
	if _, err = invoke(t, c, map[string]any{"kind": "snapshot"}, nil); err == nil {
		t.Fatal("snapshot symlink accepted")
	}
}

func TestLargeSnapshotRestoreStreamsBeyondFileAPILimit(t *testing.T) {
	c := fixture(t)
	p := filepath.Join(c.workspace, "large")
	f, err := os.Create(p)
	if err != nil {
		t.Fatal(err)
	}
	const size = 24 << 20
	if err = f.Truncate(size); err != nil {
		t.Fatal(err)
	}
	f.Close()
	archive, err := os.CreateTemp(t.TempDir(), "snapshot")
	if err != nil {
		t.Fatal(err)
	}
	defer archive.Close()
	args := []string{"run", "--uid", "11111111-1111-4111-8111-111111111111", "--allocation", "22222222-2222-4222-8222-222222222222", "--generation", "3", "--operation", testOperation}
	if err = run(args, strings.NewReader("{\"kind\":\"snapshot\",\"max_bytes\":33554432}\n"), archive, c); err != nil {
		t.Fatal(err)
	}
	st, _ := archive.Stat()
	archive.Seek(0, io.SeekStart)
	target := fixture(t)
	header := fmt.Sprintf("{\"kind\":\"restore\",\"stream_bytes\":%d,\"max_bytes\":33554432}\n", st.Size())
	var out bytes.Buffer
	if err = run(args, io.MultiReader(strings.NewReader(header), archive), &out, target); err != nil {
		t.Fatal(err)
	}
	restored, err := os.Stat(filepath.Join(target.workspace, "large"))
	if err != nil || restored.Size() != size {
		t.Fatalf("bad large restore: %v %v", restored, err)
	}
	entries, err := os.ReadDir(target.staging)
	if err != nil || len(entries) != 0 {
		t.Fatal("restore left named staging material")
	}
}
func TestRestoreRechecksIdentityAfterStreamValidation(t *testing.T) {
	c := fixture(t)
	stream := tarBytes(t, []*tar.Header{{Name: "unsafe", Size: 1, Typeflag: tar.TypeReg}}, []string{"x"})
	header := fmt.Sprintf("{\"kind\":\"restore\",\"stream_bytes\":%d,\"max_bytes\":1048576}\n", len(stream))
	binary := &changingReader{r: strings.NewReader(string(stream)), change: func() { mustWrite(t, filepath.Join(c.identity, "allocation"), []byte("replaced")) }}
	err := run([]string{"run", "--uid", "11111111-1111-4111-8111-111111111111", "--allocation", "22222222-2222-4222-8222-222222222222", "--generation", "3", "--operation", testOperation}, io.MultiReader(strings.NewReader(header), binary), io.Discard, c)
	if err == nil {
		t.Fatal("stale restore accepted")
	}
	es, _ := os.ReadDir(c.workspace)
	if len(es) != 0 {
		t.Fatal("stale restore modified target")
	}
}

func TestExistingUserAliasAndAllocationHex(t *testing.T) {
	c := fixture(t)
	mustWrite(t, filepath.Join(c.identity, "allocation"), []byte("22222222222242228222222222222222"))
	if _, err := invoke(t, c, map[string]any{"kind": "exec", "cmd": []string{"/bin/true"}, "user": "user", "timeout": 2}, nil); err != nil {
		t.Fatal(err)
	}
}
func TestMalformedUIDAndGenerationRejectedEvenWhenMatching(t *testing.T) {
	for _, tc := range []struct{ key, value string }{{"uid", "not-a-uuid"}, {"generation", "0"}, {"generation", "-1"}, {"generation", "3x"}, {"generation", "03"}} {
		t.Run(tc.key+tc.value, func(t *testing.T) {
			c := fixture(t)
			mustWrite(t, filepath.Join(c.identity, tc.key), []byte(tc.value))
			args := []string{"run", "--uid", "11111111-1111-4111-8111-111111111111", "--allocation", "22222222-2222-4222-8222-222222222222", "--generation", "3", "--operation", testOperation}
			for i := range args {
				if args[i] == "--"+tc.key {
					args[i+1] = tc.value
				}
			}
			if err := run(args, &panicReader{}, io.Discard, c); err == nil {
				t.Fatal("malformed matching identity accepted")
			}
		})
	}
}

func TestRestoreRefusesNonemptyDestination(t *testing.T) {
	c := fixture(t)
	mustWrite(t, filepath.Join(c.workspace, "kept"), []byte("safe"))
	stream := tarBytes(t, []*tar.Header{{Name: "kept", Size: 3, Typeflag: tar.TypeReg}}, []string{"bad"})
	if _, err := invoke(t, c, map[string]any{"kind": "restore", "stream_bytes": len(stream), "max_bytes": 1 << 20}, stream); err == nil {
		t.Fatal("restore overwrote nonempty destination")
	}
	b, _ := os.ReadFile(filepath.Join(c.workspace, "kept"))
	if string(b) != "safe" {
		t.Fatal("existing destination changed")
	}
}
func TestRestoreCountsImplicitDirectoriesAgainstFileLimit(t *testing.T) {
	c := fixture(t)
	stream := tarBytes(t, []*tar.Header{{Name: "a/b/c/file", Size: 1, Typeflag: tar.TypeReg}}, []string{"x"})
	if _, err := invoke(t, c, map[string]any{"kind": "restore", "stream_bytes": len(stream), "max_bytes": 1 << 20, "max_files": 2}, stream); err == nil {
		t.Fatal("implicit directory count exceeded declared maximum")
	}
	es, _ := os.ReadDir(c.workspace)
	if len(es) != 0 {
		t.Fatal("invalid stream made directories")
	}
}

func TestHoldSubprocess(t *testing.T) {
	if os.Getenv("RUNTIME_HELPER_TEST_HOLD") == "1" {
		if err := run([]string{"hold"}, &panicReader{}, io.Discard, config{}); err != nil {
			os.Exit(2)
		}
		os.Exit(0)
	}
	cmd := exec.Command(os.Args[0], "-test.run=^TestHoldSubprocess$")
	cmd.Env = append(os.Environ(), "RUNTIME_HELPER_TEST_HOLD=1")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer cmd.Process.Kill()
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case err := <-done:
		t.Fatalf("hold exited before signal: %v", err)
	case <-time.After(150 * time.Millisecond):
	}
	if err := cmd.Process.Signal(syscall.SIGTERM); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("hold rejected graceful termination: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("hold ignored termination")
	}
	if err := run([]string{"hold", "extra"}, &panicReader{}, io.Discard, config{}); err == nil {
		t.Fatal("hold accepted operation arguments")
	}
}

func TestSnapshotRestorePreservesExecutableAndDirectoryModes(t *testing.T) {
	c := fixture(t)
	os.Mkdir(filepath.Join(c.workspace, "bin"), 0750)
	p := filepath.Join(c.workspace, "bin/tool")
	mustWrite(t, p, []byte("#!/bin/sh\nexit 0\n"))
	os.Chmod(p, 0755)
	os.Chmod(filepath.Join(c.workspace, "bin"), 0550)
	t.Cleanup(func() { os.Chmod(filepath.Join(c.workspace, "bin"), 0700) })
	stream, err := invoke(t, c, map[string]any{"kind": "snapshot", "max_bytes": 1 << 20}, nil)
	if err != nil {
		t.Fatal(err)
	}
	target := fixture(t)
	if _, err = invoke(t, target, map[string]any{"kind": "restore", "stream_bytes": len(stream), "max_bytes": 1 << 20}, stream); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chmod(filepath.Join(target.workspace, "bin"), 0700) })
	for p, mode := range map[string]os.FileMode{"bin": 0550, "bin/tool": 0755} {
		st, err := os.Stat(filepath.Join(target.workspace, p))
		if err != nil || st.Mode().Perm() != mode {
			t.Fatalf("mode %s got %v err %v", p, st, err)
		}
	}
}

func TestRestoreStripsPrivilegedModesAndOwnership(t *testing.T) {
	c := fixture(t)
	stream := tarBytes(t, []*tar.Header{{Name: "tool", Size: 1, Mode: 07755, Uid: 0, Gid: 0, Typeflag: tar.TypeReg}}, []string{"x"})
	if _, err := invoke(t, c, map[string]any{"kind": "restore", "stream_bytes": len(stream), "max_bytes": 1 << 20}, stream); err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(filepath.Join(c.workspace, "tool"))
	if err != nil {
		t.Fatal(err)
	}
	if st.Mode().Perm() != 0755 || st.Mode()&(os.ModeSetuid|os.ModeSetgid|os.ModeSticky) != 0 {
		t.Fatalf("unsafe or lost mode: %v", st.Mode())
	}
	if st.Sys().(*syscall.Stat_t).Uid != uint32(os.Getuid()) {
		t.Fatal("restored foreign owner")
	}
}
