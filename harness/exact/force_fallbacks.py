#!/usr/bin/env python3
"""Force the out-of-memory fallbacks of the fast paths, to test that they are exact.

  force_fallbacks.py SRCDIR SITE[,SITE...]      (or 'all')

SRCDIR is a scratch copy of the tree (core/ and scripts/), never the
repository.  Each SITE's allocation check is made always true, with an
'OOMHIT <site>' line on stderr, so every call takes the fallback.  Build the
copy, run harness/exact/matrix.py against stock without --test-build, and
count the OOMHIT lines in the matrix's runs/*.err to confirm the site ran.

Which build reaches which site (round 4, gcc on x86-64):
  cprf dz igscache fillimp mcbuild   AVX2 -DMAFFT_FILLIMP_DIRECT_BYTES=0
  lfill igsx                         AVX2 -DMAFFT_AVX2_STRIPED=0 (lfill),
                                     with dz left unforced (igsx)
  lfill nxt                          scalar -O3 (the portable Lfill_int)
dz returns before igsx is reached, so do not force both in one build.
"""
import sys
from pathlib import Path


def mark(name):
    return 'fprintf( stderr, "OOMHIT %s\\n" ); ' % name


SITES = {
    'lfill': ('core/Lalign11.c', 'if( profbuf == NULL ) return( 0 );',
              'if( 1 ) { ' + mark('lfill') + 'free( profbuf ); return( 0 ); }', 2),
    'cprf': ('core/fftFunctions.c', 'if( cprf == NULL ) { ccap = 0; goto profiles_original; }',
             '{ ' + mark('cprf') + 'free( cprf ); cprf = NULL; ccap = 0; goto profiles_original; }', 1),
    'igscache': ('core/mltaln9.c', 'if( !c->snap[id] ) { c->ver[id] = 0; return( 0 ); }',
                 '{ ' + mark('igscache') + 'free( c->snap[id] ); c->snap[id] = NULL; c->snapcap[id] = 0; c->ver[id] = 0; return( 0 ); }', 1),
    'igsx': ('core/mltaln9.c', 'if( !gap || !pl || !runbuf || !nrun || !rare1 || !rare2 || !cs )\n\t{',
             'if( 1 || !gap || !pl || !runbuf || !nrun || !rare1 || !rare2 || !cs )\n\t{ ' + mark('igsx'), 1),
    'dz': ('core/mltaln9.c', 'if( !dz || !dzi ) { tabsrc = NULL;',
           'if( 1 || !dz || !dzi ) { ' + mark('dz') + 'tabsrc = NULL;', 1),
    'nxt': ('core/mltaln9.c', 'if( !nxtbuf || !runbuf',
            'if( ( ' + mark('nxt').rstrip('; ') + ', 1 ) || !nxtbuf || !runbuf', 1),
    'fillimp': ('core/mltaln9.c', 'if( !seg || !bstart || !bfill ) {',
                'if( 1 || !seg || !bstart || !bfill ) { ' + mark('fillimp'), 1),
    'mcbuild': ('core/partSalignmm.c', 'if( !key || !colgrp || !keyoff || !keycnt || !hash || !tab ) goto nomem;',
                'if( 1 || !key || !colgrp || !keyoff || !keycnt || !hash || !tab ) { ' + mark('mcbuild') + 'goto nomem; }', 1),
}


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    root = Path(sys.argv[1])
    if (root / '.git').exists():
        raise SystemExit('refusing to edit a git checkout; use a scratch copy')
    names = sorted(SITES) if sys.argv[2] == 'all' else sys.argv[2].split(',')
    for name in names:
        path, old, new, count = SITES[name]
        f = root / path
        text = f.read_bytes()
        if text.count(old.encode()) != count:
            raise SystemExit('%s: expected %d match(es) in %s, found %d' % (name, count, path, text.count(old.encode())))
        f.write_bytes(text.replace(old.encode(), new.encode()))
    print('forced: ' + ' '.join(names))


if __name__ == '__main__':
    main()
