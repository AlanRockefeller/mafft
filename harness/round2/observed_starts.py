#!/usr/bin/env python3
"""Extract matching production alignment-stage starts, read-only.

These are execution requests from the selected jobs, not user submission times
or the full server arrival stream. File mtimes are never used as arrivals.
"""
import argparse
import datetime
import json
from pathlib import Path
import re
import shlex
from analysis import sha256_file, write_json

STAMP = re.compile(r'^(20\d\d-\d\d-\d\d \d\d:\d\d:\d\d,\d+)')


def command_identity(command):
    if command[:1] == ['CMD:']:
        command = command[1:]
    index = command.index('--thread')
    return command[0], int(command[index+1]), command[1:index]+command[index+2:-1], command[-1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--jobs', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    records, excluded = [], []
    for f in json.loads(args.manifest.read_text())['files']:
        directory = args.jobs / Path(f['file']).stem
        log, alignment_log = directory/'logs/pipeline.log', directory/'logs/alignment.log'
        try:
            command = shlex.split(alignment_log.read_text(errors='replace').splitlines()[0])
            if command[:1] == ['CMD:']:
                command = command[1:]
            binary, threads, options, source = command_identity(command)
            if (binary, threads, options) != (f['production_binary'], f['production_threads'], f['production_options']):
                raise ValueError('Current recorded command differs from frozen production options')
            if sha256_file(source) != f['sha256']:
                raise ValueError('Current production input differs from frozen original hash')
            matches = []
            lines = log.read_text(errors='replace').splitlines()
            for number, line in enumerate(lines):
                marker = 'Running command (streaming): '
                if marker in line and STAMP.match(line):
                    try:
                        actual = shlex.split(line.split(marker, 1)[1])
                        if command_identity(actual) == command_identity(command):
                            matches.append((number, STAMP.match(line).group(1)))
                    except (ValueError, IndexError):
                        pass
            if not matches:
                raise ValueError('No dated matching MAFFT execution command')
            number, stamp = matches[-1]
            elapsed = None
            for line in lines[number+1:]:
                if 'Running command (streaming): ' in line and 'mafft ' in line:
                    break
                if 'event=alignment.mafft_completed' in line:
                    value = re.search(r'\belapsed_seconds=([0-9.]+)', line)
                    elapsed = float(value.group(1)) if value else None
                    break
            records.append({'input': f['file'], 'input_sha256': f['sha256'],
                            'start_log_clock': datetime.datetime.strptime(stamp, '%Y-%m-%d %H:%M:%S,%f').isoformat(),
                            'source_pipeline_log': str(log), 'source_log_sha256': sha256_file(log),
                            'source_line': number+1, 'matching_commands_in_log': len(matches),
                            'selection': 'Latest command matching current alignment.log and frozen manifest flags',
                            'command': command, 'current_production_input_hash_verified': True,
                            'observed_old_runtime_seconds': elapsed})
        except (OSError, ValueError, IndexError) as error:
            excluded.append({'input': f['file'], 'reason': str(error)})
    records.sort(key=lambda r: (r['start_log_clock'], r['input']))
    if not records:
        raise ValueError('No verified matching observed starts')
    first = datetime.datetime.fromisoformat(records[0]['start_log_clock'])
    for record in records:
        record['relative_seconds'] = (datetime.datetime.fromisoformat(record['start_log_clock'])-first).total_seconds()
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output/'observed-starts.json', {'records': records, 'excluded': excluded,
               'manifest_sha256': sha256_file(args.manifest), 'runner_sha256': sha256_file(__file__),
               'clock': 'Naive timestamps from one application log clock; no time zone is specified. Only relative differences are used.',
               'scope': 'Selected production alignment execution-start schedule; not submission arrivals, not the full server stream, not a replay of upstream/downstream resource use. Historical runtime is descriptive, not a paired speedup denominator. Current input hashes verify matching files but cannot prove historical immutability of those paths.'})
    print(json.dumps({'verified_starts': len(records), 'excluded': len(excluded),
                      'span_seconds': records[-1]['relative_seconds']}))


if __name__ == '__main__':
    main()
