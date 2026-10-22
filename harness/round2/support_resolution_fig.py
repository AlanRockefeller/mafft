#!/usr/bin/env python3
"""Plot stored support-filtered split and quartet differences; never infer trees."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import platform
import sys


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_rows(directory):
    summary = json.loads((directory / 'summary.json').read_text())
    if any(q['successful_requests'] != q['requested'] for q in summary['queue_counts']):
        raise ValueError('Use a completed support analysis, not a partial cohort')
    rows, protocols, observed = [], set(), set()
    for line in (directory / 'comparisons.jsonl').open():
        record = json.loads(line)
        if record['kind'] not in ['A_vs_B', 'A_vs_C']:
            continue
        protocol_id = record['protocol_id']
        method = summary['protocols'][protocol_id]
        if not record['support'] or record['fast'] or not method['support'] or method['fast']:
            raise ValueError('Expected supported default-search comparisons only')
        identity = (record['input_sha256'], record['kind'])
        if identity in observed:
            raise ValueError('Ambiguous duplicate input/comparison; do not pool methods or stages')
        observed.add(identity)
        protocols.add(protocol_id)
        value = record['comparison']['views']['strong_support']
        if (value['support_filter'] != {'shalrt_min': 80.0, 'ufboot_min': 95.0} or
                not value['zero_edges_collapsed'] or value['zero_threshold'] != 1e-6):
            raise ValueError('Expected original-edge collapse <=1e-6 and joint SH-aLRT >=80/UFBoot >=95')
        panel = value['quartets']
        count_fields = ['evaluated', 'different', 'both_resolved', 'both_unresolved',
                        'reference_only_resolved', 'candidate_only_resolved', 'resolved_conflict']
        if any(not isinstance(panel[k], int) or panel[k] < 0 for k in count_fields) or not panel['evaluated']:
            raise ValueError('Missing or invalid recorded quartet counts')
        if panel['different'] != sum(panel[k] for k in
                ['reference_only_resolved', 'candidate_only_resolved', 'resolved_conflict']):
            raise ValueError('Quartet disagreement categories do not sum to the recorded total')
        if (panel['evaluated'] != sum(panel[k] for k in
                ['both_resolved', 'both_unresolved', 'reference_only_resolved', 'candidate_only_resolved']) or
                panel['resolved_conflict'] > panel['both_resolved']):
            raise ValueError('Quartet resolution counts are inconsistent')
        if panel['exact'] and panel['evaluated'] != math.comb(value['taxa'], 4):
            raise ValueError('Exact quartet panel does not cover all recorded taxa')
        expected_nrf = value['rf'] / (2 * (value['taxa'] - 3))
        if not math.isclose(value['normalized_rf'], expected_nrf, abs_tol=1e-12):
            raise ValueError('Unexpected normalized RF definition')
        row = {k: record[k] for k in ['input', 'input_sha256', 'kind', 'protocol_id',
               'reference_config', 'candidate_config', 'reference_replicate', 'candidate_replicate',
               'reference_seed', 'candidate_seed']}
        row.update(taxa=value['taxa'], rf=value['rf'], normalized_rf=value['normalized_rf'],
                   incompatible_strong_split_pairs=value['incompatible_split_pairs'],
                   reference_tree=record['comparison']['reference']['path'],
                   candidate_tree=record['comparison']['candidate']['path'],
                   quartet_panel_exact=panel['exact'], quartet_panel_seed=panel['seed'],
                   possible_quartets=panel['possible_quartets'],
                   quartet_sampling=panel['sampling'],
                   shalrt_threshold=80, ufboot_threshold=95, original_edge_cutoff=1e-6)
        for key in count_fields:
            row[key] = panel[key]
        for key in ['different', 'reference_only_resolved', 'candidate_only_resolved', 'resolved_conflict']:
            row[key + '_percent'] = 100 * panel[key] / panel['evaluated']
        rows.append(row)
    if not rows or len(protocols) != 1:
        raise ValueError('Provide exactly one compatible completed support-method cohort')
    groups = {}
    for row in rows:
        groups.setdefault(row['input_sha256'], set()).add(row['kind'])
    if any(kinds != {'A_vs_B', 'A_vs_C'} for kinds in groups.values()):
        raise ValueError('Each selected input must have both A–B and A–C comparisons')
    rows.sort(key=lambda r: (r['taxa'], r['input'], r['kind']))
    return rows, summary['protocols'][next(iter(protocols))]


def render(rows, output, formats):
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9,
                               'svg.fonttype': 'none', 'pdf.fonttype': 42,
                               'axes.spines.top': False, 'axes.spines.right': False,
                               'savefig.dpi': 200})
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from matplotlib.ticker import FormatStrFormatter, MaxNLocator

    fig, (rf, quartet) = plt.subplots(1, 2, figsize=(11.5, .38 * len(rows) + 2.8), sharey=True,
                                     gridspec_kw={'width_ratios': [1.15, 3.5], 'wspace': .15})
    categories = [('reference_only_resolved', 'Resolved only in A', '#4477AA'),
                  ('candidate_only_resolved', 'Resolved only in B/C', '#EEAA33'),
                  ('resolved_conflict', 'Both resolved, conflicting', '#BB2233')]
    labels, group, stripe = [], None, False
    nrf_max = max(row['normalized_rf'] for row in rows)
    rf.set_xlim(-max(.002, nrf_max * .05), max(.01, nrf_max * 1.32))
    quartet.set_xlim(0, max(1, max(row['different_percent'] for row in rows) * 1.23))
    for number, row in enumerate(rows):
        first = row['input_sha256'] != group
        if first:
            group, stripe = row['input_sha256'], not stripe
        if stripe:
            for ax in [rf, quartet]:
                ax.axhspan(number - .5, number + .5, color='#F1F3F5', zorder=0)
        if first and number:
            for ax in [rf, quartet]:
                ax.axhline(number - .5, color='#D4D7DA', lw=.6)
        name = row['input'].removesuffix('.fasta')
        labels.append(f"{name:<5}  A–{row['candidate_config']} r{row['candidate_replicate']}")
        rf.scatter(row['normalized_rf'], number, s=28, color='#222222', zorder=3)
        rf.annotate(f"RF {row['rf']}", (row['normalized_rf'], number), xytext=(5, 0),
                    textcoords='offset points', va='center', fontsize=7)
        left = 0
        for key, _, color in categories:
            width = row[key + '_percent']
            quartet.barh(number, width, left=left, height=.61, color=color, zorder=2)
            left += width
        quartet.annotate(f"{left:.2f}%  ({row['different']:,}/{row['evaluated']:,})", (left, number),
                         xytext=(5, 0), textcoords='offset points', va='center', fontsize=7.5)
    rf.set_yticks(range(len(rows)), labels)
    rf.set_ylim(len(rows) - .5, -.5)
    rf.set_title('Strong-split distance', fontsize=11)
    rf.set_xlabel('Normalized unrooted RF\nRF / [2 × (taxa − 3)]')
    rf.xaxis.set_major_locator(MaxNLocator(4))
    rf.xaxis.set_major_formatter(FormatStrFormatter('%.02f'))
    quartet.set_title('Quartet disagreement: resolution changes versus conflicts', fontsize=11)
    quartet.set_xlabel('Percentage of all evaluated quartets (not percentage of taxa or errors)')
    quartet.tick_params(axis='y', left=False)
    quartet.spines['left'].set_visible(False)
    for ax in [rf, quartet]:
        ax.grid(axis='x', alpha=.2)
        ax.set_axisbelow(True)
    fig.suptitle('Supported tree differences can change resolution without opposing strong clades', fontsize=13, y=.98)
    fig.text(.5, .925, 'All original taxa retained; joint SH-aLRT ≥80 and UFBoot ≥95; original edges ≤1e−6 collapsed before comparison.',
             ha='center', fontsize=9)
    fig.legend(handles=[Patch(facecolor=color, label=label) for _, label, color in categories],
               loc='lower center', bbox_to_anchor=(.5, .075), ncol=3, frameon=False, fontsize=8)
    exact = sorted({(r['input'].removesuffix('.fasta'), r['evaluated']) for r in rows if r['quartet_panel_exact']})
    sampled = sorted({r['evaluated'] for r in rows if not r['quartet_panel_exact']})
    panel_note = ('Exact: ' + ', '.join(f'{name} {count:,} quartets' for name, count in exact) + '. ' if exact else '')
    panel_note += ('Other inputs: ' + '/'.join(f'{n:,}' for n in sampled) + ' fixed-seed uniform draws with replacement. ' if sampled else '')
    fig.text(.5, .018, panel_note + '\n'
             f"{len(rows)} selected comparisons nested within {len({r['input_sha256'] for r in rows})} input hashes; neither quartet draws nor comparisons are biological replicates.\n"
             'Unshown quartets have matching resolutions. Support and disagreement are conditional on each alignment/model, not biological accuracy.',
             ha='center', fontsize=7.5)
    fig.subplots_adjust(left=.16, right=.98, top=.86, bottom=.22)
    files = []
    for extension in formats:
        path = output / f'support-resolution.{extension}'
        metadata = ({'Creator': 'MAFFT round 2 support-resolution figure', 'CreationDate': None, 'ModDate': None}
                    if extension == 'pdf' else {'Creator': 'MAFFT round 2 support-resolution figure', 'Date': None}
                    if extension == 'svg' else {'Software': 'MAFFT round 2 support-resolution figure'})
        if path.exists():
            raise FileExistsError(path)
        fig.savefig(path, bbox_inches='tight', metadata=metadata)
        files.append({'path': str(path.resolve()), 'sha256': digest(path)})
    plt.close(fig)
    return files, matplotlib.__version__


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--analysis', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--formats', nargs='+', choices=['svg', 'pdf', 'png'], default=['svg', 'pdf', 'png'])
    args = p.parse_args()
    rows, protocol = load_rows(args.analysis)
    args.output.mkdir(parents=True, exist_ok=False)
    table = args.output / 'support-resolution.tsv'
    with table.open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter='\t', lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    files, version = render(rows, args.output, list(dict.fromkeys(args.formats)))
    manifest = {'command': sys.argv, 'python_version': platform.python_version(), 'matplotlib_version': version,
                'script_sha256': digest(__file__), 'analysis': str(args.analysis.resolve()),
                'source_sha256': {name: digest(args.analysis / name) for name in ['summary.json', 'comparisons.jsonl']},
                'protocol': protocol, 'rows': len(rows), 'distinct_input_hashes': len({r['input_sha256'] for r in rows}),
                'table': {'path': str(table.resolve()), 'sha256': digest(table)}, 'figures': files,
                'method': 'Use the recorded strong_support view only; no tree inference, quartet recomputation or resampling.',
                'interpretation': 'Counts concern selected comparisons and evaluated quartets, not placement errors or biological replication. Resolved/unresolved differences are separated from opposing resolved quartets. Support remains conditional on the analyzed alignment and inference model.'}
    with (args.output / 'manifest.json').open('x') as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'output': str(args.output.resolve()), 'rows': len(rows), 'figures': len(files)}, sort_keys=True))


if __name__ == '__main__':
    main()
