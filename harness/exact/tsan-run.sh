#!/bin/bash
# tsan-run.sh MAFFT OUTDIR INPUT... : run each input under ThreadSanitizer with
# L-INS-i, G-INS-i and E-INS-i at --thread 2 and 4 (and the FFT-NS-i and
# all-pairs threading the same runs exercise).  Build MAFFT with
#   CFLAGS="-O1 -g -fsanitize=thread -march=native -ffp-contract=off" STRIP=true
# Reports go to OUTDIR/<case>/tsan.<pid>; triage with tsan_triage.py.
# setarch -R: gcc 13 TSan aborts ("unexpected memory mapping") under the
# 32-bit mmap randomization of recent Ubuntu kernels.
set -u
mafft=$1 out=$2; shift 2
mkdir -p "$out"
for input in "$@"; do
  for strategy in "--localpair --maxiterate 1000" "--globalpair --maxiterate 1000" \
                  "--genafpair --maxiterate 1000 --ep 0" "--maxiterate 1000"; do
    for t in 2 4; do
      name=$(basename "$input" .fa).$(echo $strategy | tr -d ' -').t$t
      mkdir -p "$out/$name"
      TSAN_OPTIONS="log_path=$out/$name/tsan halt_on_error=0 exitcode=0 report_signal_unsafe=0 history_size=4" \
        timeout 3600 setarch "$(uname -m)" -R "$mafft" --thread $t $strategy "$input" \
        > "$out/$name/out.fa" 2> "$out/$name/stderr"
      echo "$name exit=$? reports=$(cat "$out/$name"/tsan.* 2>/dev/null | grep -c 'WARNING: ThreadSanitizer')"
    done
  done
done
