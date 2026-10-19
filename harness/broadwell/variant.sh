#!/bin/sh
# Reuse unchanged objects for controlled experiments. Never overwrite an existing prefix.
# Run from the repository root: sh harness/broadwell/variant.sh PREFIX CFLAGS OBJECT...
# All flags affecting unchanged objects must match the initial full build.
set -eu
prefix=$1
flags=$2
shift 2
case "$prefix" in /*) ;; *) echo 'prefix must be absolute' >&2; exit 2;; esac
if test -e "$prefix"; then echo "prefix already exists: $prefix" >&2; exit 2; fi
for object in "$@"; do
    nice -n 10 taskset -c 1 make -C core -W "${object%.o}.c" "$object" CC=gcc CFLAGS="$flags"
done
make -C core -W mafft.tmpl mafft PREFIX="$prefix"
nice -n 10 taskset -c 1 make -C core install CC=gcc CFLAGS="$flags" PREFIX="$prefix" STRIP=true
printf 'prefix=%s\nflags=%s\n' "$prefix" "$flags"
