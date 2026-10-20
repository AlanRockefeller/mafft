#!/usr/bin/env python3
"""Compare exact post-attempt refinement traces and accepted intermediate rows."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from analysis import make_mapping, sha256_file, write_json

SEMANTIC = re.compile(rb'^REFINE_SEMANTIC iter=(\d+) branch=(\d+) score=([0-9a-f]{16}) accepted=([01]) subgeneration=(\d+) converged=([01]) finish=([01]) gainlist=([0-9a-f]{16}) tscorelist=([0-9a-f]{16}) basegain=([0-9a-f]{16})$')
MASTER = re.compile(rb'^REFINE_MASTER iter=(\d+) branch=(\d+) row=(\d+) seq=(.*)$')
CACHED = re.compile(rb'(\d{3,})-(\d{4,})-\d \(thread\s+1\) cached-rejection')


def read_semantics(raw, taxa):
    records, attempts, accepted, remaining, current, next_row = [], 0, 0, 0, None, 0
    for line in raw.split(b'\n'):
        if line.startswith(b'REFINE_SEMANTIC'):
            match = SEMANTIC.fullmatch(line)
            if not match or remaining:
                raise ValueError('Malformed semantic record or incomplete accepted master rows')
            records.append(line)
            attempts += 1
            current = (int(match[1]), int(match[2]))
            if match[4] == b'1':
                accepted += 1
                remaining, next_row = taxa, 0
        elif line.startswith(b'REFINE_MASTER'):
            match = MASTER.fullmatch(line)
            if (not match or not remaining or (int(match[1]), int(match[2])) != current or
                    int(match[3]) != next_row):
                raise ValueError('Misordered or misattributed accepted master row')
            records.append(line)
            remaining -= 1
            next_row += 1
    if remaining or not attempts:
        raise ValueError('Incomplete or absent actual semantic tracing')
    cached = list(CACHED.finditer(raw))
    counters = [tuple(map(int, m)) for m in re.findall(rb'REJECT_MEMO hits=(\d+) misses=(\d+)', raw)]
    if len(cached) != sum(a for a, b in counters):
        raise ValueError('Cache-hit diagnostic count does not match summary')
    return records, {'attempts': attempts, 'accepted_intermediate_alignments': accepted,
                     'exact_master_rows': accepted * taxa, 'cached_attempts': len(cached),
                     'semantic_sha256': hashlib.sha256(b'\n'.join(records)+b'\n').hexdigest()}


def load_runs(path):
    result = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if row['config'] != 'A' or row['replicate'] != 0 or row['returncode'] or not row['matches_A_golden']:
            raise ValueError('Expected exact-output deterministic A0 checks')
        if sha256_file(row['alignment']) != row['output_sha256']:
            raise ValueError('Experimental alignment changed')
        if row['input_sha256'] in result:
            raise ValueError('Duplicate semantic-control input')
        result[row['input_sha256']] = row
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference-runs', type=Path, required=True)
    p.add_argument('--candidate-runs', type=Path, required=True)
    p.add_argument('--reference-build', type=Path, required=True)
    p.add_argument('--candidate-build', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    builds = [json.loads(path.read_text()) for path in [args.reference_build, args.candidate_build]]
    if (builds[0]['memo'] != 0 or builds[1]['memo'] != 1 or
            not all(x['semantic_trace'] == 1 for x in builds) or
            builds[0]['source_tditeration_sha256'] != builds[1]['source_tditeration_sha256'] or
            builds[0]['inherited_build_json_sha256'] != builds[1]['inherited_build_json_sha256']):
        raise ValueError('Require same-source traced memo-off and memo-on builds')
    reference, candidate = load_runs(args.reference_runs), load_runs(args.candidate_runs)
    if not reference or set(reference) != set(candidate):
        raise ValueError('Semantic input cohorts differ')
    results = []
    for digest, a in reference.items():
        b = candidate[digest]
        mapping = make_mapping(args.data / a['input'])
        if mapping['input_sha256'] != digest:
            raise ValueError('Original input changed')
        aa, bb = list(a['argv']), list(b['argv'])
        aa[aa.index(a['binary'])] = '<binary>'
        bb[bb.index(b['binary'])] = '<binary>'
        if aa != bb or a['output_sha256'] != b['output_sha256']:
            raise ValueError('Options or exact alignment outputs differ')
        paths = [Path(r['alignment']).with_name('stderr.txt') for r in [a, b]]
        records = [read_semantics(path.read_bytes(), len(mapping['records'])) for path in paths]
        identical = records[0][0] == records[1][0]
        first = next((i for i, (x, y) in enumerate(zip(records[0][0], records[1][0])) if x != y), None)
        if first is None and len(records[0][0]) != len(records[1][0]):
            first = min(len(records[0][0]), len(records[1][0]))
        results.append({'input': a['input'], 'input_sha256': digest, 'taxa': len(mapping['records']),
                        'semantic_records_byte_identical': identical, 'first_different_record_index': first,
                        'reference': records[0][1], 'candidate': records[1][1],
                        'reference_stderr': str(paths[0]), 'candidate_stderr': str(paths[1]),
                        'reference_stderr_sha256': sha256_file(paths[0]), 'candidate_stderr_sha256': sha256_file(paths[1]),
                        'alignment_sha256': a['output_sha256']})
    if not sum(r['candidate']['cached_attempts'] for r in results):
        raise ValueError('No active reuse tested; guard bypass cannot validate the shortcut')
    args.output.mkdir(parents=True, exist_ok=False)
    passed = all(r['semantic_records_byte_identical'] for r in results)
    write_json(args.output / 'summary.json', {
        'passed': passed, 'distinct_inputs': len(results), 'results': results,
        'reference_build_sha256': sha256_file(args.reference_build),
        'candidate_build_sha256': sha256_file(args.candidate_build),
        'interpretation': 'Actual separate same-source memo-off/on processes. Exact comparison includes every post-rejection raw score bit, signed zero, acceptance, subgeneration, gain and convergence bookkeeping, and every original-order sequence row at every acceptance. Traced runs are correctness experiments, never timing benchmarks. Finite checks supplement the scoped correctness argument.'})
    print(json.dumps({'passed': passed, 'inputs': len(results),
                      'attempts': sum(r['candidate']['attempts'] for r in results),
                      'cached_attempts': sum(r['candidate']['cached_attempts'] for r in results)}))
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
