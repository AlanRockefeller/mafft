#!/bin/bash
# Build stock MAFFT 7.526 (0a2319b) and this working tree with the same compiler
# and flags, then compare their output byte for byte (harness/exact/matrix.py).
#
#   harness/verify-against-stock.sh [--full] [--cc gcc] [--cflags "-O3 ..."]
#       [--candidate-cflags "..."] [--stock-cflags "..."] [--no-test-build]
#       [--make-args "STRIP=true"] [--work DIR] [--jobs N] [-- extra matrix.py arguments]
#
# Defaults: gcc, -O3 -march=native -ffp-contract=off (the AVX2 build on an
# AVX2-only CPU; use -march=broadwell or -march=haswell on an AVX-512 CPU to test
# the AVX2 paths).  --candidate-cflags is added to the candidate only, for
# example -DMAFFT_STOCK_FMA=1 for the clang FMA class.  The fast matrix takes a
# few minutes; --full takes hours.  --make-args is passed to both make commands of
# every build (STRIP=true keeps symbols for sanitizer reports).  Exit status 0
# only if every case matched.
#
# Nothing is installed outside --work (default: a new temporary directory).
# Stock builds are cached in $MAFFT_STOCK_CACHE (if set) by compiler and flags.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/.." && pwd)
stock_rev=0a2319b41ec99282487c2d758029cb7ef1fbc5c2
cc=gcc
cflags="-O3 -march=native -ffp-contract=off"
cand_extra=""
stock_extra=""
full=""
test_build=1
work=""
jobs=$(( $(nproc 2>/dev/null || echo 1) ))
make_args=""
extra=()
while [ $# -gt 0 ]; do
  case $1 in
    --full) full=--full ;;
    --cc) cc=$2; shift ;;
    --cflags) cflags=$2; shift ;;
    --candidate-cflags) cand_extra=$2; shift ;;
    --stock-cflags) stock_extra=$2; shift ;;
    --make-args) make_args=$2; shift ;;
    --no-test-build) test_build="" ;;
    --work) work=$2; shift ;;
    --jobs) jobs=$2; shift ;;
    --) shift; extra=("$@"); break ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$work" ] || work=$(mktemp -d "${TMPDIR:-/tmp}/mafft-verify.XXXXXX")
mkdir -p "$work"
work=$(cd "$work" && pwd -P)  # build() runs make in $src/core, and the launcher embeds PREFIX
echo "work directory: $work"

# build NAME SOURCE-COMMAND CFLAGS [PREFIX]: copy core/ and scripts/ into $work/src/NAME,
# build and install into PREFIX (default $work/inst/NAME; never a system prefix).  The
# launcher embeds PREFIX, so an installation cannot be moved afterwards.
build() {
  local name=$1 kind=$2 flags=$3 src=$work/src/$1 prefix=${4:-$work/inst/$1}
  rm -rf "$src" "$prefix"
  mkdir -p "$src/binaries" "$src/scripts"
  if [ "$kind" = stock ]; then
    git -C "$repo" archive "$stock_rev" core scripts | tar xf - -C "$src"
  else
    (cd "$repo" && git ls-files -z --cached --others --exclude-standard core scripts | xargs -0 tar cf -) |
      tar xf - -C "$src"
  fi
  echo "building $name: CC=$cc CFLAGS=$flags"
  (cd "$src/core" &&
     make -j"$jobs" CC="$cc" CFLAGS="$flags" PREFIX="$prefix" $make_args >../build.log 2>&1 &&
     make install CC="$cc" CFLAGS="$flags" PREFIX="$prefix" $make_args >>../build.log 2>&1) ||
    { tail -30 "$src/build.log"; echo "build of $name failed" >&2; exit 2; }
}

stock_flags="$cflags${stock_extra:+ $stock_extra}"
cand_flags="$cflags${cand_extra:+ $cand_extra}"
stock_prefix=$work/inst/stock
if [ -n "${MAFFT_STOCK_CACHE:-}" ]; then
  # -march=native names no CPU, so key on what it resolves to: the predefined target macros.
  target=""
  case " $stock_flags " in
    *" -march=native "*|*" -mcpu=native "*)
      target=$("$cc" $stock_flags -E -dM -x c /dev/null | LC_ALL=C sort | sha256sum) ;;
  esac
  tag=$(printf '%s\n' "$stock_rev" "$cc" "$("$cc" --version | head -1)" "$stock_flags" "$make_args" ${target:+"$target"} |
          sha256sum | cut -c1-16)
  mkdir -p "$MAFFT_STOCK_CACHE"
  stock_prefix=$(cd "$MAFFT_STOCK_CACHE" && pwd -P)/stock-$tag
fi
# A cached stock build is installed at its final path (the launcher embeds it) and is used
# only once .complete exists, so an interrupted build is rebuilt rather than reused.
if [ -x "$stock_prefix/bin/mafft" ] && [ -e "$stock_prefix/.complete" ]; then
  echo "using cached stock build $stock_prefix"
else
  mkdir -p "$(dirname "$stock_prefix")"
  build stock stock "$stock_flags" "$stock_prefix"
  touch "$stock_prefix/.complete"
fi
build candidate tree "$cand_flags"
test_args=()
if [ -n "$test_build" ]; then
  build candidate-test tree "$cand_flags -DMAFFT_ORDERED_TEST"
  test_args=(--test-build "$work/inst/candidate-test/bin/mafft")
fi

echo "stock:     $("$stock_prefix/bin/mafft" --version 2>&1)"
echo "candidate: $("$work/inst/candidate/bin/mafft" --version 2>&1)"
exec python3 "$here/exact/matrix.py" --stock "$stock_prefix/bin/mafft" \
  --candidate "$work/inst/candidate/bin/mafft" "${test_args[@]}" \
  --out "$work/matrix" --jobs "$jobs" $full "${extra[@]}"
