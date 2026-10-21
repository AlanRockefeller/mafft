# Deploy the audited Dikarya4 build

`deploy.py` installs the already validated **7.526-opt8-dikarya4** binary bundle.
It uses Python 3.10+ and the studied Linux x86-64 AVX2 VM, without a compiler,
network access or additional Python packages. It verifies the pinned build,
installed-file, corpus and golden-manifest hashes before making changes.

Choose the **actual MAFFT command configured in the production pipeline**.
The research records use `/opt/mafft-7.526-opt8/bin/mafft` and some historical
opt5 paths; `command -v mafft` currently finds the distribution installation.
The command below uses the recorded opt8 path explicitly.

From the `mafft-round2` worktree, preview the deployment:

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

Default artifacts are the sibling `round2/dikarya4-r2-build`,
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

Every input runs with A (`--thread 1`) and F (`--thread 2 --threadit 1`), keeping
its recorded strategy and direction flags. For a full 200-job check, add
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
- A lone request with both CPUs free: `--thread 2 --threadit 1` is the measured
  latency option preserving the tested one-thread outputs.
- Include downstream tree searches in the same two-CPU budget.

`--threadit 0` was repeatable in the study but changed some alignments relative
to A. Ordinary `--thread 2` refinement was nondeterministic. The
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
