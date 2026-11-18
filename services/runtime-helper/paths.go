//go:build linux

package main

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path"
	"sort"
	"strings"
	"syscall"
)

const oPath = 0x200000 // Linux O_PATH; opens metadata without opening devices/FIFOs.
type tree struct{ root *os.File }

func openTree(name string) (*tree, error) {
	fd, err := syscall.Open(name, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	return &tree{os.NewFile(uintptr(fd), name)}, nil
}
func (t *tree) Close() error { return t.root.Close() }
func clean(name string, root bool) (string, error) {
	if name == "" && root {
		return ".", nil
	}
	if len(name) > 4096 || name == "" || strings.HasPrefix(name, "/") || strings.ContainsRune(name, 0) {
		return "", rejected
	}
	for _, s := range strings.Split(name, "/") {
		if s == ".." || s == "" {
			return "", rejected
		}
	}
	name = path.Clean(name)
	if name == "." && !root {
		return "", rejected
	}
	return name, nil
}
func (t *tree) directory(name string, create bool) (*os.File, error) {
	name, err := clean(name, true)
	if err != nil {
		return nil, err
	}
	fd, err := syscall.Dup(int(t.root.Fd()))
	if err != nil {
		return nil, err
	}
	syscall.CloseOnExec(fd)
	if name != "." {
		for _, part := range strings.Split(name, "/") {
			next, e := syscall.Openat(fd, part, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
			if e == syscall.ENOENT && create {
				if e = syscall.Mkdirat(fd, part, 0700); e == nil || e == syscall.EEXIST {
					next, e = syscall.Openat(fd, part, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
				}
			}
			syscall.Close(fd)
			if e != nil {
				return nil, e
			}
			fd = next
		}
	}
	return os.NewFile(uintptr(fd), name), nil
}
func (t *tree) parent(name string, create bool) (*os.File, string, error) {
	name, err := clean(name, false)
	if err != nil {
		return nil, "", err
	}
	dir, err := t.directory(path.Dir(name), create)
	return dir, path.Base(name), err
}
func metadataAt(dir *os.File, name string) (*os.File, os.FileInfo, error) {
	fd, err := syscall.Openat(int(dir.Fd()), name, oPath|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, nil, err
	}
	f := os.NewFile(uintptr(fd), name)
	st, err := f.Stat()
	if err != nil {
		f.Close()
		return nil, nil, err
	}
	if st.Mode()&os.ModeSymlink != 0 {
		f.Close()
		return nil, nil, rejected
	}
	return f, st, nil
}
func (t *tree) read(name string) (*os.File, error) {
	dir, base, err := t.parent(name, false)
	if err != nil {
		return nil, err
	}
	defer dir.Close()
	anchor, st, err := metadataAt(dir, base)
	if err != nil {
		return nil, err
	}
	defer anchor.Close()
	if !st.Mode().IsRegular() {
		return nil, rejected
	}
	// Reopen the pinned regular inode, never a second untrusted path lookup.
	return os.Open(fmt.Sprintf("/proc/self/fd/%d", anchor.Fd()))
}
func randomName() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	return ".runtime-" + hex.EncodeToString(b[:]), nil
}
func (t *tree) write(name string, r io.Reader, size int64, mode os.FileMode) error {
	dir, base, err := t.parent(name, true)
	if err != nil {
		return err
	}
	defer dir.Close()
	if f, st, e := metadataAt(dir, base); e == nil {
		f.Close()
		if !st.Mode().IsRegular() {
			return rejected
		}
	} else if e != syscall.ENOENT {
		return e
	}
	tmp, err := randomName()
	if err != nil {
		return err
	}
	fd, err := syscall.Openat(int(dir.Fd()), tmp, syscall.O_WRONLY|syscall.O_CREAT|syscall.O_EXCL|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0600)
	if err != nil {
		return err
	}
	f := os.NewFile(uintptr(fd), tmp)
	defer func() { f.Close(); syscall.Unlinkat(int(dir.Fd()), tmp) }()
	n, err := io.Copy(f, r)
	if err != nil || size >= 0 && n != size {
		return rejected
	}
	if err = f.Chmod(mode); err != nil {
		return err
	}
	if err = f.Sync(); err != nil {
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	// Rename replaces a concurrently inserted symlink itself; it never follows it.
	if err = syscall.Renameat(int(dir.Fd()), tmp, int(dir.Fd()), base); err != nil {
		return err
	}
	return dir.Sync()
}

type entry struct {
	Path string `json:"path"`
	Kind string `json:"kind"`
	Size int64  `json:"size"`
}

func (t *tree) files(q request) (any, error) {
	switch q.Kind {
	case "put_files":
		if len(q.Files) > 1000 {
			return nil, rejected
		}
		var total int64
		seen := map[string]bool{}
		for _, f := range q.Files {
			p, err := clean(f.Path, false)
			if err != nil || seen[p] {
				return nil, rejected
			}
			seen[p] = true
			total += int64(len(f.B64))
			if total > fileLimit {
				return nil, rejected
			}
		}
		for _, f := range q.Files {
			if err := t.write(f.Path, strings.NewReader(string(f.B64)), int64(len(f.B64)), 0600); err != nil {
				return nil, err
			}
		}
		return map[string]any{"size": total}, nil
	case "get_files":
		max := int64(fileLimit)
		if q.Limit != 0 {
			if q.Limit < 0 || q.Limit > max {
				return nil, rejected
			}
			max = q.Limit
		}
		f, err := t.read(q.Path)
		if err != nil {
			return nil, err
		}
		defer f.Close()
		st, err := f.Stat()
		if err != nil || st.Size() > max {
			return nil, rejected
		}
		b, err := io.ReadAll(io.LimitReader(f, max+1))
		if err != nil || int64(len(b)) > max {
			return nil, rejected
		}
		return map[string]any{"b64": b, "size": len(b)}, nil
	case "list_files":
		name, err := clean(q.Path, true)
		if err != nil {
			return nil, err
		}
		entries := []entry{}
		var walk func(string, int) error
		walk = func(p string, depth int) error {
			dir, err := t.directory(p, false)
			if err != nil {
				return err
			}
			defer dir.Close()
			for len(entries) < listLimit {
				names, err := dir.Readdirnames(1)
				if err == io.EOF {
					break
				}
				if err != nil {
					return err
				}
				n := names[0]
				anchor, st, err := metadataAt(dir, n)
				if err != nil {
					return err
				}
				anchor.Close()
				kind := "file"
				if st.IsDir() {
					kind = "dir"
				} else if !st.Mode().IsRegular() {
					return rejected
				}
				next := path.Join(p, n)
				entries = append(entries, entry{next, kind, st.Size()})
				if kind == "dir" && depth < 3 {
					if err = walk(next, depth+1); err != nil {
						return err
					}
				}
			}
			return nil
		}
		if err = walk(name, 1); err != nil {
			return nil, err
		}
		sort.Slice(entries, func(i, j int) bool { return entries[i].Path < entries[j].Path })
		return map[string]any{"entries": entries}, nil
	}
	return nil, rejected
}
func (t *tree) inject(secrets map[string]string) error {
	if len(secrets) > 1000 {
		return rejected
	}
	total := 0
	for k, v := range secrets {
		if len(k) == 0 || len(k) > 128 {
			return rejected
		}
		for _, c := range k {
			if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_' || c == '-' || c == '.') {
				return rejected
			}
		}
		if k == "." || k == ".." {
			return rejected
		}
		total += len(v)
		if total > fileLimit {
			return rejected
		}
	}
	for k, v := range secrets {
		if err := t.write(k, strings.NewReader(v), int64(len(v)), 0400); err != nil {
			return err
		}
	}
	return nil
}
