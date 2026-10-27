# Deploy the audited opt9 build

> **Status (checked 2026-10-27):** dikarya5 is still the production build. The public command
> `/opt/mafft-7.526-opt8/bin/mafft` was switched to
> `/opt/mafft-releases/mafft-7.526-opt8-dikarya5-c81eb9794e14` on 2026-10-22 at 05:59 UTC
> (activation link `.mafft-release-20261022T055923Z-…`; dikarya4's is from 2026-10-21 at
> 23:58 UTC), and Dikarya's job logs report `Version 7.526-opt8-dikarya5` from that day on.
> The deployment records under `/opt/mafft-releases/deployments/` are readable only by root;
> rollback to dikarya4 uses the record of the 2026-10-22 transaction, as described
> [below](#rollback). The opt9 bundle is built, checked and pinned here, and a full
> rehearsal passed, but it has not been applied; see [how it was made](#the-opt9-bundle).

This script deployed dikarya4 to Dikarya production on 2026-10-21 and dikarya5 on
2026-10-22, and is now pinned to the **7.526-opt9** bundle (source tag `v7.526-opt9`). To
deploy dikarya5 again, use this script as of commit `307a46f` (source tag
`v7.526-opt8-dikarya5`); for dikarya4, as of commit `8933faa` (tag `v7.526-opt8-dikarya4`).

`deploy.py` installs the already validated **7.526-opt9** binary bundle.
It uses Python 3.10+ and the studied Linux x86-64 AVX2 VM, without a compiler,
network access or additional Python packages. It verifies the pinned build,
installed-file, corpus and golden-manifest hashes before making changes.

Choose the **actual MAFFT command configured in the production pipeline**.
The research records use `/opt/mafft-7.526-opt8/bin/mafft` and some historical
opt5 paths; `command -v mafft` currently finds the distribution installation.
The command below uses the recorded opt8 path explicitly.

From a worktree of this repository, preview the deployment:

```sh
python3 harness/round2/deploy.py deploy \
  --entrypoint /opt/mafft-7.526-opt8/bin/mafft
```

The default is a read-only dry run: no directories, locks, validation jobs or
production changes. It prints the selected path, its current file/link state,
release destination, checksums and validation plan. `--dry-run` is also accepted.

To execute that deployment, with write permission for the chosen paths:

```sh
sudo python3 harness/round2/deploy.py deploy \
  --entrypoint /opt/mafft-7.526-opt8/bin/mafft --apply
```

Default artifacts are the sibling `round2/opt9-build`,
`bench/production-corpus.json`, `bench/data`, `bench/golden/production-t1` and
`bench/golden.sha256`. Override their locations with `--build`, `--corpus`,
`--data`, `--golden` and `--golden-manifest`. The bundle needs its `build.json`,
`binary-sha256.json`, `install/` and the 90 native source files under `source/`;
compiled objects and research logs are unnecessary. Internal absolute aliases
are remapped to the bundle's current location, so copying the bundle does not
require its original research installation to remain available.

The default release root is `/opt/mafft-releases`; change it with
`--releases-dir`. The public command must be named `mafft`; strategy aliases
such as `linsi` have different defaults and are not activation targets.

## Activation and validation

The script copies all 57 verified installation files into a versioned release,
dereferencing aliases into independent files. Small launch wrappers pin
`MAFFT_BINARIES` to that release's helper directory. This avoids dependencies on
the private benchmark path and prevents an inherited helper override from
mixing versions. Arguments pass through to MAFFT, including explicit thread,
strategy, direction, placement and output-format options.
Existing strategy aliases that link to the public `mafft` command retain their
defaults. The release also supplies wrappers for the installed aliases, helper
commands and Ruby drivers; the Ruby wrappers select this release's MAFFT rather
than their upstream hardcoded `/usr/local/bin/mafft` default. Ruby and BLAST are
still required for the respective optional drivers.

The payload and wrappers are made read-only, then published at a stable versioned
path. Before switching the public command, the script checks the exact version
and byte-compares real golden outputs on four representative inputs:

| Input | Strategy exercised |
|---|---|
| `1msv` | Direction adjustment and L-INS-i |
| `buh4` | Direction adjustment and FFT-NS-2 via `--auto` |
| `t4lr` | Direction adjustment and FFT-NS-i via `--auto` |
| `icv1` | Accurate direction adjustment and the recorded noniterative strategy |

Every input runs with A (`--thread 1`) and F (`--thread 2 --threadit 1`), and L-INS-i
inputs (`--localpair`) also with O (plain `--thread 2`, ordered refinement), keeping
its recorded strategy and direction flags. FFT-NS-i keeps stock two-thread refinement,
so O is not compared with the golden there. For a full 200-job check, add
`--validation full`. These are sequential correctness checks; their durations
are not performance measurements. `--timeout` sets a per-command limit,
defaulting to 600 seconds. A timed-out/interrupted validation kills only its
own process group and retains the command and output logs.

After validation, one atomic symlink replacement activates the release through
a unique retained `.mafft-release-TRANSACTION` link beside the public command.
This identifies the activation even if the same release is installed again.
A version check and one real A alignment through the public command must then
pass. A post-activation failure automatically restores the previous command.
Running jobs retain their old launcher and helper paths; old installations and
releases are retained. Deployment uses nonblocking release and command locks,
and checks that the command has not changed during validation.

Reapplying an already active, intact release verifies its files and wrappers
and returns a no-op. It creates no new rollback transaction and does not rerun
the alignment checks. A changed payload, wrapper or recorded executable mode
causes a failure.

## Rollback

Each attempted activation prints a `deployment.json` path before validation,
under `/opt/mafft-releases/deployments/`. It records the original regular file,
symlink target or absence. Regular-file bytes, permissions and ownership are
saved. Command logs and `result.json` record validation outcomes. Successful
deployment also prints the exact rollback command.
Before validation, the script reconstructs a temporary original command beside
the public path to verify it has permission to restore the original ownership
and mode. A failed probe prevents activation.

Preview or execute rollback using that retained record:

```sh
python3 harness/round2/deploy.py rollback \
  --deployment /opt/mafft-releases/deployments/TRANSACTION/deployment.json

sudo python3 harness/round2/deploy.py rollback \
  --deployment /opt/mafft-releases/deployments/TRANSACTION/deployment.json --apply
```

Rollback checks the backup hash and restores the prior file/link/absence
atomically. It refuses to overwrite a newer deployment or a manual change.
Rollback is idempotent. Old helper installations must remain available for
their restored launchers. SIGKILL or a power interruption can leave a complete
validated release active; the durable transaction record supplies the rollback.
Retain the hidden activation links, deployment records and old releases while
rollback to them may be needed. The script performs no garbage collection.

## Pipeline settings

Deployment passes caller arguments through. Configure the scheduler separately:

- Backlog with two available CPUs: two independent `--thread 1` jobs.
- A lone request with both CPUs free: plain `--thread 2`. From dikarya5 its L-INS-i
  refinement commits in one-worker order and gives the one-thread output, about 1.26x
  faster than `--thread 2 --threadit 1`
  ([round-3 report](../../paper/round3-ordered-refinement.md)). FFT-NS-i (`--auto` on
  some inputs) still refines in completion order with two threads.
- Include downstream tree searches in the same two-CPU budget.

`--threadit 0` was repeatable in the study but changed some alignments relative
to A. Through dikarya4, ordinary `--thread 2` refinement was nondeterministic. The
[performance report](../../paper/round2-performance.md) records the measured
benefits and their limits; the
[phylogenetic study](../../paper/round2-phylogenetics.md) does not rank biological
accuracy between threading settings.

Run the deployment regression tests with:

```sh
cd harness/round2
python3 -m unittest test_deploy -v
```

The tests use disposable fake installations and exercise dry runs, relocation,
file/link/absent rollback, failed pre/post checks, competing changes, locks,
corrupt backups, helper pinning and timeout cleanup. Production paths are never
used by the tests.

## The opt9 bundle

opt9 changes no alignment output that Dikarya uses: the round-2 corpus goldens and stock
`--thread 1` output are unchanged, and the only stdout change, the CLUSTAL header (`v7.526`
instead of `7.526-opt8`), affects `--clustalout`, which Dikarya does not use. Done on
2026-10-27 on the Dikarya VM, at nice 19:

1. **Release commit.** PR #3 was merged into `exact-speedups` (`c961b51`); `9d391cf` dates the
   launcher `v7.526-opt9 (2026/Oct/27)` and is tagged `v7.526-opt9`.
2. **Bundle.** From a clean archive of the tag (`git archive v7.526-opt9 | tar -x -C "$src"`):
   `python3 harness/round2/build.py --source "$src" --output round2/opt9-build --jobs 2`
   (GCC 13.3, `-O3 -march=native -ffp-contract=off -g`, the dikarya5 bundle's flags). 57
   installed files; `build.json` sha256 `a45a927d…`, `binary-sha256.json` sha256 `c8ade5a0…`.
3. **Check against stock.** `harness/verify-against-stock.sh --full --cflags "-O3
   -march=native -ffp-contract=off"` on the tag: 675/675 cases identical to stock 7.526
   (`0a2319b`, same flags); two inputs are rejected with the same nonzero exit by both builds.
4. **Pin the script.** `deploy.py` pins `SOURCE_COMMIT` `9d391cf`, `VERSION`
   `v7.526-opt9 (2026/Oct/27)`, the two bundle hashes and `RELEASE_NAME`
   `mafft-7.526-opt9-c8ade5a0b948`; the corpus and golden hashes are unchanged.
   `python3 -m unittest test_deploy`: 27 tests OK.
5. **Rehearsal.** A real `deploy --apply --validation full` into a scratch release root and
   entry point: activated and verified, 298 golden checks (all 100 corpus inputs with A and
   F, the 97 L-INS-i inputs with O, and the post-activation run), all byte-identical to
   `bench/golden/production-t1`.
6. **Apply** (needs root; not yet run), then keep the printed rollback command:

   ```sh
   sudo python3 harness/round2/deploy.py deploy \
     --entrypoint /opt/mafft-7.526-opt8/bin/mafft --apply
   ```

   The default smoke validation reruns 1msv, buh4, t4lr and icv1 before the switch; add
   `--validation full` to rerun the whole corpus there (about 30 minutes).
