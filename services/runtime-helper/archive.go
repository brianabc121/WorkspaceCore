//go:build linux

package main

import (
	"archive/tar"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path"
	"sort"
	"strings"
	"syscall"
)

const archiveMetadataLimit = 16 << 20

func archiveBounds(q request) (int64, int, error) {
	max := q.MaxBytes
	if max == 0 {
		max = 5 << 30
	}
	files := q.MaxFiles
	if files == 0 {
		files = archiveFiles
	}
	if max < 1024 || max > archiveLimit || files < 1 || files > archiveFiles {
		return 0, 0, rejected
	}
	return max, files, nil
}

type limitedWriter struct {
	w    io.Writer
	left int64
}

func (w *limitedWriter) Write(b []byte) (int, error) {
	if int64(len(b)) > w.left {
		return 0, rejected
	}
	n, err := w.w.Write(b)
	w.left -= int64(n)
	return n, err
}
func snapshot(fs *tree, q request, out io.Writer) error {
	max, maxFiles, err := archiveBounds(q)
	if err != nil {
		return rejected
	}
	roots := q.Paths
	if len(roots) == 0 {
		roots = []string{"."}
	}
	if len(roots) > 1000 {
		return rejected
	}
	selected := []string{}
	for _, p := range roots {
		p, err = clean(p, true)
		if err != nil {
			return rejected
		}
		skip := false
		for _, old := range selected {
			if p == old || old == "." || strings.HasPrefix(p, old+"/") {
				skip = true
				break
			}
		}
		if !skip {
			selected = append(selected, p)
		}
	}
	// Remove nested selections when their ancestor appeared later.
	roots = nil
	for i, p := range selected {
		nested := false
		for j, old := range selected {
			if i != j && (old == "." || strings.HasPrefix(p, old+"/")) {
				nested = true
				break
			}
		}
		if !nested {
			roots = append(roots, p)
		}
	}
	bounded := &limitedWriter{out, max}
	tw := tar.NewWriter(bounded)
	count := 0
	counted := map[string]bool{}
	countPath := func(name string) error {
		for {
			if !counted[name] {
				counted[name] = true
				count++
			}
			if name == "." {
				break
			}
			name = path.Dir(name)
		}
		if count > maxFiles {
			return rejected
		}
		return nil
	}
	var expanded int64
	metadata := 0
	var walk func(string) error
	walk = func(name string) error {
		var f *os.File
		var st os.FileInfo
		if name == "." {
			f, err = fs.directory(".", false)
			if err == nil {
				st, err = f.Stat()
			}
		} else {
			dir, base, e := fs.parent(name, false)
			if e != nil {
				return e
			}
			anchor, s, e := metadataAt(dir, base)
			dir.Close()
			if e != nil {
				return e
			}
			st = s
			if s.IsDir() {
				anchor.Close()
				f, err = fs.directory(name, false)
			} else if s.Mode().IsRegular() {
				anchor.Close()
				f, err = fs.read(name)
			} else {
				anchor.Close()
				return rejected
			}
			if err == nil {
				st, err = f.Stat()
			}
		}
		if err != nil {
			return err
		}
		defer f.Close()
		if err = countPath(name); err != nil {
			return err
		}
		metadata += len(name)
		if count > maxFiles || metadata > archiveMetadataLimit {
			return rejected
		}
		h := &tar.Header{Name: name, Mode: int64(st.Mode().Perm()), Typeflag: tar.TypeReg, Size: st.Size(), Format: tar.FormatPAX}
		if st.IsDir() {
			h.Typeflag = tar.TypeDir
			h.Size = 0
		} else {
			if st.Size() < 0 || st.Size() > max-expanded {
				return rejected
			}
			expanded += st.Size()
		}
		if err = tw.WriteHeader(h); err != nil {
			return err
		}
		if !st.IsDir() {
			_, err = io.CopyN(tw, f, st.Size())
			return err
		}
		for {
			names, e := f.Readdirnames(1)
			if e == io.EOF {
				break
			}
			if e != nil {
				return e
			}
			if e = walk(path.Join(name, names[0])); e != nil {
				return e
			}
		}
		return nil
	}
	// Every archive has a single workspace root, including a selection of files.
	if len(roots) == 1 && roots[0] == "." {
		if err = walk("."); err != nil {
			return rejected
		}
	} else {
		if err = countPath("."); err != nil {
			return rejected
		}
		if count > maxFiles {
			return rejected
		}
		rootInfo, e := fs.root.Stat()
		if e != nil {
			return rejected
		}
		if err = tw.WriteHeader(&tar.Header{Name: ".", Mode: int64(rootInfo.Mode().Perm()), Typeflag: tar.TypeDir, Format: tar.FormatPAX}); err != nil {
			return rejected
		}
		for _, p := range roots {
			if err = walk(p); err != nil {
				if errors.Is(err, syscall.ENOENT) {
					continue
				}
				return rejected
			}
		}
	}
	if err = tw.Close(); err != nil {
		return rejected
	}
	return nil
}
func temporary(staging string) (*os.File, error) {
	fs, err := openTree(staging)
	if err != nil {
		return nil, err
	}
	defer fs.Close()
	name, err := randomName()
	if err != nil {
		return nil, err
	}
	fd, err := syscall.Openat(int(fs.root.Fd()), name, syscall.O_RDWR|syscall.O_CREAT|syscall.O_EXCL|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0600)
	if err != nil {
		return nil, err
	}
	f := os.NewFile(uintptr(fd), name)
	if err = syscall.Unlinkat(int(fs.root.Fd()), name); err != nil {
		f.Close()
		return nil, err
	}
	return f, nil
}
func restore(c config, q request, in io.Reader, out io.Writer, recheck func() error) error {
	max, maxFiles, err := archiveBounds(q)
	if err != nil || q.StreamBytes < 1024 || q.StreamBytes > max || q.StreamBytes%512 != 0 {
		return rejected
	}
	spool, err := temporary(c.staging)
	if err != nil {
		return rejected
	}
	defer spool.Close()
	if _, err = io.CopyN(spool, in, q.StreamBytes); err != nil {
		return rejected
	}
	var extra [1]byte
	if n, e := in.Read(extra[:]); n != 0 || e != io.EOF {
		return rejected
	}
	if _, err = spool.Seek(0, io.SeekStart); err != nil {
		return rejected
	}
	if err = validateArchive(spool, max, maxFiles); err != nil {
		return rejected
	}
	if err = recheck(); err != nil {
		return rejected
	}
	// No workspace entry was opened or created until the complete stream passed.
	if _, err = spool.Seek(0, io.SeekStart); err != nil {
		return rejected
	}
	fs, err := openTree(c.workspace)
	if err != nil {
		return rejected
	}
	defer fs.Close()
	if names, e := fs.root.Readdirnames(1); len(names) != 0 || e != io.EOF {
		return rejected
	}
	tr := tar.NewReader(spool)
	type directoryMode struct {
		path string
		mode os.FileMode
	}
	modes := []directoryMode{}
	for {
		h, e := tr.Next()
		if e == io.EOF {
			break
		}
		if e != nil {
			return rejected
		}
		name, e := archivePath(h)
		if e != nil {
			return rejected
		}
		if h.Typeflag == tar.TypeDir {
			modes = append(modes, directoryMode{name, os.FileMode(h.Mode) & 0777})
			dir, e := fs.directory(name, true)
			if e != nil {
				return rejected
			}
			dir.Close()
		} else {
			if e = fs.write(name, io.LimitReader(tr, h.Size), h.Size, os.FileMode(h.Mode)&0777); e != nil {
				return rejected
			}
		}
	}
	sort.Slice(modes, func(i, j int) bool { return len(modes[i].path) > len(modes[j].path) })
	for _, m := range modes {
		dir, e := fs.directory(m.path, false)
		if e != nil {
			return rejected
		}
		e = dir.Chmod(m.mode)
		dir.Close()
		if e != nil {
			return rejected
		}
	}
	return json.NewEncoder(out).Encode(map[string]any{})
}
func archivePath(h *tar.Header) (string, error) {
	name := h.Name
	if h.Typeflag == tar.TypeDir {
		name = strings.TrimSuffix(name, "/")
	}
	name, err := clean(name, h.Typeflag == tar.TypeDir)
	if err != nil {
		return "", err
	}
	if h.Typeflag != tar.TypeDir && h.Typeflag != tar.TypeReg && h.Typeflag != tar.TypeRegA {
		return "", rejected
	}
	if h.Linkname != "" || h.Size < 0 || h.Typeflag == tar.TypeDir && h.Size != 0 {
		return "", rejected
	}
	// Sparse encodings can expand data beyond physical stream bytes. Reject all
	// vendor/sparse PAX extensions; only portable name/size/time fields are accepted.
	for k := range h.PAXRecords {
		switch k {
		case "path", "size", "mtime", "atime", "ctime", "uid", "gid", "uname", "gname":
		default:
			return "", rejected
		}
	}
	return name, nil
}
func validateArchive(r io.Reader, max int64, maxFiles int) error {
	tr := tar.NewReader(r)
	seen := map[string]byte{}
	requiredDirs := map[string]bool{}
	nodes := map[string]bool{".": true}
	count := 0
	metadata := 0
	var expanded int64
	for {
		h, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return rejected
		}
		name, err := archivePath(h)
		if err != nil {
			return rejected
		}
		count++
		nodes[name] = true
		metadata += len(name)
		if count > maxFiles || metadata > archiveMetadataLimit {
			return rejected
		}
		if _, exists := seen[name]; exists {
			return rejected
		}
		for parent := path.Dir(name); parent != "."; parent = path.Dir(parent) {
			requiredDirs[parent] = true
			nodes[parent] = true
			if typ, exists := seen[parent]; exists && typ != tar.TypeDir {
				return rejected
			}
		}
		if len(nodes) > maxFiles {
			return rejected
		}
		if h.Typeflag != tar.TypeDir && requiredDirs[name] {
			return rejected
		}
		seen[name] = h.Typeflag
		if h.Size > max-expanded {
			return rejected
		}
		expanded += h.Size
		if _, err = io.Copy(io.Discard, tr); err != nil {
			return rejected
		}
	}
	// tar.Reader permits arbitrary trailing data. Accept only zero block padding.
	var b [32 << 10]byte
	for {
		n, err := r.Read(b[:])
		for _, v := range b[:n] {
			if v != 0 {
				return rejected
			}
		}
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return rejected
		}
	}
}
