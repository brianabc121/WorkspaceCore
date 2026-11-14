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
	"strconv"
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
	leaderDone := false
	select {
	case err = <-waited:
		leaderDone = true
	case <-timer.C:
		r.TimedOut = true
	}
	// Signal delivery and leader reaping do not prove that every group member
	// has finished exiting. Bound both leader wait and observable group cleanup.
	cleanupDeadline := time.Now().Add(2 * time.Second)
	if e := syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL); e != nil && e != syscall.ESRCH {
		return r, rejected
	}
	if !leaderDone {
		cleanupTimer := time.NewTimer(time.Until(cleanupDeadline))
		defer cleanupTimer.Stop()
		select {
		case err = <-waited:
		case <-cleanupTimer.C:
			return r, rejected
		}
	}
	if e := waitProcessGroupStopped(cmd.Process.Pid, cleanupDeadline); e != nil {
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

// Wait for observed non-zombie members to disappear, rather than assuming that
// SIGKILL delivery or pipe EOF is equivalent to process termination. A failed
// observation is an unknown outcome; no successful exec JSON may be emitted.
func waitProcessGroupStopped(pgid int, deadline time.Time) error {
	for {
		alive, err := processGroupAlive(pgid, deadline)
		if err != nil {
			return rejected
		}
		if !alive {
			return nil
		}
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return rejected
		}
		pause := 5 * time.Millisecond
		if remaining < pause {
			pause = remaining
		}
		timer := time.NewTimer(pause)
		<-timer.C
	}
}
func processGroupAlive(pgid int, deadline time.Time) (bool, error) {
	if pgid <= 0 {
		return false, rejected
	}
	if err := syscall.Kill(-pgid, 0); err == syscall.ESRCH {
		return false, nil
	} else if err != nil {
		return false, err
	}
	proc, err := os.Open("/proc")
	if err != nil {
		return false, err
	}
	defer proc.Close()
	for {
		names, err := proc.Readdirnames(64)
		if err != nil && err != io.EOF {
			return false, err
		}
		for _, name := range names {
			if time.Now().After(deadline) {
				return false, rejected
			}
			pid, e := strconv.Atoi(name)
			if e != nil || pid <= 0 {
				continue
			}
			state, group, e := procState("/proc/" + name + "/stat")
			if os.IsNotExist(e) || e == syscall.ESRCH {
				continue
			}
			if e != nil {
				return false, e
			}
			if group != pgid {
				continue
			}
			if state != "Z" && state != "X" {
				return true, nil
			}
			// A zombie thread-group leader can still have running worker threads.
			// Inspect all remaining tasks before accepting a zombie process as dead.
			tasks, e := os.Open("/proc/" + name + "/task")
			if os.IsNotExist(e) || e == syscall.ESRCH {
				continue
			}
			if e != nil {
				return false, e
			}
			alive, e := liveTasks(tasks, "/proc/"+name+"/task/", pgid, deadline)
			tasks.Close()
			if e != nil || alive {
				return alive, e
			}
		}
		if err == io.EOF {
			return false, nil
		}
	}
}
func liveTasks(tasks *os.File, prefix string, pgid int, deadline time.Time) (bool, error) {
	for {
		names, err := tasks.Readdirnames(64)
		if err != nil && err != io.EOF {
			return false, err
		}
		for _, name := range names {
			if time.Now().After(deadline) {
				return false, rejected
			}
			state, group, e := procState(prefix + name + "/stat")
			if os.IsNotExist(e) || e == syscall.ESRCH {
				continue
			}
			if e != nil {
				return false, e
			}
			if group == pgid && state != "Z" && state != "X" {
				return true, nil
			}
		}
		if err == io.EOF {
			return false, nil
		}
	}
}
func procState(name string) (string, int, error) {
	f, err := os.Open(name)
	if err != nil {
		return "", 0, err
	}
	defer f.Close()
	b, err := io.ReadAll(io.LimitReader(f, 8193))
	if err != nil || len(b) > 8192 {
		return "", 0, rejected
	}
	// comm is parenthesized and can itself contain spaces or parentheses.
	close := strings.LastIndexByte(string(b), ')')
	if close < 0 {
		return "", 0, rejected
	}
	fields := strings.Fields(string(b[close+1:]))
	if len(fields) < 3 {
		return "", 0, rejected
	}
	pgid, err := strconv.Atoi(fields[2])
	if err != nil {
		return "", 0, rejected
	}
	return fields[0], pgid, nil
}
