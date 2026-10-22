#!/usr/bin/env python3
"""Audit cached Zhou et al. accession/voucher links without inferring accuracy.

Inputs are the retained primary article XML, GenBank XML and original FASTA.
Missing cached records are unverified, never evidence that a locus is absent.
"""
import argparse
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from analysis import make_mapping, reverse_complement, sha256_file, write_json

LOCI = ['ITS', 'LSU', 'tef1', 'rpb1', 'rpb2', 'mtSSU', 'nucSSU']


def voucher_key(value):
    return re.sub('[^a-z0-9]', '', value.lower())


def match_sequence(sequence, primary):
    for strand, candidate in [('forward', sequence), ('reverse_complement', reverse_complement(sequence))]:
        if candidate == primary:
            return 'exact_' + strand
        if candidate and candidate in primary:
            return 'contiguous_subsequence_' + strand
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--article', type=Path, required=True)
    p.add_argument('--genbank', type=Path, required=True)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    mapping = make_mapping(args.input)
    table = []
    article = ET.parse(args.article).getroot()
    for tr in article.findall('.//table-wrap[@id="t0002"]//tr'):
        cells = [' '.join(''.join(cell.itertext()).split()) for cell in tr]
        if len(cells) == 10:
            table.append({'species': cells[0], 'voucher': cells[1],
                          'loci': dict(zip(LOCI, cells[3:]))})
    if not table:
        raise ValueError('Expected primary seven-locus table t0002')
    gb = {}
    for record in ET.parse(args.genbank).getroot().findall('GBSeq'):
        accession = record.findtext('GBSeq_primary-accession')
        qualifiers = {}
        for q in record.findall('.//GBFeature[GBFeature_key="source"]//GBQualifier'):
            qualifiers.setdefault(q.findtext('GBQualifier_name'), []).append(q.findtext('GBQualifier_value'))
        gb[accession] = {'sequence': record.findtext('GBSeq_sequence', '').upper(),
                         'organism': record.findtext('GBSeq_organism'),
                         'vouchers': qualifiers.get('specimen_voucher', []),
                         'isolates': qualifiers.get('isolate', [])}
    rows = []
    for record in mapping['records']:
        accession = record['original_header'].split()[0].split('.')[0]
        original = gb.get(accession)
        for row in table:
            its = row['loci']['ITS']
            published = gb.get(its)
            direct = accession == its
            voucher = bool(original and published and
                           voucher_key(row['voucher']) in {voucher_key(v) for v in original['vouchers']} and
                           voucher_key(row['voucher']) in {voucher_key(v) for v in published['vouchers']})
            if not (direct or voucher):
                continue
            seqmatch = match_sequence(record['sequence'], original['sequence']) if original else None
            published_match = match_sequence(record['sequence'], published['sequence']) if published else None
            genes = {locus: {'accession': acc, 'cached_primary_record': acc in gb,
                            'organism': gb[acc]['organism'] if acc in gb else None,
                            'voucher_matches': voucher_key(row['voucher']) in {
                                voucher_key(v) for v in gb[acc]['vouchers']} if acc in gb else None,
                            'same_accession_as_ITS': acc == its}
                     for locus, acc in row['loci'].items() if acc and locus != 'ITS'}
            verified_protein = [locus for locus in ['tef1', 'rpb1', 'rpb2'] if
                                genes.get(locus, {}).get('voucher_matches') and seqmatch and published_match]
            rows.append({'id': record['id'], 'original_header': record['original_header'],
                         'input_accession': accession, 'paper_species': row['species'],
                         'paper_voucher': row['voucher'], 'paper_ITS': its,
                         'link_basis': 'same_ITS_accession' if direct else 'exact_primary_voucher',
                         'input_vs_primary_sequence': seqmatch,
                         'input_vs_paper_ITS_sequence': published_match,
                         'non_ITS_candidates': genes, 'verified_protein_loci': verified_protein,
                         'organism_metadata_conflicts': [locus for locus, gene in genes.items()
                             if original and gene['organism'] and gene['organism'] != original['organism']]})
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / 'audit.json', {
        'source_sha256': {str(path.resolve()): sha256_file(path)
                          for path in [args.article, args.genbank, args.input]},
        'input_sha256': mapping['input_sha256'], 'table_rows': len(table),
        'cached_GenBank_records': len(gb), 'candidate_links': rows,
        'matched_records_with_verified_protein_locus': len({r['id'] for r in rows if r['verified_protein_loci']}),
        'interpretation': 'This is a provenance audit, not an independent topology test. The published concatenation includes ITS. Two protein-coding voucher matches do not identify an unrooted quartet. Different organism names remain explicit metadata conflicts, not automatically corrected identities. LSU records sharing the ITS accession require annotated non-ITS extraction before any independent analysis. Uncached candidates remain unverified.'})
    print(json.dumps({'candidate_links': len(rows), 'verified_protein_records': len({r['id'] for r in rows if r['verified_protein_loci']})}))


if __name__ == '__main__':
    main()
