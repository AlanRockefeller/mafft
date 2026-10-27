#!/usr/bin/env python3
"""Prepare/deduplicate/execute IQ-TREE searches from MAFFT runs.jsonl.

All alignment replicates are considered unless --replicates is supplied.
--controls-seeds adds distinct searches on ONE fixed A alignment per dataset.
Create OUTPUT/PAUSE to pause safely between jobs. --plan-only runs no inference.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import threading
import time

from analysis import make_mapping, read_alignment, sha256_file, write_json
from tree import inference_version, prepare_tree_job, run_tree_job


def digest_object(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_status(output, value):
    temporary = output / "STATUS.tmp.json"
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    temporary.replace(output / "STATUS.json")


def create_queue(args):
    log_paths = [Path(p).resolve() for p in args.alignments]
    iqtree = shutil.which(args.iqtree)
    if iqtree is None:
        raise ValueError(f"Cannot find IQ-TREE: {args.iqtree}")
    iqtree = str(Path(iqtree).resolve())
    metadata = {"schema": "mafft-round2-tree-queue-v1",
                "alignment_logs": [{"path": str(p), "sha256": sha256_file(p)} for p in log_paths],
                "data": str(Path(args.data).resolve()), "configs": args.configs,
                "files": sorted(args.files) if args.files else None,
                "replicates": args.replicates, "seeds": args.seeds,
                "controls_seeds": args.controls_seeds, "controls_files": args.controls_files,
                "iqtree": iqtree, "binary_sha256": sha256_file(iqtree),
                "iqtree_version": inference_version(iqtree), "fast": args.fast,
                "support": args.support, "mem": args.mem, "nstop": args.nstop,
                "model": "GTR+F+G4", "threads": 1}
    queue_path = args.output / "queue.json"
    if queue_path.exists():
        queue = json.loads(queue_path.read_text())
        if queue["metadata"] != metadata:
            raise ValueError("Queue provenance/options changed; use a new output directory")
        return queue
    args.output.mkdir(parents=True, exist_ok=False)
    runs = []
    seen = {}
    for path in log_paths:
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("returncode", 0) or row.get("completed") is False:
                continue
            if args.files and row["input"] not in args.files:
                continue
            if row["config"] not in args.configs:
                continue
            if args.replicates is not None and row["replicate"] not in args.replicates:
                continue
            key = (row["input_sha256"], row["config"], row["replicate"])
            if key in seen:
                if seen[key]["output_sha256"] != row["output_sha256"]:
                    raise ValueError(f"Conflicting same input/config/replicate in logs: {key}")
                continue
            row = dict(row, source_log=str(path), source_line=lineno)
            seen[key] = row
            runs.append(row)
    if not runs:
        raise ValueError("No successful alignment records matched the queue selection")
    runs.sort(key=lambda r: (r["input"], r["config"], r["replicate"]))
    requests = [(r, seed, "alignment_replicate") for r in runs for seed in args.seeds]
    controls = {}
    for row in runs:
        if row["config"] == "A" and (not args.controls_files or row["input"] in args.controls_files):
            controls.setdefault(row["input_sha256"], row)
    for row in controls.values():
        requests.extend((row, seed, "fixed_alignment_search_control") for seed in args.controls_seeds if seed not in args.seeds)
    if args.controls_seeds and not controls:
        raise ValueError("Search controls requested but no selected A alignment exists")
    mappings = {}
    normalized = {}
    jobs = {}
    records = []
    for row, seed, reason in requests:
        input_digest = row["input_sha256"]
        if input_digest not in mappings:
            mapping = make_mapping(Path(args.data) / row["input"])
            if mapping["input_sha256"] != input_digest:
                raise ValueError(f"Original input hash changed: {row['input']}")
            mappings[input_digest] = mapping
        mapping = mappings[input_digest]
        source = str(Path(row["alignment"]).resolve())
        if source not in normalized:
            if sha256_file(source) != row["output_sha256"]:
                raise ValueError(f"Alignment hash changed: {source}")
            alignment = read_alignment(source, mapping)
            named = "".join(f">{label}\n{seq}\n" for label, seq in zip(alignment.ids, alignment.sequences))
            normalized[source] = hashlib.sha256(named.encode()).hexdigest()
        options = {k: metadata[k] for k in ["binary_sha256", "fast", "support", "mem", "nstop", "model", "threads"]}
        key = digest_object({"input_sha256": input_digest, "named_alignment_sha256": normalized[source],
                             "seed": seed, "options": options})
        if key not in jobs:
            plan = prepare_tree_job(alignment=source, out_dir=args.output / "jobs" / key,
                                    seed=seed, mapping=mapping, iqtree=iqtree,
                                    fast=args.fast, support=args.support, mem=args.mem, nstop=args.nstop)
            if plan["named_alignment_sha256"] != normalized[source]:
                raise ValueError("Normalized alignment hash disagrees with prepared file")
            jobs[key] = plan
        records.append({"input": row["input"], "input_sha256": input_digest,
                        "config": row["config"], "replicate": row["replicate"], "seed": seed,
                        "reason": reason, "source_alignment": source,
                        "source_alignment_sha256": row["output_sha256"],
                        "named_alignment_sha256": normalized[source],
                        "source_log": row["source_log"], "source_line": row["source_line"],
                        "job_key": key, "directory": jobs[key]["directory"],
                        "requested_id": digest_object([input_digest, row["config"], row["replicate"], seed, reason, key])})
    queue = {"metadata": metadata, "requests": records, "jobs": jobs,
             "created_utc": datetime.now(timezone.utc).isoformat(),
             "requested_searches": len(records), "unique_searches": len(jobs),
             "deduplication_rule": "Same original input hash, normalized stable-ID alignment hash, seed, executable hash, full/fast/support/memory/search options. Reused requests are not independent searches."}
    write_json(queue_path, queue)
    return queue


def execute_queue(queue, args):
    output = args.output
    logpath = output / "runs.jsonl"
    done = {}
    if logpath.exists():
        for line in logpath.read_text().splitlines():
            record = json.loads(line)
            # A recorded tree must still be the tree that was recorded.
            if record.get("completed") and (not record.get("tree") or not Path(record["tree"]).exists()
                                            or sha256_file(record["tree"]) != record["tree_sha256"]):
                raise ValueError(f"Recorded tree missing or changed: {record['requested_id']}")
            done[record["requested_id"]] = record
    requests_by_job = defaultdict(list)
    for request in queue["requests"]:
        requests_by_job[request["job_key"]].append(request)
    failed_jobs = 0
    # One job at a time. Dictionary insertion order preserves deterministic preparation order.
    with logpath.open("a", buffering=1) as log:
        for number, (key, plan) in enumerate(queue["jobs"].items(), 1):
            if all(r["requested_id"] in done for r in requests_by_job[key]):
                continue
            while (output / "PAUSE").exists():
                write_status(output, {"paused": True, "completed_requests": len(done),
                                      "total_requests": len(queue["requests"]), "next_job": key})
                time.sleep(.5)
            write_status(output, {"paused": False, "completed_requests": len(done),
                                  "total_requests": len(queue["requests"]), "running_job": key,
                                  "unique_job_index": number, "unique_jobs": len(queue["jobs"])})
            directory = Path(plan["directory"])
            if (directory / "run.json").exists():
                result = json.loads((directory / "run.json").read_text())
                if result["plan"] != plan:
                    raise ValueError(f"Job provenance changed: {directory}")
                if result.get("completed") and sha256_file(result["tree"]) != result["tree_sha256"]:
                    raise ValueError(f"Completed tree hash changed: {directory}")
            elif (directory / "started.json").exists():
                raise ValueError(f"Interrupted started job lacks run.json; preserve it and retry in a new queue: {directory}")
            else:
                result = run_tree_job(plan, timeout=args.timeout, lock=args.lock)
            failed_jobs += not result["completed"]
            first = requests_by_job[key][0]
            for request in requests_by_job[key]:
                if request["requested_id"] in done:
                    continue
                record = dict(request, completed=result["completed"], tree=result["tree"],
                              tree_sha256=result["tree_sha256"], elapsed_seconds=result["elapsed_seconds"],
                              resources=result["resources"], run_json=str(directory / "run.json"),
                              fast=plan.get("fast", False), support=plan["support"],
                              search_method=plan.get("search_method", "IQ-TREE default full search"),
                              reused=request["requested_id"] != first["requested_id"],
                              original_request_id=first["requested_id"],
                              inference_counts_as_independent=request["requested_id"] == first["requested_id"])
                log.write(json.dumps(record, sort_keys=True) + "\n")
                done[record["requested_id"]] = record
            print(f"{number}/{len(queue['jobs'])} {first['input']} {first['config']} r{first['replicate']} seed{first['seed']} "
                  f"complete={result['completed']} wall={result['elapsed_seconds']:.3f}s requests={len(requests_by_job[key])}", flush=True)
            if not result["completed"] and not args.continue_on_error:
                write_status(output, {"finished": False, "failed_job": key, "completed_requests": len(done)})
                return False
    write_status(output, {"finished": True, "paused": False, "completed_requests": len(done),
                          "total_requests": len(queue["requests"]), "failed_jobs_this_invocation": failed_jobs,
                          "unique_jobs": len(queue["jobs"])})
    return not any(not r["completed"] for r in done.values())


def execute_parallel_queue(queue, args):
    """Precompute disjoint jobs, then let the serial writer adopt their results.

    Each IQ-TREE still has -T 1. Only its own directory is written by a worker.
    The established serial recovery/alias writer creates the canonical log.
    """
    workers = args.workers
    if workers not in (1, 2):
        raise ValueError('Only one or two independent searches may run at once')
    pending = []
    for key, plan in queue['jobs'].items():
        directory = Path(plan['directory'])
        if (directory/'run.json').exists():
            result = json.loads((directory/'run.json').read_text())
            if result['plan'] != plan or (result['completed'] and sha256_file(result['tree']) != result['tree_sha256']):
                raise ValueError('Changed completed search: ' + str(directory))
            if not result['completed'] and not args.continue_on_error:
                raise ValueError('Previous failed search requires review or an explicit continue-on-error: ' + str(directory))
        elif (directory/'started.json').exists():
            raise ValueError('Interrupted started job; preserve and use a new queue: ' + str(directory))
        else:
            pending.append((key, plan))
    attempt = args.output / ('execution-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '.json')
    write_json(attempt, {'workers': workers, 'inference_threads_per_job': 1,
                         'argv': sys.argv,
                         'queue_sha256': sha256_file(args.output/'queue.json'),
                         'runner_sha256': sha256_file(__file__), 'pending_jobs': len(pending),
                         'timing_scope': 'Independent tree searches may overlap. Their wall times are descriptive; no alignment performance benchmark runs concurrently.'})
    lock = threading.Lock()
    active, completed = set(), set()
    stop = threading.Event()
    def worker():
        while not stop.is_set():
            with lock:
                if not pending:
                    return
                if (args.output/'PAUSE').exists():
                    job = None
                else:
                    job = pending.pop(0)
                    active.add(job[0])
            if job is None:
                time.sleep(.5)
                continue
            key, plan = job
            try:
                result = run_tree_job(plan, timeout=args.timeout, lock=args.lock)
                if not result['completed'] and not args.continue_on_error:
                    stop.set()
            except BaseException:
                stop.set()
                raise
            finally:
                with lock:
                    active.discard(key)
                    completed.add(key)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker) for _ in range(workers)]
        while not all(f.done() for f in futures):
            with lock:
                state = {'finished': False, 'workers': workers, 'inference_threads_per_job': 1,
                         'paused': (args.output/'PAUSE').exists(), 'running_jobs': sorted(active),
                         'new_jobs_finished': len(completed), 'pending_jobs': len(pending),
                         'unique_jobs': len(queue['jobs'])}
            write_status(args.output, state)
            wait(futures, timeout=.5)
        for future in futures:
            future.result()
    # Failed searches and aliases retain the same exit/recovery policy as serial.
    return execute_queue(queue, args)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--alignments", nargs="+", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--configs", nargs="+", default=["A", "B", "C"])
    ap.add_argument("--files", nargs="+")
    ap.add_argument("--replicates", nargs="+", type=int, help="Default all; use --replicates 0 for broad screening")
    ap.add_argument("--seeds", nargs="+", type=int, default=[73111])
    ap.add_argument("--controls-seeds", nargs="+", type=int, default=[])
    ap.add_argument("--controls-files", nargs="+")
    ap.add_argument("--iqtree", default="/usr/bin/iqtree2")
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--support", action="store_true")
    ap.add_argument("--mem", default="6G")
    ap.add_argument("--nstop", type=int)
    ap.add_argument("--timeout", type=float, default=3600)
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--continue-on-error", action="store_true")
    ap.add_argument("--workers", type=int, choices=[1, 2], default=1,
                    help="Independent searches at once; IQ-TREE itself always uses one thread")
    ap.add_argument("--lock")
    args = ap.parse_args()
    if len(set(args.seeds)) != len(args.seeds) or len(set(args.controls_seeds)) != len(args.controls_seeds):
        ap.error("Seeds within each seed list must be unique")
    if any(seed <= 0 for seed in args.seeds + args.controls_seeds):
        ap.error("Seeds must be positive")
    args.output = args.output.resolve()
    queue = create_queue(args)
    print(json.dumps({"queue": str(args.output / "queue.json"),
                      "requested_searches": queue["requested_searches"], "unique_searches": queue["unique_searches"]}), flush=True)
    executor = execute_queue if args.workers == 1 else execute_parallel_queue
    if not args.plan_only and not executor(queue, args):
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        sys.exit(str(exc))
