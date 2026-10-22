#!/usr/bin/env python3
"""Test deduplication/provenance without executing any inference program."""
import argparse
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from analysis import read_alignment, sha256_file, write_named_alignment
from queue_trees import create_queue, execute_queue, execute_parallel_queue


class QueueTests(unittest.TestCase):
    def test_two_independent_workers_adopt_each_job_once_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs, requests = {}, []
            for number in range(3):
                job = root / str(number); job.mkdir()
                jobs[str(number)] = {'directory': str(job), 'support': False}
                for alias in range(2):
                    requests.append({'job_key': str(number), 'requested_id': f'{number}-{alias}',
                                     'input': 'fixture', 'config': 'A' if not alias else 'C',
                                     'replicate': 0, 'seed': 73111})
            queue = {'jobs': jobs, 'requests': requests}
            (root/'queue.json').write_text(json.dumps(queue))
            args = argparse.Namespace(output=root, workers=2, timeout=1, lock=None, continue_on_error=False)
            barrier = threading.Barrier(2); lock = threading.Lock()
            counts = {'calls': 0, 'active': 0, 'peak': 0}
            def run(plan, **_):
                with lock:
                    counts['calls'] += 1; index = counts['calls']
                    counts['active'] += 1
                    counts['peak'] = max(counts['peak'], counts['active'])
                if index <= 2:
                    barrier.wait(timeout=2)
                job = Path(plan['directory']); tree = job/'inference.treefile'
                tree.write_text('((a,b),(c,d));')
                result = {'plan': plan, 'completed': True, 'tree': str(tree),
                          'tree_sha256': sha256_file(tree), 'elapsed_seconds': .1, 'resources': None}
                (job/'run.json').write_text(json.dumps(result))
                with lock: counts['active'] -= 1
                return result
            with patch('queue_trees.run_tree_job', side_effect=run) as runner:
                self.assertTrue(execute_parallel_queue(queue, args))
                self.assertTrue(execute_parallel_queue(queue, args))
                self.assertEqual(runner.call_count, 3)
            self.assertEqual(counts['peak'], 2)
            records = [json.loads(line) for line in (root/'runs.jsonl').read_text().splitlines()]
            self.assertEqual(len(records), 6)
            self.assertEqual(sum(r['inference_counts_as_independent'] for r in records), 3)
            self.assertTrue(json.loads((root/'STATUS.json').read_text())['finished'])

    def test_identical_named_alignments_reuse_but_seeds_and_search_method_do_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fixture.fasta"
            source.write_text(">first\nAC\n>second\nAG\n>third\nAT\n>fourth\nAA\n")
            a, c = root / "a.fasta", root / "c.fasta"
            a.write_text(">second\nAG-\n>first\nAC-\n>fourth\nAA-\n>third\nAT-\n")
            c.write_text(">first\nAC-\n>second\nA-G\n>third\nAT-\n>fourth\nAA-\n")
            binary = root / "fake-iqtree"
            binary.write_text("#!/bin/sh\nexit 0\n")
            binary.chmod(0o755)
            rows = []
            for config, replicate in [("A", 0), ("A", 1), ("B", 0), ("B", 1), ("C", 0)]:
                alignment = c if config == "C" else a
                rows.append({"input": source.name, "input_sha256": sha256_file(source),
                             "config": config, "replicate": replicate, "returncode": 0,
                             "alignment": str(alignment), "output_sha256": sha256_file(alignment)})
            log = root / "alignments.jsonl"
            log.write_text("".join(json.dumps(r) + "\n" for r in rows))
            def prepare(**kwargs):
                out = Path(kwargs["out_dir"])
                out.mkdir(parents=True)
                alignment = read_alignment(kwargs["alignment"], kwargs["mapping"])
                write_named_alignment(alignment, out / "named.fasta")
                return {"directory": str(out), "named_alignment_sha256": sha256_file(out / "named.fasta"),
                        "seed": kwargs["seed"], "fast": kwargs["fast"], "support": kwargs["support"]}
            args = argparse.Namespace(alignments=[str(log)], iqtree=str(binary), data=str(root),
                                      configs=["A", "B", "C"], files=None, replicates=None,
                                      seeds=[73111], controls_seeds=[73129], controls_files=None,
                                      output=root / "queue", fast=False, support=False, mem="6G",
                                      nstop=None, timeout=10, lock=None, continue_on_error=False)
            with patch("queue_trees.inference_version", return_value="fixture"), patch("queue_trees.prepare_tree_job", side_effect=prepare):
                queue = create_queue(args)
                self.assertEqual(queue["requested_searches"], 6)
                self.assertEqual(queue["unique_searches"], 3)
                self.assertEqual(len({r["job_key"] for r in queue["requests"] if r["seed"] == 73111}), 2)
                self.assertEqual(len({r["job_key"] for r in queue["requests"] if r["config"] == "A"}), 2)
                self.assertEqual(create_queue(args), queue)
                args.output = root / "fast-queue"
                args.fast = True
                fast = create_queue(args)
                self.assertFalse(set(queue["jobs"]) & set(fast["jobs"]))
            args.output = root / "queue"
            def run(plan, **_kwargs):
                return {"completed": True, "tree": str(Path(plan["directory"]) / "fixture.treefile"),
                        "tree_sha256": "fixture-hash", "elapsed_seconds": .1, "resources": None}
            with patch("queue_trees.run_tree_job", side_effect=run) as executor:
                self.assertTrue(execute_queue(queue, args))
                self.assertEqual(executor.call_count, 3)
                self.assertTrue(execute_queue(queue, args))
                self.assertEqual(executor.call_count, 3)
            results = [json.loads(line) for line in (args.output / "runs.jsonl").read_text().splitlines()]
            self.assertEqual(len(results), 6)
            self.assertEqual(sum(r["inference_counts_as_independent"] for r in results), 3)
            self.assertEqual(sum(r["reused"] for r in results), 3)


if __name__ == "__main__":
    unittest.main()
