#!/usr/bin/env python3
"""Publication figures and interpretable split tables from completed study outputs.

No MAFFT or tree searches are launched. Inference cohorts and analysis stages
remain separate. Matplotlib is imported only when a rendering function is called.
Use --dry-run to inspect the figure plan without creating files or rendering.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
from typing import Any

from analysis import make_mapping, sha256_file, write_json
from analyze_trees import inference_protocol
from tree import read_tree, visualize_trees

COLORS = {'B': '#D55E00', 'C': '#0072B2', 'F': '#009E73', 'A': '#444444'}
KINDS = [('A_vs_B', 'A versus B', '#D55E00', 'o'),
         ('within_B', 'B repeat versus B repeat', '#009E73', 's'),
         ('A_vs_C', 'A versus C', '#0072B2', '^'),
         ('A_vs_F', 'A versus F', '#CC79A7', 'v'),
         ('search_control_A', 'Same A alignment, different search seeds', '#444444', 'D')]
DEFAULT_VIEWS = ['raw', 'input_duplicate_representatives_zero_collapsed', 'strong_support']
VIEW_NAMES = {'raw': 'All taxa, raw splits', 'zero_collapsed': 'All taxa, edges <= 1e-6 collapsed',
              'input_duplicate_representatives': 'Original duplicate representatives',
              'input_duplicate_representatives_zero_collapsed': 'Duplicate representatives, edges <= 1e-6 collapsed',
              'strong_support': 'All taxa, SH-aLRT >= 80 and UFBoot >= 95',
              'input_duplicate_representatives_strong_support': 'Duplicate representatives, SH-aLRT >= 80 and UFBoot >= 95'}


def slug(value):
    return re.sub(r'[^A-Za-z0-9_.-]+', '-', str(value)).strip('-') or 'figure'


def read_json(path):
    return json.loads(Path(path).read_text())


def write_table(path, rows, fields):
    with Path(path).open('x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter='\t', lineterminator='\n', extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list, tuple)) else value
                             for key, value in row.items()})


def plt_import():
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9,
                                'axes.spines.top': False, 'axes.spines.right': False,
                                'svg.fonttype': 'none', 'pdf.fonttype': 42,
                                'axes.titlesize': 11, 'axes.labelsize': 9,
                                'legend.frameon': False, 'savefig.dpi': 200})
    import matplotlib.pyplot as plt
    return plt


def save_figure(fig, directory, stem, formats):
    files = []
    for extension in formats:
        path = directory / f'{stem}.{extension}'
        if path.exists():
            raise FileExistsError(path)
        metadata = ({'Creator': 'MAFFT round 2 figures', 'CreationDate': None, 'ModDate': None} if extension == 'pdf'
                    else {'Creator': 'MAFFT round 2 figures', 'Date': None} if extension == 'svg'
                    else {'Software': 'MAFFT round 2 figures'})
        fig.savefig(path, bbox_inches='tight', metadata=metadata)
        files.append(str(path.resolve()))
    return files


@dataclass
class Cohort:
    directory: Path
    protocol_id: str
    protocol: dict[str, Any]
    rows: list[dict[str, Any]]
    summary: dict[str, Any]

    @property
    def stem(self):
        location = hashlib.sha256(str(self.directory.resolve()).encode()).hexdigest()[:6]
        return f'{slug(self.directory.name)}-{self.protocol_id[:10]}-{location}'

    @property
    def label(self):
        method = 'IQ-TREE fast search' if self.protocol['fast'] else 'IQ-TREE default search'
        support = '; bootstrap + SH-aLRT' if self.protocol['support'] else '; no branch-support replicates'
        return f'{self.directory.name}: {method}{support}'


def load_cohorts(directories):
    cohorts = []
    for directory in dict.fromkeys(Path(x).resolve() for x in directories):
        summary = read_json(directory / 'summary.json')
        if 'protocols' not in summary:
            raise ValueError(f'{directory}: rerun analyze_trees.py with complete inference protocol grouping before plotting')
        with (directory / 'comparisons.tsv').open(newline='') as stream:
            rows = list(csv.DictReader(stream, delimiter='\t'))
        grouped = defaultdict(list)
        for row in rows:
            for name in ['rf', 'reference_replicate', 'candidate_replicate', 'reference_seed', 'candidate_seed',
                         'shared_splits', 'reference_splits', 'candidate_splits', 'incompatible_split_pairs']:
                if row.get(name, '') != '':
                    row[name] = int(row[name])
            for name in ['normalized_rf', 'split_normalized_rf', 'quartet_distance']:
                row[name] = float(row[name]) if row.get(name, '') not in ['', None] else None
                if row[name] is not None and not math.isfinite(row[name]):
                    raise ValueError(f'Nonfinite comparison value in {directory}')
            grouped[row['protocol_id']].append(row)
        for identifier, protocol in summary['protocols'].items():
            cohorts.append(Cohort(directory, identifier, protocol, grouped.get(identifier, []), summary))
    return cohorts


def rows_for(cohort, view, kind=None):
    return [row for row in cohort.rows if row['view'] == view and (kind is None or row['kind'] == kind)]


def plot_alignment_variation(summary, output, formats):
    plt = plt_import()
    datasets = sorted(summary['datasets'], key=lambda d: (-min((d['configs'][c]['homology_f1']['min']
                         for c in d['configs'] if c != 'A'), default=1.0), d['input']))
    # Most changed files appear first; a dataset remains one plotting unit.
    datasets.reverse()
    configs = [c for c in ['B', 'C', 'F'] if any(c in d['configs'] for d in datasets)]
    if not configs or not datasets:
        return []
    fig, (ax, repeat) = plt.subplots(1, 2, figsize=(12, max(4.8, .32 * len(datasets) + 1.8)),
                                      sharey=True, gridspec_kw={'width_ratios': [3.5, 1.6]})
    for offset, config in enumerate(configs):
        yshift = (offset - (len(configs) - 1) / 2) * .19
        xs, ys, lows, highs = [], [], [], []
        for y, dataset in enumerate(datasets):
            if config not in dataset['configs']:
                continue
            values = dataset['configs'][config]
            f1 = values['homology_f1']
            x = 100 * (1 - f1['median'])
            xs.append(x); ys.append(y + yshift)
            lows.append(max(0, x - 100 * (1 - f1['max'])))
            highs.append(max(0, 100 * (1 - f1['min']) - x))
            variants, runs = values['distinct_outputs'], values['runs']
            repeat.scatter(offset, y, s=110, color=COLORS[config] if variants > 1 else '#e4e4e4',
                           edgecolors=COLORS[config], linewidth=.8, zorder=2)
            repeat.annotate(f'{variants}/{runs}', (offset, y), xytext=(10, 0), textcoords='offset points', va='center', fontsize=8)
        ax.errorbar(xs, ys, xerr=[lows, highs], fmt='o', ms=4, color=COLORS[config],
                    elinewidth=1.2, capsize=2, label=config, zorder=3)
    ax.set_yticks(range(len(datasets)), [f"{d['input'].removesuffix('.fasta')}  (n={d['taxa']})" for d in datasets])
    ax.invert_yaxis()
    ax.set(xlabel='100 × (1 − residue-homology F1), relative to frozen A', title='Observed alignment differences')
    ax.set_xlim(left=-.03)
    ax.grid(axis='x', alpha=.2)
    ax.legend(title='Configuration', loc='lower right')
    repeat.set_xticks(range(len(configs)), configs)
    repeat.set_xlim(-.5, len(configs) - .08)
    repeat.set_title('Distinct byte outputs / runs')
    repeat.tick_params(axis='y', left=False)
    repeat.spines['left'].set_visible(False)
    fig.suptitle(f"Alignment pilot/screen: {len(datasets)} distinct input hashes, {summary['alignment_runs']} runs", fontsize=13)
    fig.text(.5, .015, 'Points: file-level medians; bars: observed minimum–maximum across runs, not confidence intervals. '
             'F1 deficits are not fractions of residues moved.\nFiles may share taxa. Repeated runs are nested observations; reproducibility is finite evidence.',
             ha='center', fontsize=8)
    fig.tight_layout(rect=[0, .055, 1, .96])
    files = save_figure(fig, output, 'alignment-variation', formats)
    plt.close(fig)
    return files


def plot_tree_variation(cohort, view, output, formats):
    plt = plt_import()
    relevant = rows_for(cohort, view)
    grouped = defaultdict(list)
    for row in relevant:
        grouped[(row['input_sha256'], row['kind'])].append(row)
    names = {row['input_sha256']: row['input'].removesuffix('.fasta') for row in relevant}
    kinds = [kind for kind in KINDS if any(key[1] == kind[0] for key in grouped)]
    hashes = sorted(names, key=lambda sha: (-max(row['normalized_rf'] for row in relevant if row['input_sha256'] == sha), names[sha]))
    if not hashes or not kinds:
        return []
    fig, ax = plt.subplots(figsize=(11, max(4.5, .32 * len(hashes) + 2)))
    for k, (kind, label, color, marker) in enumerate(kinds):
        for y, sha in enumerate(hashes):
            values = [row['normalized_rf'] for row in grouped.get((sha, kind), [])]
            if not values:
                continue
            mid = statistics.median(values)
            ax.errorbar(mid, y + (k - (len(kinds) - 1) / 2) * .13,
                        xerr=[[mid - min(values)], [max(values) - mid]], fmt=marker, color=color,
                        ms=4, elinewidth=1, capsize=2, label=label if y == next(i for i, s in enumerate(hashes) if grouped.get((s, kind))) else None)
    ax.set_yticks(range(len(hashes)), [names[sha] for sha in hashes])
    ax.invert_yaxis(); ax.set_xlim(left=-.005)
    ax.set(xlabel='Normalized unrooted RF distance: RF / [2 × (taxa − 3)]', title=VIEW_NAMES.get(view, view))
    ax.grid(axis='x', alpha=.2)
    ax.legend(loc='lower right', fontsize=7)
    fig.suptitle(cohort.label, fontsize=11)
    fig.text(.5, .012, 'Each point is a file/kind median; bars show the observed range of repeated comparisons, not confidence intervals.\n'
             'Search controls compare different seeds on the exact same A alignment. Files may share taxa.', ha='center', fontsize=8)
    fig.tight_layout(rect=[0, .055, 1, .95])
    files = save_figure(fig, output, f'{cohort.stem}-{view}-tree-variation', formats)
    plt.close(fig)
    return files


def search_control_rows(cohort, view):
    relevant = rows_for(cohort, view)
    grouped = defaultdict(list)
    for row in relevant:
        grouped[(row['input_sha256'], row['kind'])].append(row['normalized_rf'])
    table = []
    for config in ['B', 'C', 'F']:
        for sha in sorted({key[0] for key in grouped}):
            controls = grouped.get((sha, 'search_control_A'), [])
            aligned = grouped.get((sha, 'A_vs_' + config), [])
            if not controls or not aligned:
                continue
            name = next(r['input'] for r in relevant if r['input_sha256'] == sha)
            x, y = statistics.median(controls), statistics.median(aligned)
            table.append({'analysis': str(cohort.directory), 'protocol_id': cohort.protocol_id, 'view': view,
                          'input': name, 'input_sha256': sha, 'config': config,
                          'search_control_comparisons': len(controls), 'alignment_comparisons': len(aligned),
                          'search_control_median_nrf': x, 'alignment_median_nrf': y,
                          'search_control_min_nrf': min(controls), 'search_control_max_nrf': max(controls),
                          'alignment_min_nrf': min(aligned), 'alignment_max_nrf': max(aligned),
                          'difference_of_medians_nrf': y - x})
    return table


def plot_search_controls(cohort, view, output, formats):
    table = search_control_rows(cohort, view)
    if not table:
        return [], []
    plt = plt_import()
    fig, ax = plt.subplots(figsize=(6.8, 6))
    labels = set()
    for row in table:
        config = row['config']
        x, y = row['search_control_median_nrf'], row['alignment_median_nrf']
        ax.errorbar(x, y, xerr=[[x - row['search_control_min_nrf']], [row['search_control_max_nrf'] - x]],
                    yerr=[[y - row['alignment_min_nrf']], [row['alignment_max_nrf'] - y]],
                    fmt={'B': 'o', 'C': '^', 'F': 's'}[config], color=COLORS[config],
                    ms=5, elinewidth=.65, alpha=.8, label=config if config not in labels else None)
        labels.add(config)
        ax.annotate(row['input'].removesuffix('.fasta'), (x, y), xytext=(4, 4), textcoords='offset points', fontsize=6.5)
    upper = max(max(ax.get_xlim()), max(ax.get_ylim()), .005)
    ax.plot([0, upper], [0, upper], '--', color='#777777', lw=.8, label='Equal medians')
    ax.set_xlim(-upper * .025, upper * 1.04); ax.set_ylim(-upper * .025, upper * 1.04)
    ax.set(xlabel='Same A alignment, different search seeds: median normalized RF',
           ylabel='A versus another alignment: median normalized RF', title=VIEW_NAMES.get(view, view))
    ax.legend(title='Candidate configuration', fontsize=8)
    fig.suptitle(cohort.label, fontsize=10)
    fig.text(.5, .012, 'Observed ranges are not confidence intervals. One point per input/configuration.\n'
             'This sensitivity comparison does not establish biological accuracy or an alignment-only causal effect.', ha='center', fontsize=8)
    fig.tight_layout(rect=[0, .065, 1, .95])
    files = save_figure(fig, output, f'{cohort.stem}-{view}-search-controls', formats)
    plt.close(fig)
    return files, table


def load_alignment_index(directory):
    directory = Path(directory)
    result, cache = {}, {}
    for row in read_json(directory / 'metric-index.json'):
        path = directory / f"{row['input']}.{row['metrics_hash'][:16]}.json"
        if path not in cache:
            metric = read_json(path)
            if metric['candidate']['sha256'] != row['metrics_hash']:
                raise ValueError(f'Alignment metric hash mismatch: {path}')
            cache[path] = {'input_sha256': metric['input_sha256'], 'f1': metric['homology']['f1'],
                           'alignment_sha256': metric['candidate']['sha256'], 'golden_sha256': metric['reference']['sha256'],
                           'source': str(path.resolve())}
        value = cache[path]
        key = (value['input_sha256'], row['config'], row['replicate'])
        if key in result and result[key] != value:
            raise ValueError(f'Ambiguous alignment replicate provenance: {key}')
        result[key] = value
    return result


def queue_index(cohort):
    result = {}
    for source in cohort.summary['queue_counts']:
        directory = Path(source['queue'])
        queue = read_json(directory / 'queue.json')
        with (directory / 'runs.jsonl').open() as stream:
            for line in stream:
                row = json.loads(line)
                if not row['completed']:
                    continue
                protocol, _ = inference_protocol(queue, row)
                if protocol != cohort.protocol_id:
                    continue
                key = (row['input_sha256'], row['config'], row['replicate'], row['seed'])
                if key in result:
                    old = result[key]
                    if any(old[field] != row[field] for field in ['source_alignment_sha256', 'named_alignment_sha256', 'tree_sha256']):
                        raise ValueError(f'Ambiguous tree request provenance while plotting: {key}')
                else:
                    result[key] = row
    return result


def join_alignment_tree(cohort, view, metrics, requests):
    joined, missing, distinct = [], [], {}
    for row in rows_for(cohort, view):
        if row['kind'] not in ['A_vs_B', 'A_vs_C', 'A_vs_F']:
            continue
        sha = row['input_sha256']
        ak = (sha, row['reference_config'], row['reference_replicate'])
        bk = (sha, row['candidate_config'], row['candidate_replicate'])
        ar = requests.get((*ak, row['reference_seed']))
        br = requests.get((*bk, row['candidate_seed']))
        a, b = metrics.get(ak), metrics.get(bk)
        reason = None
        if not all([a, b, ar, br]):
            reason = 'Missing exact alignment metric or prepared-tree request'
        elif a['alignment_sha256'] != ar['source_alignment_sha256'] or b['alignment_sha256'] != br['source_alignment_sha256']:
            reason = 'Metric alignment hashes do not equal the actual tree-input source hashes'
        elif a['alignment_sha256'] != a['golden_sha256'] or b['golden_sha256'] != a['alignment_sha256']:
            reason = 'Reference tree alignment is not the exact frozen A used for this F1'
        if reason:
            missing.append({'input': row['input'], 'protocol_id': cohort.protocol_id, 'view': view,
                            'config': row['candidate_config'], 'replicate': row['candidate_replicate'], 'reason': reason})
            continue
        key = (sha, row['candidate_config'], row['reference_seed'], row['candidate_seed'], ar['job_key'], br['job_key'])
        if key in distinct:
            distinct[key]['represented_candidate_replicates'].append(row['candidate_replicate'])
            continue
        point = {'analysis': str(cohort.directory), 'protocol_id': cohort.protocol_id, 'view': view,
                 'input': row['input'], 'input_sha256': sha, 'config': row['candidate_config'],
                 'reference_seed': row['reference_seed'], 'candidate_seed': row['candidate_seed'],
                 'represented_candidate_replicates': [row['candidate_replicate']],
                 'alignment_homology_f1': b['f1'], 'homology_f1_deficit_percent': 100 * (1 - b['f1']),
                 'tree_rf': row['rf'], 'tree_normalized_rf': row['normalized_rf'],
                 'reference_tree': ar['tree'], 'candidate_tree': br['tree'],
                 'reference_alignment_sha256': a['alignment_sha256'], 'candidate_alignment_sha256': b['alignment_sha256'],
                 'metric_source': b['source']}
        joined.append(point); distinct[key] = point
    return joined, missing


def plot_alignment_tree(cohort, view, points, output, formats):
    if not points:
        return []
    plt = plt_import()
    configs = [config for config in ['B', 'C', 'F'] if any(p['config'] == config for p in points)]
    fig, axes = plt.subplots(1, len(configs), figsize=(max(6, 4 * len(configs)), 4.8), sharex=True, sharey=True, squeeze=False)
    for ax, config in zip(axes[0], configs):
        selected = [p for p in points if p['config'] == config]
        ax.scatter([p['homology_f1_deficit_percent'] for p in selected], [p['tree_normalized_rf'] for p in selected],
                   c=COLORS[config], s=23, alpha=.65, edgecolors='white', linewidths=.35)
        by_file = {}
        for point in sorted(selected, key=lambda p: p['tree_normalized_rf'], reverse=True):
            by_file.setdefault(point['input_sha256'], point)
        for point in list(by_file.values())[:4]:
            ax.annotate(point['input'].removesuffix('.fasta'), (point['homology_f1_deficit_percent'], point['tree_normalized_rf']),
                        xytext=(3, 4), textcoords='offset points', fontsize=7)
        ax.set(title=f'A versus {config}', xlabel='100 × (1 − residue-homology F1)')
        ax.grid(alpha=.15)
    axes[0, 0].set_ylabel('Normalized unrooted RF distance')
    fig.suptitle(f'{cohort.label}\n{VIEW_NAMES.get(view, view)}', fontsize=10)
    fig.text(.5, .012, 'Unique alignment-pair/search-seed points; repeated identical requests share their recorded computation.\n'
             'Points are nested within files. Association is not a test of biological accuracy; no pooled regression is fitted.', ha='center', fontsize=8)
    fig.tight_layout(rect=[0, .09, 1, .90])
    files = save_figure(fig, output, f'{cohort.stem}-{view}-alignment-versus-tree', formats)
    plt.close(fig)
    return files


def mapping_for(data, row, cache):
    sha = row['input_sha256']
    if sha not in cache:
        mapping = make_mapping(data / row['input'])
        if mapping['input_sha256'] != sha:
            raise ValueError('Original FASTA changed before label export: ' + row['input'])
        cache[sha] = mapping
    return cache[sha]


def observation_key(row):
    return tuple(row[k] for k in ['input_sha256', 'kind', 'reference_config', 'candidate_config',
                                 'reference_replicate', 'candidate_replicate', 'reference_seed', 'candidate_seed'])


def detailed_comparisons(cohort):
    with (cohort.directory / 'comparisons.jsonl').open() as stream:
        for line in stream:
            row = json.loads(line)
            if row['protocol_id'] == cohort.protocol_id:
                yield row


def split_conflicts(cohort, view, data, mapping_cache, limit=None):
    """Export actual incompatible splits; examples alone can omit strong conflicts."""
    if not any(row['view'] == view and row['incompatible_split_pairs'] for row in cohort.rows):
        return [], {'view': view, 'available': any(row['view'] == view for row in cohort.rows), 'exported_pairs': 0, 'truncated': False}
    result, expected, exported = [], 0, 0
    for info in detailed_comparisons(cohort):
        values = info['comparison']['views'].get(view)
        if not values or not values['incompatible_split_pairs']:
            continue
        expected += values['incompatible_split_pairs']
        mapping = mapping_for(data, info, mapping_cache)
        names = {record['id']: record['original_header'] for record in mapping['records']}
        universe = (set(info['comparison']['duplicate_sensitivity']['retained_ids'])
                    if view.startswith('input_duplicate_representatives') else set(names))
        if len(universe) != values['taxa']:
            raise ValueError('Conflict-table taxon universe does not match the selected view')
        if 'split_details' not in values:
            raise ValueError('Complete conflict export requires analyze_trees split_details, not only conflict_examples')
        refs = [item['reference'] for item in values['split_details'] if item['status'] == 'reference_only']
        cands = [item['candidate'] for item in values['split_details'] if item['status'] == 'candidate_only']
        found = 0
        for reference in refs:
            a = set(reference['side'])
            for candidate in cands:
                b = set(candidate['side'])
                quadrants = [a & b, a - b, b - a, universe - (a | b)]
                if not all(quadrants):
                    continue
                found += 1
                if limit is not None and exported >= limit:
                    continue
                exported += 1
                record = {k: v for k, v in info.items() if k != 'comparison'}
                record.update(analysis=str(cohort.directory), view=view, reference_split_id=reference['id'],
                              candidate_split_id=candidate['id'], reference_side_ids=sorted(a), candidate_side_ids=sorted(b),
                              reference_min_side_size=min(len(a), len(universe - a)), candidate_min_side_size=min(len(b), len(universe - b)),
                              reference_support=reference['support'], candidate_support=candidate['support'],
                              reference_tree=info['comparison']['reference']['path'], candidate_tree=info['comparison']['candidate']['path'],
                              quartet_witnesses_for_this_split_pair=math.prod(map(len, quadrants)),
                              witness_interpretation='Choose one tip from each of four quadrants; witnesses overlap across split pairs and must not be summed as a quartet distance.')
                for i, quadrant in enumerate(quadrants, 1):
                    record[f'quadrant_{i}_ids'] = sorted(quadrant)
                    record[f'quadrant_{i}_example_original_headers'] = [names[t] for t in sorted(quadrant)[:5]]
                result.append(record)
        if found != values['incompatible_split_pairs']:
            raise ValueError('Reconstructed conflict count disagrees with the topology comparison')
    return result, {'view': view, 'available': True, 'expected_pairs_in_repeated_observations': expected,
                    'exported_pairs': exported, 'truncated': exported < expected,
                    'interpretation': 'Rows are repeated observations nested within input files. Splits are unrooted; no nominal-species monophyly or biological major-clade status is inferred.'}


def select_detailed_cases(cohort, view, count):
    """One comparison per input, ranked without interpreting biological accuracy."""
    if count <= 0:
        return []
    candidates = [r for r in rows_for(cohort, view) if r['kind'] in ['A_vs_B', 'A_vs_C', 'A_vs_F'] and r['normalized_rf'] > 0]
    candidates.sort(key=lambda r: (-r['incompatible_split_pairs'], -r['normalized_rf'], r['input'], r['candidate_replicate']))
    selected, inputs = [], set()
    for row in candidates:
        if row['input_sha256'] not in inputs:
            selected.append(row); inputs.add(row['input_sha256'])
        if len(selected) >= count:
            break
    return selected


def taxon_stability_rows(cohort, selected, views, data, mapping_cache):
    """Join recorded quartet participation to original labels; do not resample."""
    chosen = {observation_key(row) for row in selected}
    if not chosen:
        return []
    result, seen = [], set()
    for info in detailed_comparisons(cohort):
        key = observation_key(info)
        if key not in chosen:
            continue
        if key in seen:
            raise ValueError('Duplicate detailed comparison while exporting taxon stability')
        seen.add(key)
        mapping = mapping_for(data, info, mapping_cache)
        records = {record['id']: record for record in mapping['records']}
        for view in views:
            value = info['comparison']['views'].get(view)
            if value is None:
                continue
            panel = value['quartets']
            if panel['evaluated'] and 'taxon_participation' not in panel:
                raise ValueError('Rerun tree comparison to record per-taxon quartet participation')
            participation = panel.get('taxon_participation', [])
            if participation:
                ids = [item['id'] for item in participation]
                expected = (set(info['comparison']['duplicate_sensitivity']['retained_ids'])
                            if view.startswith('input_duplicate_representatives') else set(records))
                if len(ids) != len(set(ids)) or set(ids) != expected:
                    raise ValueError('Quartet-participation taxa do not match the comparison view')
                if sum(item['sampled_quartets'] for item in participation) != 4 * panel['evaluated']:
                    raise ValueError('Quartet-participation sample counts are inconsistent')
                if sum(item['discordant_quartets'] for item in participation) != 4 * panel['different']:
                    raise ValueError('Quartet-participation discordance counts are inconsistent')
            for item in participation:
                record = records[item['id']]
                row = {k: v for k, v in info.items() if k != 'comparison'}
                row.update(analysis=str(cohort.directory), view=view,
                           reference_tree=info['comparison']['reference']['path'],
                           candidate_tree=info['comparison']['candidate']['path'],
                           tree_normalized_rf=value['normalized_rf'],
                           quartet_panel_evaluated=panel['evaluated'], quartet_panel_exact=panel['exact'],
                           quartet_panel_seed=panel['seed'], id=item['id'],
                           original_ordinal=record['ordinal'], original_header=record['original_header'],
                           sequence_sha256=record['sequence_sha256'],
                           sampled_quartets=item['sampled_quartets'], discordant_quartets=item['discordant_quartets'],
                           discordance_fraction=item['discordance_fraction'],
                           interpretation='Stability diagnostic: participation in differing quartet resolutions, including resolved/unresolved differences. It is not a placement-error probability or an independent biological observation.')
                result.append(row)
    if seen != chosen:
        raise ValueError('A selected detailed comparison is missing from comparisons.jsonl')
    return result


def tanglegrams(cohort, view, selected, max_tips, data, mapping_cache, requests, output):
    output_rows = []
    for i, row in enumerate(selected, 1):
        mapping = mapping_for(data, row, mapping_cache)
        a = requests[(row['input_sha256'], row['reference_config'], row['reference_replicate'], row['reference_seed'])]
        b = requests[(row['input_sha256'], row['candidate_config'], row['candidate_replicate'], row['candidate_seed'])]
        stem = f"{cohort.stem}-tanglegram-{i:02d}-{slug(row['input'])}-{row['candidate_config']}-r{row['candidate_replicate']}"
        selection = visualize_trees(read_tree(a['tree']), read_tree(b['tree']), svg=output / f'{stem}.svg',
                                    pdf=output / f'{stem}.pdf', mapping=mapping, max_tips=max_tips,
                                    title=f"{row['input']}: A versus {row['candidate_config']} r{row['candidate_replicate']}; seed {row['candidate_seed']}")
        selection.update(protocol_id=cohort.protocol_id, ranking_view=view, ranking_normalized_rf=row['normalized_rf'],
                         ranking_incompatible_split_pairs=row['incompatible_split_pairs'],
                         selection_policy='Largest incompatible-split count, then normalized RF; at most one comparison per input file.',
                         display_interpretation='The display uses original raw trees, pruned only for readability. Red marks absence from the other displayed tree; it does not automatically mean strong support or biological error.')
        write_json(output / f'{stem}.json', selection)
        output_rows.append(selection)
    return output_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--alignment-analysis', type=Path, help='One summarize_study.py output; do not pool distinct MAFFT builds')
    parser.add_argument('--tree-analysis', nargs='+', type=Path, default=[], help='analyze_trees.py outputs, kept as separate stages/cohorts')
    parser.add_argument('--data', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--views', nargs='+', default=DEFAULT_VIEWS)
    parser.add_argument('--formats', nargs='+', choices=['svg', 'pdf', 'png'], default=['svg', 'pdf'])
    parser.add_argument('--tanglegrams', type=int, default=3, help='Maximum distinct files per compatible inference cohort')
    parser.add_argument('--max-tips', type=int, default=40)
    parser.add_argument('--conflict-view', default='strong_support')
    parser.add_argument('--max-conflict-pairs', type=int, help='Optional explicit export limit; truncation is recorded')
    parser.add_argument('--tables-only', action='store_true', help='Export selected cases and machine-readable tables without importing Matplotlib or rendering')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if not args.alignment_analysis and not args.tree_analysis:
        parser.error('Provide at least one completed alignment or tree analysis')
    if args.tanglegrams < 0 or args.max_tips < 4 or (args.max_conflict_pairs is not None and args.max_conflict_pairs < 1):
        parser.error('Invalid display/export limits')
    cohorts = load_cohorts(args.tree_analysis)
    plan = {'alignment_analysis': str(args.alignment_analysis) if args.alignment_analysis else None,
            'tree_cohorts': [{'analysis': str(c.directory), 'protocol_id': c.protocol_id,
                             'protocol': c.protocol, 'comparison_rows': len(c.rows),
                             'available_requested_views': [v for v in args.views if any(r['view'] == v for r in c.rows)]} for c in cohorts],
            'output': str(args.output.resolve()), 'conflict_view': args.conflict_view,
            'formats': args.formats, 'tanglegrams_per_cohort': args.tanglegrams, 'tables_only': args.tables_only,
            'interpretation': 'No cross-stage inference pooling. Repeated comparisons are nested within files. Figure ranges are descriptive, not confidence intervals or biological-accuracy tests.'}
    if args.dry_run:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / 'figure-plan.json', plan)
    files, controls, joined, skipped, conflict_rows, conflicts, displays, participation, selected_cases = [], [], [], [], [], [], [], [], []
    mappings = {}
    metrics = load_alignment_index(args.alignment_analysis) if args.alignment_analysis and cohorts else None
    if args.alignment_analysis and not args.tables_only:
        files += plot_alignment_variation(read_json(args.alignment_analysis / 'summary.json'), args.output, args.formats)
    for cohort in cohorts:
        requests = queue_index(cohort)
        for view in args.views:
            if not any(r['view'] == view for r in cohort.rows):
                continue
            if not args.tables_only:
                files += plot_tree_variation(cohort, view, args.output, args.formats)
                figure_files, table = plot_search_controls(cohort, view, args.output, args.formats)
                files += figure_files; controls += table
            else:
                controls += search_control_rows(cohort, view)
            if metrics is not None:
                points, missing = join_alignment_tree(cohort, view, metrics, requests)
                joined += points; skipped += missing
                if not args.tables_only:
                    files += plot_alignment_tree(cohort, view, points, args.output, args.formats)
        rows, status = split_conflicts(cohort, args.conflict_view, args.data, mappings, args.max_conflict_pairs)
        conflict_rows += rows; conflicts.append(dict(status, protocol_id=cohort.protocol_id, analysis=str(cohort.directory)))
        rank_view = ('strong_support' if any(r['view'] == 'strong_support' and r['incompatible_split_pairs'] > 0 for r in cohort.rows)
                     else 'input_duplicate_representatives_zero_collapsed' if any(r['view'] == 'input_duplicate_representatives_zero_collapsed' for r in cohort.rows)
                     else 'raw')
        selected = select_detailed_cases(cohort, rank_view, args.tanglegrams)
        selected_cases.extend(dict(row, analysis=str(cohort.directory), ranking_view=rank_view) for row in selected)
        participation += taxon_stability_rows(cohort, selected, args.views, args.data, mappings)
        if not args.tables_only:
            displays += tanglegrams(cohort, rank_view, selected, args.max_tips, args.data, mappings, requests, args.output)
    if controls:
        write_table(args.output / 'tree-versus-search-controls.tsv', controls, list(controls[0]))
    if joined:
        write_table(args.output / 'alignment-versus-tree.tsv', joined, list(joined[0]))
    if skipped:
        write_table(args.output / 'alignment-tree-join-exclusions.tsv', skipped, list(skipped[0]))
    if conflict_rows:
        fields = list(dict.fromkeys(key for row in conflict_rows for key in row))
        write_table(args.output / 'unrooted-split-conflicts.tsv', conflict_rows, fields)
    if selected_cases:
        write_table(args.output / 'selected-detailed-cases.tsv', selected_cases, list(selected_cases[0]))
    if participation:
        write_table(args.output / 'taxon-quartet-stability.tsv', participation, list(participation[0]))
    taxa = []
    for sha, mapping in mappings.items():
        representatives = {}
        for record in mapping['records']:
            representative = representatives.setdefault(record['sequence_sha256'], record['id'])
            taxa.append({'input_sha256': sha, 'input': Path(mapping['input']).name, 'id': record['id'],
                         'original_ordinal': record['ordinal'], 'original_header': record['original_header'],
                         'sequence_sha256': record['sequence_sha256'], 'length': record['length'],
                         'first_exact_duplicate_id': representative})
    if taxa:
        write_table(args.output / 'taxa-labels.tsv', taxa, list(taxa[0]))
    write_json(args.output / 'figure-manifest.json', {'schema': 'mafft-round2-figures-v1', 'plan': plan,
               'figures_py_sha256': sha256_file(__file__), 'files': files, 'tanglegrams': displays,
               'conflict_exports': conflicts, 'alignment_tree_joined_points': len(joined),
               'selected_detailed_cases': len(selected_cases), 'taxon_quartet_stability_rows': len(participation),
               'alignment_tree_join_exclusions': len(skipped),
               'source_summaries': [{'path': str(c.directory / 'summary.json'), 'sha256': sha256_file(c.directory / 'summary.json')}
                                    for c in cohorts],
               'source_comparisons': [{'path': str(c.directory / 'comparisons.tsv'), 'sha256': sha256_file(c.directory / 'comparisons.tsv')}
                                      for c in cohorts]})
    print(json.dumps({'output': str(args.output.resolve()), 'plots': len(files), 'tanglegrams': len(displays),
                      'conflict_rows': len(conflict_rows), 'joined_points': len(joined),
                      'taxon_stability_rows': len(participation)}, sort_keys=True))


if __name__ == '__main__':
    main()
