#!/usr/bin/env python3
"""Verify a private release against tracked source and preserved references.

Every check is read-only; the only write is a new JSON evidence file. Manifest
paths are resolved against --manifest-base for the retained round-1 manifests.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from analysis import sha256_file


def verify_json_manifest(prefix, path):
    expected = json.loads(path.read_text())
    for relative, digest in expected.items():
        if sha256_file(prefix / relative) != digest:
            raise ValueError('Changed preserved file: ' + str(prefix / relative))
    return {'manifest': str(path.resolve()), 'manifest_sha256': sha256_file(path),
            'verified_files': len(expected), 'all_match': True}


def verify_text_manifest(path, base):
    count = 0
    for line in path.read_text().splitlines():
        if not line:
            continue
        digest, relative = line.split(None, 1)
        target = Path(relative.lstrip('*'))
        if not target.is_absolute():
            target = base / target
        if sha256_file(target) != digest:
            raise ValueError('Changed preserved file: ' + str(target))
        count += 1
    return {'manifest': str(path.resolve()), 'manifest_sha256': sha256_file(path),
            'verified_files': count, 'all_match': True}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repository', type=Path, required=True)
    p.add_argument('--source-commit', required=True)
    p.add_argument('--build', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--data', type=Path, required=True)
    p.add_argument('--manifest-base', type=Path, required=True)
    p.add_argument('--golden-manifest', type=Path, required=True)
    p.add_argument('--original-manifest', type=Path, required=True)
    p.add_argument('--private-original', type=Path, required=True)
    p.add_argument('--private-original-manifest', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    git = lambda *a: subprocess.check_output(['git', '-C', str(args.repository), *a])
    commit = git('rev-parse', args.source_commit).decode().strip()
    build = json.loads((args.build / 'build.json').read_text())
    for relative, digest in build['source_sha256'].items():
        tracked = hashlib.sha256(git('show', commit + ':' + relative)).hexdigest()
        locations = [args.repository / relative, args.build / 'source' / relative,
                     Path(build['source']) / relative]
        if tracked != digest or any(sha256_file(path) != digest for path in locations):
            raise ValueError('Build/tracked/worktree source mismatch: ' + relative)
    installed = verify_json_manifest(args.build / 'install', args.build / 'binary-sha256.json')
    goldens = verify_text_manifest(args.golden_manifest, args.manifest_base)
    original = verify_text_manifest(args.original_manifest, args.manifest_base)
    private = verify_json_manifest(args.private_original, args.private_original_manifest)
    files = json.loads(args.manifest.read_text())['files']
    for row in files:
        if sha256_file(args.data / row['file']) != row['sha256']:
            raise ValueError('Original input changed: ' + row['file'])
    binary = args.build / 'install/bin/mafft'
    env = dict(os.environ); env.pop('MAFFT_BINARIES', None)
    version = subprocess.run([str(binary), '--version'], env=env, capture_output=True, text=True, check=True)
    record = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'argv': sys.argv,
              'source_commit': commit, 'current_harness_commit': git('rev-parse', 'HEAD').decode().strip(),
              'current_git_status': git('status', '--short').decode(),
              'core_files_matching_tracked_build_and_both_source_copies': len(build['source_sha256']),
              'build_metadata_sha256': sha256_file(args.build / 'build.json'),
              'compiler': build['compiler'], 'flags': build['flags'], 'commands': build['commands'],
              'version': version.stdout + version.stderr, 'installed': installed,
              'launcher_sha256': sha256_file(binary),
              'launcher_prefix_assignments': [line for line in binary.read_text().splitlines()
                                             if line.startswith(('prefix=', 'defaultprefix='))],
              'preserved_goldens': goldens, 'preserved_production_opt8': original,
              'preserved_private_opt8': private,
              'raw_inputs': {'files': len(files), 'distinct_sha256': len({r['sha256'] for r in files}),
                             'all_match': True, 'manifest_sha256': sha256_file(args.manifest)},
              'scope': 'Verifies source correspondence, binary preservation and input identities. Experiment outcomes and timing are separate artifacts. No installation or production setting is changed.'}
    with args.output.open('x') as out:
        out.write(json.dumps(record, indent=2) + '\n')
    print(json.dumps({'verified_core_files': len(build['source_sha256']), 'verified_inputs': len(files),
                      'verified_goldens': goldens['verified_files'], 'version': record['version'].strip()}))


if __name__ == '__main__':
    main()
