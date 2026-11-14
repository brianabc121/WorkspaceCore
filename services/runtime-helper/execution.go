//go:build linux

package main

import (
	"encoding/base64"
	"fmt"
	"io"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"syscall"
	"time"
)

type execResult struct {
	ExitCode  int     `json:"exit_code"`
	OutputB64 string  `json:"output_b64"`
	Truncated bool    `json:"truncated"`
	TimedOut  bool    `json:"timed_out"`
	Seconds   float64 `json:"seconds"`
	StartedAt string  `json:"started_at"`
}
type boundedOutput struct {
	mu        sync.Mutex
	b         []byte
	limit     int
	truncated bool
}

func (w *boundedOutput) Write(p []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	n := len(p)
	room := w.limit - len(w.b)
	if len(p) > room {
		w.truncated = true
		p = p[:room]
	}
	w.b = append(w.b, p...)
	return n, nil
}
func execute(workspace string, q request) (execResult, error) {
	var r execResult
	maxTimeout := 7200.0
	cap := outputLimit
	defaultTimeout := 600.0
	if q.Kind == "setup" {
		maxTimeout = 1800
		defaultTimeout = 1800
		cap = setupOutputLimit
	}
	if q.Timeout == 0 {
		q.Timeout = defaultTimeout
	}
	if q.Timeout < 0 || q.Timeout > maxTimeout || math.IsNaN(q.Timeout) || math.IsInf(q.Timeout, 0) || len(q.Cmd) == 0 || len(q.Cmd) > 1000 {
		return r, rejected
	}
	chars := 0
	for _, v := range q.Cmd {
		chars += len(v)
		if strings.ContainsRune(v, 0) {
			return r, rejected
		}
	}
	if chars > 100000 || q.Cmd[0] == "" {
		return r, rejected
	}
	if len(q.ExecEnv) > 50 {
		return r, rejected
	}
	env := map[string]string{}
	for _, v := range os.Environ() {
		k, val, _ := strings.Cut(v, "=")
		env[k] = val
	}
	for k, v := range q.ExecEnv {
		if k == "" || len(k) > 128 || len(v) > 4096 || strings.ContainsAny(k, "=\x00") || strings.ContainsRune(v, 0) {
			return r, rejected
		}
		env[k] = v
	}
	fs, err := openTree(workspace)
	if err != nil {
		return r, err
	}
	defer fs.Close()
	cwd, err := fs.directory(q.Workdir, false)
	if err != nil {
		return r, err
	}
	defer cwd.Close()
	resolved, err := commandPath(q.Cmd[0], env["PATH"], cwd)
	if err != nil {
		return r, err
	}
	cmd := &exec.Cmd{Path: resolved, Args: q.Cmd}
	// Dir refers to a pinned directory FD and is resolved before exec closes it.
	cmd.Dir = fmt.Sprintf("/proc/self/fd/%d", cwd.Fd())
	cmd.ExtraFiles = []*os.File{cwd}
	cmd.Env = make([]string, 0, len(env))
	for k, v := range env {
		cmd.Env = append(cmd.Env, k+"="+v)
	}
	sort.Strings(cmd.Env)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true, Pdeathsig: syscall.SIGKILL}
	reader, writer, err := os.Pipe()
	if err != nil {
		return r, err
	}
	defer reader.Close()
	cmd.Stdout = writer
	cmd.Stderr = writer
	start := time.Now()
	r.StartedAt = start.UTC().Format(time.RFC3339Nano)
	if err = cmd.Start(); err != nil {
		writer.Close()
		return r, rejected
	}
	writer.Close()
	output := &boundedOutput{limit: cap}
	drained := make(chan struct{})
	go func() { _, _ = io.Copy(output, reader); close(drained) }()
	waited := make(chan error, 1)
	go func() { waited <- cmd.Wait() }()
	timer := time.NewTimer(time.Duration(q.Timeout * float64(time.Second)))
	defer timer.Stop()
	select {
	case err = <-waited:
	case <-timer.C:
		r.TimedOut = true
		if e := syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL); e != nil && e != syscall.ESRCH {
			return r, rejected
		}
		err = <-waited
	}
	// Reap the leader and terminate remaining members, including descendants that
	// outlive a successful shell. An escaped session still requires workload stop.
	if e := syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL); e != nil && e != syscall.ESRCH {
		return r, rejected
	}
	select {
	case <-drained:
	case <-time.After(200 * time.Millisecond):
		reader.Close()
		<-drained
	}
	r.ExitCode = cmd.ProcessState.ExitCode()
	if err != nil && r.ExitCode == 0 {
		return r, rejected
	}
	output.mu.Lock()
	r.OutputB64 = base64.StdEncoding.EncodeToString(output.b)
	r.Truncated = output.truncated
	output.mu.Unlock()
	r.Seconds = time.Since(start).Seconds()
	return r, nil
}

func commandPath(name, search string, cwd *os.File) (string, error) {
	if strings.Contains(name, "/") {
		return name, nil
	}
	for _, dir := range filepath.SplitList(search) {
		if dir == "" {
			dir = "."
		}
		candidate := filepath.Join(dir, name)
		if !strings.Contains(candidate, "/") {
			candidate = "./" + candidate
		}
		probe := candidate
		if !filepath.IsAbs(probe) {
			probe = fmt.Sprintf("/proc/self/fd/%d/%s", cwd.Fd(), candidate)
		}
		if err := syscall.Access(probe, 1); err != nil {
			continue
		}
		st, err := os.Stat(probe)
		if err == nil && st.Mode().IsRegular() {
			return candidate, nil
		}
	}
	return "", rejected
}
