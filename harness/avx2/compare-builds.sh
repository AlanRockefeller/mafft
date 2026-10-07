#!/bin/bash
# Compare two MAFFT builds for byte-identical output, single-threaded.
#
#   harness/avx2/compare-builds.sh REF_MAFFT NEW_MAFFT input.fasta [input.fasta ...]
#
# REF_MAFFT / NEW_MAFFT are installed `mafft` scripts (each finds its own
# libexec).  Every input is aligned with each option set below; any difference,
# or an empty result from the new build, is reported and the exit status is 1.
#
# For the AllocateCharMtxNoZero change, build NEW with -DMAFFT_POISON_TEST as
# well: the rows that are no longer cleared are then filled with junk, so a read
# of a byte that was never written shows up as a difference.
set -u
REF=$1 NEW=$2; shift 2
OPTS=( "--auto" "--localpair --maxiterate 1000" "--globalpair --maxiterate 1000"
       "--maxiterate 2" "--retree 2 --maxiterate 0" "--adjustdirection --auto" )
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
fail=0
for f in "$@"; do
  for o in "${OPTS[@]}"; do
    # shellcheck disable=SC2086
    env -u MAFFT_BINARIES "$REF" --thread 1 --quiet $o "$f" > "$tmp/ref" 2>/dev/null
    # shellcheck disable=SC2086
    env -u MAFFT_BINARIES "$NEW" --thread 1 --quiet $o "$f" > "$tmp/new" 2>/dev/null
    if [ -s "$tmp/new" ] && cmp -s "$tmp/ref" "$tmp/new"; then r=same; else r=DIFFERENT; fail=1; fi
    printf '%-9s %-32s %s\n' "$r" "$o" "$f"
  done
done
exit $fail
