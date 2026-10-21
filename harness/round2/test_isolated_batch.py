"""Synthetic records only: no compiler, subprocess, MAFFT or timing workload."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import alignments
import isolated_batch as batch
import quiet
import throughput
from summarize_scheduling import aggregate, stats, write_table
from test_summarize_scheduling import Fixture, digest, save


def cpu_snapshot(when, ticks):
    return dict(available=True, monotonic=when, busy_ticks=ticks,
                steal_ticks=0, tick_hz=100, cpu_count=2)


def synthetic_batch(name='a.fasta', policy='one_job', rnd=0, index=0):
    """GNU centisecond totals flag this batch; waited-child totals do not."""
    origin = 100 + index*20
    runs, completed = [], 0.0
    for rep in range(batch.REPLICATES):
        service = .39 + rnd*.01 + rep*.001
        before = cpu_snapshot(origin+completed, 10000+index*960+rep*47)
        completed += service
        after = cpu_snapshot(origin+completed, before['busy_ticks']+47)
        usage = {'user': .37, 'system': .01, 'rss_kib': 1024+rep, 'exit': 0}
        cpu = usage['user'] + usage['system']
        runs.append(dict(input=name, policy=policy, round=rnd, replicate=rep,
                         record_id=batch.record_id(name, policy, rnd, rep),
                         config=throughput.POLICIES[policy][0], slot=0,
                         returncode=0, timed_out=False, resources_error=None, resources=usage, cpu=cpu,
                         queue_seconds=0.0, response_seconds=service, service_seconds=service,
                         completed_since_policy_seconds=completed, matches_A=True,
                         cpu_stat_before=before, cpu_stat_after=after,
                         contention=quiet.busy_delta(before, after, cpu, max_busy_cores=batch.CEILING)))
    gnu = sum(r['cpu'] for r in runs)
    before, after = cpu_snapshot(origin, 10000+index*960), cpu_snapshot(origin+9, 10960+index*960)
    child_before = dict(available=True, pid=4401, user=50+index*8, system=10+index,
                        monotonic=origin+.001)
    child_after = dict(available=True, pid=4401, user=child_before['user']+7.63,
                       system=child_before['system']+.27, monotonic=origin+8.999)
    delta = batch.children_delta(child_before, child_after, gnu)
    summary = dict(schema=batch.SCHEMA, policy=policy, round=rnd,
                   config=throughput.POLICIES[policy][0], concurrency=1,
                   jobs=batch.REPLICATES, native_repeats_per_round=batch.REPLICATES,
                   native_repeats_per_input=batch.REPLICATES*2, paired_rounds=2,
                   selection_contention_source=batch.SELECTION_FIELD,
                   makespan_seconds=9.0, jobs_per_hour=batch.REPLICATES*3600/9,
                   cpu_total_seconds=gnu, cpu_per_job_seconds=gnu/batch.REPLICATES,
                   child_cpu_total_seconds=delta['cpu_seconds'],
                   child_cpu_per_job_seconds=delta['cpu_seconds']/batch.REPLICATES,
                   rusage_children_before=child_before, rusage_children_after=child_after,
                   rusage_children_delta=delta, cpu_stat_before=before, cpu_stat_after=after,
                   contention=quiet.busy_delta(before, after, gnu, max_busy_cores=batch.CEILING),
                   all_children_reaped=True, queue_order=[name]*batch.REPLICATES,
                   matches_A=batch.REPLICATES, max_per_job_rss_mib=max(r['resources']['rss_kib'] for r in runs)/1024,
                   peak_sampled_process_tree_rss_mib=2.0,
                   quiet_window=dict(ready=True, enabled=True))
    summary[batch.SELECTION_FIELD] = batch.selection_contention(before, after, delta)
    for key, field in [('service_latency_seconds', 'service_seconds'), ('queue_seconds', 'queue_seconds'),
                       ('response_latency_seconds', 'response_seconds')]:
        summary[key] = stats([r[field] for r in runs])
    return summary, runs


class BatchFixture(Fixture):
    """One selected 40-run short input plus the unchanged two-run long input."""
    def __init__(self, root):
        super().__init__(root)
        old = json.loads((root/'isolated-a.fasta/experiment.json').read_text())
        self.eligibility_root = root/'frozen-corpus'; self.eligibility_root.mkdir()
        build = {k: old[k] for k in ('binary', 'launcher_sha256', 'version', 'installed_file_sha256')}
        build['name'] = 'candidate'
        self.plan = dict(manifest_sha256=digest(self.manifest), files=self.files, builds=[build],
                         rounds=1, cpu='0', data=str(self.data),
                         golden_sha256={f['file']: digest(self.golden/f['file']) for f in self.files})
        self.frozen_rows = []
        for f, wall in zip(self.files, [.415, 1.0, 1.5]):
            golden = self.plan['golden_sha256'][f['file']]
            self.frozen_rows.append(dict(build='candidate', input=f['file'], round=0, threads=1,
                                        input_sha256=f['sha256'], returncode=0, timed_out=False,
                                        resources_error=None, identical=True, wall=wall,
                                        golden_sha256=golden, output_sha256=golden,
                                        argv=['nice', '-n', '10', 'taskset', '-c', '0', str(self.binary),
                                              '--thread', '1', *f['production_options'], str(self.data/f['file'])]))
        save(self.eligibility_root/'experiment.json', self.plan)
        self.frozen_runs = self.eligibility_root/'runs.jsonl'
        self.frozen_runs.write_text(''.join(json.dumps(row)+'\n' for row in self.frozen_rows))
        self.batch_root = root/'batch-a'; self.batch_root.mkdir()
        retained = self.batch_root/'harness'; retained.mkdir()
        harness = {}
        for module in (batch, throughput, quiet, alignments):
            source = Path(module.__file__).resolve()
            copy = retained/source.name; shutil.copyfile(source, copy)
            harness[module.__name__] = dict(path=str(source), retained_copy=str(copy), sha256=digest(copy))
        settings = dict(enabled=True, max_busy_cores=.2, max_scientific_cores=.05)
        old.update(schema=batch.SCHEMA, protocol=batch.SCHEMA, selected_policies=list(batch.POLICIES),
                   rounds=2, paired_rounds=2, native_repeats_per_round=20, native_repeats_per_input=40,
                   policy_order_by_round=[batch.policy_order(r) for r in range(2)],
                   selection_contention_source=batch.SELECTION_FIELD, quiet_window=settings,
                   priority=dict(driver_at_measurement=10, gnu_time_inherits_driver_nice=10,
                                 native_nice_increment=10, expected_native_nice=19),
                   gnu_time_sha256=digest(Path('/usr/bin/time')), harness_source_at_startup=harness,
                   eligibility=dict(runs=str(self.frozen_runs), experiment=str(self.eligibility_root/'experiment.json'),
                                    full_corpus_runs_sha256=digest(self.frozen_runs),
                                    full_corpus_experiment_sha256=digest(self.eligibility_root/'experiment.json'),
                                    build='candidate', metric='mean A wall across all frozen corpus rounds',
                                    strict_threshold_seconds=1.0, input_mean_A_wall_seconds=.415,
                                    eligible_short_inputs=['a.fasta']))
        save(self.batch_root/'experiment.json', old)
        record = self.files[0]
        for rnd in range(2):
            for position, policy in enumerate(batch.policy_order(rnd)):
                out = self.batch_root/f'r{rnd}'/policy; out.mkdir(parents=True)
                s, runs = synthetic_batch(policy=policy, rnd=rnd, index=rnd*4+position)
                for r in runs:
                    dest = out/f"replicate-{r['replicate']:03d}"/'a.fasta'; dest.mkdir(parents=True)
                    alignment = dest/'alignment.fasta'
                    config = throughput.POLICIES[policy][0]
                    extra = '\n'*(1+rnd+r['replicate']%2) if config == 'B' else '\n' if config == 'C' else ''
                    alignment.write_text((self.golden/'a.fasta').read_text()+extra)
                    stderr = dest/'stderr.txt'; stderr.write_text('')
                    save(dest/'resources.json', r['resources'])
                    r.update(input_sha256=record['sha256'], threads=int(batch.CONFIGS[config][1]),
                             recorded_production_threads=record['production_threads'],
                             alignment=str(alignment), output_sha256=digest(alignment), stderr_sha256=digest(stderr),
                             golden_sha256=old['golden_sha256']['a.fasta'],
                             matches_A=digest(alignment) == old['golden_sha256']['a.fasta'],
                             argv=['nice', '-n', '10', 'taskset', '-c', '0,1', str(self.binary),
                                   *batch.CONFIGS[config], *record['production_options'], str(self.data/'a.fasta')])
                s['matches_A'] = sum(r['matches_A'] for r in runs)
                s['quiet_window']['settings'] = settings
                save(out/'summary.json', s); save(out/'quiet-window.json', s['quiet_window'])
                (out/'runs.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in runs))
        save(self.batch_root/'STATUS.json', dict(finished=True, accepted=True, completed_batches=8))
        self.isolated_rows[0]['output'] = 'batch-a'; self.ledgers()


class IsolatedBatchTests(unittest.TestCase):
    def test_precision_field_preserves_gnu_flags_and_never_subtracts_observer(self):
        s, runs = synthetic_batch()
        batch.validate_batch_summary(s, runs, 'a.fasta', 'one_job', 0)
        self.assertTrue(s['contention']['contention_flag'])
        self.assertFalse(s[batch.SELECTION_FIELD]['contention_flag'])
        self.assertAlmostEqual(s['cpu_total_seconds'], 7.6)
        self.assertAlmostEqual(s['child_cpu_total_seconds'], 7.9)
        before = deepcopy(s[batch.SELECTION_FIELD])
        s['observer_cpu_seconds'] = 1000  # Diagnostic value cannot influence acceptance.
        self.assertEqual(before, batch.selection_contention(s['cpu_stat_before'], s['cpu_stat_after'], s['rusage_children_delta']))
        self.assertEqual(sum(r['contention']['contention_flag'] for r in runs), 20)

    def test_invalid_child_deltas_fail_closed(self):
        s, _ = synthetic_batch()
        for field, value in [('pid', 1), ('user', -1), ('monotonic', 0), ('available', False)]:
            with self.subTest(field=field):
                after = dict(s['rusage_children_after'], **{field: value})
                delta = batch.children_delta(s['rusage_children_before'], after, 7.6)
                self.assertFalse(delta['available'])
                selected = batch.selection_contention(s['cpu_stat_before'], s['cpu_stat_after'], delta)
                self.assertFalse(selected['available']); self.assertIsNone(selected['contention_flag'])

    def test_group_invariants_reject_missing_duplicate_reordered_or_changed_raw_records(self):
        for failure in ['missing', 'duplicate_id', 'reordered', 'raw_flag', 'nonsequential', 'bool_index']:
            with self.subTest(failure=failure):
                s, runs = synthetic_batch()
                if failure == 'missing': runs.pop()
                elif failure == 'duplicate_id': runs[-1]['record_id'] = runs[0]['record_id']
                elif failure == 'reordered': runs.reverse()
                elif failure == 'raw_flag': runs[0]['contention']['contention_flag'] = False
                elif failure == 'nonsequential': runs[0]['queue_seconds'] = .1
                else: runs[0]['replicate'] = False
                with self.assertRaises(ValueError):
                    batch.validate_batch_summary(s, runs, 'a.fasta', 'one_job', 0)

    def test_complete_clean_set_requires_both_rounds_and_all_four_policies(self):
        summaries = [synthetic_batch(policy=p, rnd=r)[0] for r in range(2) for p in batch.POLICIES]
        self.assertTrue(batch.clean_batch_set(summaries))
        self.assertFalse(batch.clean_batch_set(summaries[:-1]))
        self.assertFalse(batch.clean_batch_set(summaries[:-1]+[summaries[0]]))
        summaries[-1][batch.SELECTION_FIELD]['contention_flag'] = True
        self.assertFalse(batch.clean_batch_set(summaries))

    def test_priority_change_is_confined_to_own_new_driver(self):
        with patch.object(batch.os, 'getpriority', side_effect=[0, 10]), patch.object(batch.os, 'setpriority') as change:
            self.assertEqual(batch.driver_priority()['driver_at_measurement'], 10)
            change.assert_called_once_with(batch.os.PRIO_PROCESS, 0, 10)
        with patch.object(batch.os, 'getpriority', return_value=10), patch.object(batch.os, 'setpriority') as change:
            batch.driver_priority(); change.assert_not_called()
        with patch.object(batch.os, 'getpriority', return_value=19), patch.object(batch.os, 'setpriority') as change:
            with self.assertRaisesRegex(ValueError, 'inherited niceness'):
                batch.driver_priority()
            change.assert_not_called()

    def test_mixed_protocol_uses_equal_input_weights_and_honest_native_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BatchFixture(Path(temporary)); result = aggregate(fixture.args())
            self.assertEqual(result['schema'], 'mafft-round2-scheduling-summary-v2')
            self.assertEqual(result['native_repeats_per_input'], {'a.fasta': 40, 'b.fasta': 2})
            self.assertEqual({tuple(row) for row in result['isolated_per_input']}, {tuple(result['isolated_per_input'][0])})
            self.assertTrue(all(row['paired_rounds'] == 2 for row in result['isolated_per_input']))
            self.assertTrue(all(row['measured_runs'] == 42 for row in result['isolated_by_config']))
            a = next(row for row in result['isolated_by_config'] if row['config'] == 'A')
            self.assertAlmostEqual(a['median_file_mean_latency_seconds'], (.4045+1.1)/2)
            self.assertEqual(a['matches_A'], 42)
            short = next(row for row in result['isolated_per_input'] if row['input'] == 'a.fasta')
            long = next(row for row in result['isolated_per_input'] if row['input'] == 'b.fasta')
            self.assertAlmostEqual(short['mean_cpu_seconds'], .38)
            self.assertAlmostEqual(short['mean_child_tree_cpu_seconds'], .395)
            self.assertIsNone(long['mean_child_tree_cpu_seconds'])
            self.assertEqual(short['serial_jobs_flagged'], 40)
            self.assertIn(str(fixture.frozen_runs), result['source_and_result_sha256'])
            write_table(fixture.root/'mixed.tsv', result['isolated_per_input'])

    def test_eligibility_is_strict_frozen_complete_and_command_specific(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BatchFixture(Path(temporary))
            manifest = {'files': fixture.files}
            _, _, eligible = batch.eligibility(manifest, fixture.plan, fixture.frozen_rows, 'candidate')
            self.assertEqual(eligible, ['a.fasta'])  # copy.fasta is exactly 1 second.
            for failure in ['missing', 'duplicate', 'two_threads', 'command', 'duplicate_plan_file', 'not_exact']:
                with self.subTest(failure=failure):
                    plan, rows = deepcopy(fixture.plan), deepcopy(fixture.frozen_rows)
                    if failure == 'missing': rows.pop()
                    elif failure == 'duplicate': rows.append(deepcopy(rows[0]))
                    elif failure == 'two_threads': rows[0]['threads'] = 2
                    elif failure == 'command': rows[0]['argv'].append('--retree')
                    elif failure == 'duplicate_plan_file': plan['files'].append(deepcopy(plan['files'][0]))
                    else: rows[0]['identical'] = False
                    with self.assertRaises(ValueError):
                        batch.eligibility(manifest, plan, rows, 'candidate')

    def test_frozen_provenance_and_unmodified_original_fields_are_enforced(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BatchFixture(Path(temporary))
            paths = [fixture.frozen_runs, fixture.batch_root/'harness/isolated_batch.py',
                     fixture.batch_root/'r0/one_job/summary.json']
            for path in paths:
                with self.subTest(path=path.name):
                    original = path.read_bytes()
                    try:
                        if path.name == 'summary.json':
                            changed = json.loads(original); changed['contention']['contention_flag'] = False
                            save(path, changed)
                        else: path.write_bytes(original+b'\n')
                        with self.assertRaises(ValueError):
                            aggregate(fixture.args())
                    finally: path.write_bytes(original)

    def test_changed_rotation_or_incomplete_set_is_never_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = BatchFixture(Path(temporary))
            for failure in ['rounds', 'priority', 'ceiling', 'rotation', 'complete']:
                with self.subTest(failure=failure):
                    path = fixture.batch_root/('STATUS.json' if failure == 'complete' else 'experiment.json')
                    original = path.read_bytes(); data = json.loads(original)
                    if failure == 'rounds': data['native_repeats_per_input'] = 2
                    elif failure == 'priority': data['priority']['driver_at_measurement'] = 19
                    elif failure == 'ceiling': data['quiet_window']['max_busy_cores'] = .25
                    elif failure == 'rotation': data['policy_order_by_round'][1].reverse()
                    else: data['accepted'] = False
                    try:
                        save(path, data)
                        with self.assertRaises(ValueError): aggregate(fixture.args())
                    finally: path.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
