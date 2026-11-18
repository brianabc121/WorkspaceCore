#!/bin/sh
set -eu
# This script intentionally downloads nothing and uses no builder image.
if [ "$#" -ne 1 ]; then
    echo 'usage: ./build.sh /absolute/path/to/verified/go' >&2
    exit 2
fi
compiler=$1
case "$compiler" in /*) ;; *) echo 'compiler must be an absolute path' >&2; exit 2 ;; esac
if [ "$("$compiler" version)" != 'go version go1.27.4 linux/amd64' ]; then
    echo 'verified Go 1.27.4 linux/amd64 is required' >&2
    exit 2
fi
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
mkdir -p .build
env GOENV=off GOTOOLCHAIN=local GOOS=linux GOARCH=amd64 CGO_ENABLED=0 \
    GOPROXY=off GOSUMDB=off GOFLAGS= GOEXPERIMENT= \
    "$compiler" build -buildvcs=false -trimpath -ldflags='-s -w -buildid=' -o .build/helper .
