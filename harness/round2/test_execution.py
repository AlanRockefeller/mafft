#!/usr/bin/env python3
"""Mocked timeout/recovery/protocol fixtures; no MAFFT or tree inference."""
import argparse
from copy import deepcopy
import io
import json
from pathlib import Path
import signal
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock, call

import alignments
import analyze_trees
from analysis import sha256_file
from run_plan import execute_plan


class AlignmentExecutionTests(unittest.TestCase):
    def test_timeout_kills_entire_group_even_when_leader_exits_on_term(self):
        process=Mock(pid=12345)
        process.wait.side_effect=[subprocess.TimeoutExpired(['fixture'],.01),-15,-15]
        with patch('alignments.subprocess.Popen',return_value=process) as popen, patch('alignments.os.killpg') as kill:
            result=alignments.run_process_group(['fixture'],stdout=io.BytesIO(),stderr=io.BytesIO(),env={},timeout=.01)
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        self.assertEqual(kill.call_args_list,[call(12345,signal.SIGTERM),call(12345,signal.SIGKILL)])
        self.assertTrue(result['timed_out'])
        self.assertEqual(result['returncode'],-15)
        self.assertEqual(result['signals_sent'],['SIGTERM','SIGKILL'])

    def test_ignoring_term_escalates_after_grace(self):
        process=Mock(pid=12345)
        process.wait.side_effect=[subprocess.TimeoutExpired(['fixture'],.01),subprocess.TimeoutExpired(['fixture'],5),-9]
        with patch('alignments.subprocess.Popen',return_value=process), patch('alignments.os.killpg') as kill:
            result=alignments.run_process_group(['fixture'],stdout=io.BytesIO(),stderr=io.BytesIO(),env={},timeout=.01)
        self.assertEqual(result['returncode'],-9)
        self.assertEqual(kill.call_args_list[-1],call(12345,signal.SIGKILL))

    def test_success_never_signals_group(self):
        process=Mock(pid=12345)
        process.wait.return_value=0
        with patch('alignments.subprocess.Popen',return_value=process), patch('alignments.os.killpg') as kill:
            result=alignments.run_process_group(['fixture'],stdout=io.BytesIO(),stderr=io.BytesIO(),env={},timeout=1)
        kill.assert_not_called()
        self.assertFalse(result['timed_out'])

    def test_prefixed_or_missing_resource_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'resources.json'
            self.assertIsNone(alignments.read_resources(path)[0])
            path.write_text('Command exited with non-zero status 3\n{"user":0.2,"system":0.1,"rss_kib":100,"exit":3}\n')
            values,error=alignments.read_resources(path)
            self.assertIsNone(error)
            self.assertEqual(values['exit'],3)
            path.write_text('Command terminated by signal 15\n')
            self.assertIsNone(alignments.read_resources(path)[0])

    def test_timeout_is_logged_without_fabricated_cpu_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source=root/'fixture.fasta';source.write_text('>a\nACGT\n')
            (root/'prefix/bin').mkdir(parents=True);(root/'prefix/libexec/mafft').mkdir(parents=True)
            binary=root/'prefix/bin/mafft';binary.write_text('fixture')
            manifest=root/'manifest.json'
            manifest.write_text(json.dumps({'files':[{'file':source.name,'sha256':sha256_file(source),
                                                      'production_threads':2,'production_options':[]}]}))
            output=root/'results'
            argv=['alignments.py','--manifest',str(manifest),'--data',str(root),'--binary',str(binary),
                  '--output',str(output),'--configs','B','--replicates','1','--a-replicates','0','--timeout','1']
            execution={'returncode':-15,'timed_out':True,'signals_sent':['SIGTERM','SIGKILL'],'process_group_pid':12345}
            with patch('sys.argv',argv),patch('alignments.subprocess.run',return_value=SimpleNamespace(stdout='fixture',stderr='')),patch('alignments.run_process_group',return_value=execution),patch('sys.stdout',new_callable=io.StringIO):
                with self.assertRaisesRegex(SystemExit,'alignment timeout'):
                    alignments.main()
            row=json.loads((output/'runs.jsonl').read_text())
            self.assertTrue(row['timed_out'])
            self.assertFalse(row['completed'])
            self.assertEqual(row['failure_reason'],'alignment timeout')
            self.assertIsNone(row['cpu'])
            self.assertIsNone(row['resources'])
            self.assertFalse(row['resources_complete'])
            self.assertFalse(json.loads((output/'STATUS.json').read_text())['finished'])


    def test_installed_hashes_cover_a_release_and_refuse_other_layouts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ['bin/mafft','payload/bin/mafft','payload/libexec/mafft/tbfast','other/lone/mafft']:
                (root/name).parent.mkdir(parents=True,exist_ok=True);(root/name).write_text(name)
            self.assertEqual(sorted(alignments.installed_hashes(root/'bin/mafft')),
                             ['bin/mafft','payload/bin/mafft','payload/libexec/mafft/tbfast'])
            with self.assertRaisesRegex(SystemExit,'--binary must be'):
                alignments.installed_hashes(root/'other/lone/mafft')

    def test_resume_refuses_a_rebuilt_libexec_program(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source=root/'fixture.fasta';source.write_text('>a\nACGT\n')
            (root/'prefix/bin').mkdir(parents=True);(root/'prefix/libexec/mafft').mkdir(parents=True)
            binary=root/'prefix/bin/mafft';binary.write_text('launcher')
            program=root/'prefix/libexec/mafft/tbfast';program.write_text('build 1')
            manifest=root/'manifest.json'
            manifest.write_text(json.dumps({'files':[{'file':source.name,'sha256':sha256_file(source),
                                                      'production_threads':2,'production_options':[]}]}))
            argv=['alignments.py','--manifest',str(manifest),'--data',str(root),'--binary',str(binary),
                  '--output',str(root/'results'),'--configs','B','--replicates','0','--a-replicates','0']
            version=SimpleNamespace(stdout='fixture',stderr='')
            with patch('sys.argv',argv),patch('alignments.subprocess.run',return_value=version),patch('sys.stdout',new_callable=io.StringIO):
                alignments.main()
                program.write_text('build 2')
                with self.assertRaisesRegex(SystemExit,'resume metadata mismatch: installed_file_sha256'):
                    alignments.main()


class PlanExecutionTests(unittest.TestCase):
    def fixture(self,root,steps):
        path=root/'plan.json'
        path.write_text(json.dumps({'output':str(root/'campaign'),'cwd':str(root),'steps':steps}))
        return path

    def test_failure_retries_into_new_log_and_success_is_not_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);plan=self.fixture(root,[{'label':'one','command':['fixture']}])
            codes=iter([1,0])
            def run(command,**kwargs):
                code=next(codes);kwargs['stdout'].write(f'attempt returned {code}\n')
                return SimpleNamespace(returncode=code)
            with patch('run_plan.subprocess.run',side_effect=run) as runner,patch('sys.stdout',new_callable=io.StringIO):
                self.assertEqual(execute_plan(plan),1)
                original=(root/'campaign/one.log').read_text()
                self.assertEqual(execute_plan(plan),0)
                self.assertEqual(execute_plan(plan),0)
                self.assertEqual(runner.call_count,2)
            self.assertEqual((root/'campaign/one.log').read_text(),original)
            self.assertIn('returned 0',(root/'campaign/one.attempt-002.log').read_text())
            ledger=[json.loads(line) for line in (root/'campaign/steps.jsonl').read_text().splitlines()]
            self.assertEqual([r['attempt'] for r in ledger],[1,2])
            self.assertEqual([r['accepted'] for r in ledger],[False,True])

    def test_orphan_attempt_log_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);plan=self.fixture(root,[{'label':'one','command':['fixture']}])
            (root/'campaign').mkdir();(root/'campaign/one.log').write_text('interrupted log\n')
            with patch('run_plan.subprocess.run',return_value=SimpleNamespace(returncode=0)),patch('sys.stdout',new_callable=io.StringIO):
                self.assertEqual(execute_plan(plan),0)
            self.assertEqual((root/'campaign/one.log').read_text(),'interrupted log\n')
            self.assertTrue((root/'campaign/one.attempt-002.log').exists())

    def test_explicit_partial_exit_proceeds_and_stays_visible_on_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            plan=self.fixture(root,[{'label':'partial','command':['fixture'],'accepted_returncodes':[0,1]},
                                    {'label':'analysis','command':['fixture']}])
            with patch('run_plan.subprocess.run',side_effect=[SimpleNamespace(returncode=1),SimpleNamespace(returncode=0)]) as runner,patch('sys.stdout',new_callable=io.StringIO):
                self.assertEqual(execute_plan(plan),0)
                self.assertEqual(execute_plan(plan),0)
                self.assertEqual(runner.call_count,2)
            status=json.loads((root/'campaign/STATUS.json').read_text())
            self.assertTrue(status['finished'])
            self.assertEqual(status['accepted_nonzero_steps'],[{'label':'partial','returncode':1,'attempt':1}])

    def test_invalid_exit_policy_or_changed_plan_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);plan=self.fixture(root,[{'label':'bad','command':['fixture'],'accepted_returncodes':'0,1'}])
            with self.assertRaisesRegex(ValueError,'accepted_returncodes'):
                execute_plan(plan)
            self.assertFalse((root/'campaign').exists())
            plan=self.fixture(root,[{'label':'one','command':['fixture']}])
            with patch('run_plan.subprocess.run',return_value=SimpleNamespace(returncode=0)),patch('sys.stdout',new_callable=io.StringIO):
                self.assertEqual(execute_plan(plan),0)
            changed=json.loads(plan.read_text());changed['steps'][0]['accepted_returncodes']=[0,1];plan.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError,'Plan changed'):
                execute_plan(plan)


class ProtocolTests(unittest.TestCase):
    def queue(self,root,nstop=None):
        meta={'iqtree':'/fixture/iqtree2','binary_sha256':'binary-one','iqtree_version':'fixture2',
              'model':'GTR+F+G4','threads':1,'nstop':nstop,'fast':False,'support':False,'mem':'6G'}
        command=[meta['iqtree'],'-s','/fixture/alignment','-m',meta['model'],'-T','1','--seed','73111',
                 '--mem','6G','--prefix','/fixture/out','--keep-ident']
        if nstop is not None:command.extend(['--nstop',str(nstop)])
        plan={**meta,'command':command,'seed':73111,'input_sha256':'input','named_alignment_sha256':'aln',
              'support_replicates':None}
        row={'job_key':'job','input_sha256':'input','named_alignment_sha256':'aln','seed':73111,'fast':False,'support':False}
        return {'metadata':meta,'jobs':{'job':plan}},row

    def test_seed_and_io_paths_vary_but_unrecognized_search_flags_do_not_pool(self):
        queue,row=self.queue(None)
        first,_=analyze_trees.inference_protocol(queue,row)
        changed=deepcopy(queue);changedrow=dict(row,seed=73129)
        plan=changed['jobs']['job'];plan['seed']=73129
        for flag,value in [('-s','/new/alignment'),('--prefix','/new/out'),('--seed','73129')]:
            plan['command'][plan['command'].index(flag)+1]=value
        self.assertEqual(first,analyze_trees.inference_protocol(changed,changedrow)[0])
        changed['jobs']['job']['command'].extend(['--epsilon','0.001'])
        self.assertNotEqual(first,analyze_trees.inference_protocol(changed,changedrow)[0])

    def test_binary_model_search_changes_split_cohorts_and_forged_metadata_fails(self):
        queue,row=self.queue(None)
        first,_=analyze_trees.inference_protocol(queue,row)
        search,searchrow=self.queue(None,nstop=500)
        self.assertNotEqual(first,analyze_trees.inference_protocol(search,searchrow)[0])
        changed=deepcopy(queue)
        changed['metadata']['binary_sha256']=changed['jobs']['job']['binary_sha256']='binary-two'
        self.assertNotEqual(first,analyze_trees.inference_protocol(changed,row)[0])
        forged=deepcopy(queue);forged['metadata']['model']='HKY+F+G4'
        with self.assertRaisesRegex(ValueError,'provenance mismatch: model'):
            analyze_trees.inference_protocol(forged,row)
        forged=deepcopy(queue);forged['jobs']['job']['command'][4]='GTR+F+G8'
        with self.assertRaisesRegex(ValueError,'model/thread command'):
            analyze_trees.inference_protocol(forged,row)
        forged=deepcopy(queue);forged['jobs']['job']['command'][8]='999'
        with self.assertRaisesRegex(ValueError,'paired-search label'):
            analyze_trees.inference_protocol(forged,row)

    def test_collector_keeps_two_methods_in_separate_input_aggregates(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'fixture.fasta'
            source.write_text('>a\nAC\n>b\nAG\n>c\nAT\n>d\nAA\n')
            digest=sha256_file(source);queues=[]
            for number,nstop in enumerate([100,500]):
                path=root/f'queue{number}';path.mkdir();queue,row=self.queue(root,nstop)
                queue['jobs']={};rows=[]
                for config,newick in [('A','((t000001,t000002),(t000003,t000004));'),
                                       ('B','((t000001,t000003),(t000002,t000004));')]:
                    tree=path/f'{config}.treefile';tree.write_text(newick)
                    base,_=self.queue(root,nstop);plan=base['jobs']['job'];plan['input_sha256']=digest
                    plan['named_alignment_sha256']=config+'alignment';queue['jobs'][config]=plan
                    rows.append(dict(row,job_key=config,input=source.name,input_sha256=digest,named_alignment_sha256=plan['named_alignment_sha256'],
                                     config=config,replicate=0,completed=True,inference_counts_as_independent=True,
                                     tree=str(tree),tree_sha256=sha256_file(tree)))
                (path/'queue.json').write_text(json.dumps(queue))
                (path/'runs.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows));queues.append(path)
            output=root/'analysis'
            argv=['analyze_trees.py','--queues',*map(str,queues),'--data',str(root),'--output',str(output),'--quartets','10']
            with patch('sys.argv',argv),patch('sys.stdout',new_callable=io.StringIO):
                analyze_trees.main()
            result=json.loads((output/'summary.json').read_text())
            self.assertEqual(len(result['protocols']),2)
            self.assertEqual(len(result['datasets']),2)
            self.assertTrue(all(x['distinct_inputs']==1 for x in result['aggregates']))
            self.assertEqual(len({x['protocol_id'] for x in result['aggregates']}),2)


if __name__=='__main__':
    unittest.main()
