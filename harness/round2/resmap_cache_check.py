#!/usr/bin/env python3
"""Generate an actual-source residue-map-cache differential checker.

  python3 harness/round2/resmap_cache_check.py --source core/mltaln9.c --output NEW

Generation alone does not invoke a compiler or native program. Add --run only
in an assigned correctness window. All native variants run sequentially.
The checker compares exact double bytes, including initialized matrix padding,
and exact rowlo/rowhi bytes. Counters establish coverage, never performance.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


_C_TOKENS = re.compile(
    r'"(?:\\[\s\S]|[^"\\])*"|'
    r"'(?:\\[\s\S]|[^'\\])*'|"
    r'/\*[\s\S]*?\*/|//(?:\\(?:\r\n|\r|\n)|[^\r\n])*')
_C_LITERALS = re.compile(r'"(?:\\[\s\S]|[^"\\])*"|'
                         r"'(?:\\[\s\S]|[^'\\])*'")


def _blank(text):
    """Preserve byte/character offsets and CR/LF layout."""
    return re.sub(r"[^\r\n]", " ", text)


def mask_c_comments(source):
    """Blank comments while preserving string/character literals exactly.

    The lexer consumes literals as whole tokens, so comment-looking bytes in a
    literal cannot begin a comment. Latin-1 source decoding makes each retained
    position correspond to the same byte in the original file.
    """
    return _C_TOKENS.sub(
        lambda match: _blank(match.group()) if match.group().startswith(("//", "/*"))
        else match.group(), source)


def _code_mask(source):
    # A second positional mask lets structural parsing ignore punctuation in
    # literals too, without changing mask_c_comments' literal-preserving API.
    return _C_LITERALS.sub(lambda match: _blank(match.group()), mask_c_comments(source))


def _function_header(source, name):
    code = _code_mask(source)
    pattern = (r"(?m)^(?:static\s+)?(?:void|int|double)\s+\*?\s*" +
               re.escape(name) + r"\s*\(")
    definitions = []
    for match in re.finditer(pattern, code):
        opening = match.end() - 1
        depth, closing = 1, opening + 1
        while closing < len(code) and depth:
            if code[closing] == "(":
                depth += 1
            elif code[closing] == ")":
                depth -= 1
            closing += 1
        if depth:
            raise ValueError(f"Unterminated parameter list of {name}")
        after = closing
        while after < len(code) and code[after].isspace():
            after += 1
        if after < len(code) and code[after] == "{":
            definitions.append((match.start(), opening, closing - 1, after))
    if len(definitions) != 1:
        raise ValueError(f"Expected one definition of {name}, found {len(definitions)}")
    return code, definitions[0]


def function(source, name):
    code, (start, _, _, opening) = _function_header(source, name)
    depth = 0
    for position in range(opening, len(code)):
        if code[position] == "{":
            depth += 1
        elif code[position] == "}":
            depth -= 1
            if depth == 0:
                return start, position + 1, source[start:position + 1]
    raise ValueError(f"Unterminated definition of {name}")


def function_parameters(source, name):
    """Return parameters from the same positional mask used for matching."""
    code, (_, opening, closing, _) = _function_header(source, name)
    parameters, start, depth = [], opening + 1, 0
    for position in range(start, closing):
        if code[position] in "([":
            depth += 1
        elif code[position] in ")]":
            depth -= 1
        elif code[position] == "," and depth == 0:
            parameters.append(code[start:position].strip())
            start = position + 1
    parameters.append(code[start:closing].strip())
    return [] if parameters == [""] or parameters == ["void"] else parameters


def null_arguments(source, name):
    return ["NULL" if "*" in parameter or "[" in parameter else "0"
            for parameter in function_parameters(source, name)]


def replace_function(source, name, transform):
    start, end, text = function(source, name)
    return source[:start] + transform(text) + source[end:]


def replace_exact(source, old, new, count=1):
    if source.count(old) != count:
        raise ValueError(f"Expected {count} instances of {old!r}")
    return source.replace(old, new)


DRIVER = r'''
#include "include/mltaln.h"
#include <stdint.h>
#include <inttypes.h>
#include <float.h>
_Static_assert(sizeof(double)==8 && DBL_MANT_DIG==53, "binary64 required");
int njob;
double fastathreshold = 1.75;
static TLS uint64_t gets, hits, stores, aliases, bandcalls, banddone, failures;
static TLS uint64_t comparisons, cells, peak_bytes, rng, seed;
static TLS unsigned fail_after, random_cases;
static TLS const char *current_case = "setup";
static uint64_t tls_comparisons;
static void die(const char *why)
{
    fprintf(stderr,"RESMAP_CHECK %s: %s (seed=%" PRIu64 ")\n",current_case,why,seed);
    exit(1);
}
static void *checked_malloc(size_t n)
{
    void *p=malloc(n?n:1); if(!p) die("driver allocation failed"); return p;
}
static void *optional_malloc(size_t n)
{
    if(fail_after && --fail_after==0) { failures++; return NULL; }
    return malloc(n);
}
static void *optional_calloc(size_t n,size_t s)
{
    if(fail_after && --fail_after==0) { failures++; return NULL; }
    return calloc(n,s);
}
#define MAFFT_FILLIMP_RESMAP_CACHE 1
#include "helpers-instrumented.c"
#include "fill0.c"
#include "fill1.c"
#include "cleanup-wrapper.c"

enum { GROUPS=4, WIDTH=260, SEGMENTS=3 };
typedef struct {
    int n1,n2,w1,w2,ids1[GROUPS],ids2[GROUPS],use1,use2,useswap,nohom,overrun;
    char text1[GROUPS][WIDTH+1],text2[GROUPS][WIDTH+1],swap[GROUPS];
    char *seq1[GROUPS],*seq2[GROUPS];
    double e1[GROUPS],e2[GROUPS],k1[GROUPS],k2[GROUPS];
    LocalHom nodes[GROUPS][GROUPS][SEGMENTS];
    LocalHom *pairs[GROUPS][GROUPS],**lh[GROUPS];
} Fixture;
typedef struct {
    double *arena,**row;
    unsigned char *used;
    int *lobase,*hibase,*lo,*hi,h,w;
    size_t count;
} Matrix;
typedef void (*Fill)(double**,double*,int,int,int,int,char**,char**,double*,double*,
                     double*,double*,LocalHom***,char*,int,int*,int*,int*,int*);

static uint64_t word(void)
{
    uint64_t z=(rng+=UINT64_C(0x9e3779b97f4a7c15));
    z=(z^(z>>30))*UINT64_C(0xbf58476d1ce4e5b9);
    z=(z^(z>>27))*UINT64_C(0x94d049bb133111eb);
    return z^(z>>31);
}
static void text(char *s,int width)
{
    static const char letters[]="ACGTURYSWKMBDHVNacgtn?.ARNDCEQGHILKFPSTWYV";
    int i;
    for(i=0;i<width;i++) {
        uint64_t v=word();
        s[i]=(v%4==0)?'-':letters[(v>>3)%(sizeof(letters)-1)];
    }
    s[width]=0;
}
static void fixture(Fixture *f,int jobs,int n1,int n2,int w1,int w2)
{
    int i;
    memset(f,0,sizeof(*f)); njob=jobs;
    f->n1=n1; f->n2=n2; f->w1=w1; f->w2=w2; f->use1=f->use2=1;
    for(i=0;i<n1;i++) {
        text(f->text1[i],w1); f->seq1[i]=f->text1[i]; f->ids1[i]=i;
        f->e1[i]=(i==0)?-0.0:(i+1)/7.0; f->k1[i]=(i+2)/11.0;
        f->swap[i]=(char)(i&1);
    }
    for(i=0;i<n2;i++) {
        text(f->text2[i],w2); f->seq2[i]=f->text2[i]; f->ids2[i]=n1+i;
        f->e2[i]=(i+3)/13.0; f->k2[i]=(i==1)?0.0:(i+1)/5.0;
    }
}
static void localhom(Fixture *f)
{
    int i,j,k,a,b,count1[GROUPS],count2[GROUPS],scratch[WIDTH+1];
    memset(f->nodes,0,sizeof(f->nodes)); memset(f->pairs,0,sizeof(f->pairs));
    for(i=0;i<f->n1;i++) { count1[i]=makeresmap(f->seq1[i],scratch); f->lh[i]=f->pairs[i]; }
    for(j=0;j<f->n2;j++) count2[j]=makeresmap(f->seq2[j],scratch);
    for(i=0;i<f->n1;i++) for(j=0;j<f->n2;j++) {
        int swapped=f->useswap ? f->swap[i] :
                    (f->use1 && f->use2 && f->ids1[i]>f->ids2[j]);
        if(f->nohom || !count1[i] || !count2[j]) continue;
        f->pairs[i][j]=f->nodes[i][j];
        for(k=0;k<SEGMENTS;k++) {
            LocalHom *p=&f->nodes[i][j][k];
            int length=MIN(count1[i],count2[j]);
            a=k%count1[i]; b=(k*2)%count2[j];
            length=MIN(length,MIN(count1[i]-a,count2[j]-b));
            if(length>5+k) length=5+k;
            if(swapped) { p->start1=b; p->end1=b+length-1; p->start2=a; p->end2=a+length-1; }
            else { p->start1=a; p->end1=a+length-1; p->start2=b; p->end2=b+length-1; }
            if(f->overrun && k==2) { p->end1+=count1[i]+count2[j]+3; p->end2+=count1[i]+count2[j]+3; }
            p->importance=(k==0)?-0.0:((i+j+k)&1?-1.0:1.0)*(k+1)/17.0;
            p->korh=(k&1)?'k':'h';
            p->next=(k+1<SEGMENTS)?p+1:NULL;
        }
    }
}
static Matrix matrix(int h,int w,int tracked,int uneven)
{
    Matrix m;
    size_t i,at=3;
    uint64_t guard=UINT64_C(0x7ff8123400012345);
    memset(&m,0,sizeof(m)); m.h=h; m.w=w;
    m.count=(size_t)(h+2)*(w+13)+10;
    m.arena=checked_malloc(m.count*sizeof(double));
    m.used=calloc(m.count,1);
    m.row=checked_malloc((size_t)(h+1)*sizeof(double*));
    m.lobase=checked_malloc((size_t)(h+2)*sizeof(int));
    m.hibase=checked_malloc((size_t)(h+2)*sizeof(int));
    if(!m.used) die("driver bitmap allocation failed");
    m.lo=m.lobase+1; m.hi=m.hibase+1;
    for(i=0;i<m.count;i++) memcpy(m.arena+i,&guard,sizeof(guard));
    for(i=0;i<(size_t)(h+2);i++) m.lobase[i]=m.hibase[i]=0x195c4a3;
    m.row[0]=m.arena+at; /* valid even for an empty first profile */
    for(i=0;i<(size_t)h;i++) {
        int j;
        m.row[i]=m.arena+at;
        for(j=0;j<w;j++) {
            m.used[at+j]=1;
            m.row[i][j]=tracked?0.0:((int)((i+j)%7)-3)/19.0;
        }
        m.lo[i]=w+2; m.hi[i]=-1;
        at+=(size_t)w+7+(uneven?i%3:0);
    }
    return m;
}
static void release(Matrix *m)
{
    free(m->arena);free(m->used);free(m->row);free(m->lobase);free(m->hibase);
}
static void invoke(Fill fn,Fixture *f,Matrix *m,int tracked)
{
    fn(m->row,NULL,f->n1,f->n2,f->w1,f->w2,f->seq1,f->seq2,
       f->e1,f->e2,f->k1,f->k2,f->lh,f->useswap?f->swap:NULL,1,
       f->use1?f->ids1:NULL,f->use2?f->ids2:NULL,
       tracked?m->lo:NULL,tracked?m->hi:NULL);
}
static void compare(Fixture *f,const char *name,int tracked,int uneven)
{
    Matrix a=matrix(f->w1,f->w2,tracked,uneven),b=matrix(f->w1,f->w2,tracked,uneven);
    Fixture before;
    uint64_t guard=UINT64_C(0x7ff8123400012345);
    size_t i;
    current_case=name; localhom(f); before=*f;
    invoke(fillimp0,f,&a,tracked); invoke(fillimp1,f,&b,tracked);
    if(memcmp(&before,f,sizeof(*f))) die("input fixture was modified");
    if(memcmp(a.arena,b.arena,a.count*sizeof(double))) die("double bytes differ");
    if(memcmp(a.lobase,b.lobase,(size_t)(a.h+2)*sizeof(int)) ||
       memcmp(a.hibase,b.hibase,(size_t)(a.h+2)*sizeof(int))) die("rowlo/rowhi bytes differ");
    for(i=0;i<a.count;i++) if(!a.used[i] && memcmp(a.arena+i,&guard,sizeof(guard))) die("padding overwritten");
    if(a.lobase[0]!=0x195c4a3 || a.lobase[a.h+1]!=0x195c4a3 ||
       a.hibase[0]!=0x195c4a3 || a.hibase[a.h+1]!=0x195c4a3) die("row-boundary guard overwritten");
    if(fi_resmap_bytes>(size_t)MAFFT_FILLIMP_RESMAP_CACHE_BYTES) die("cache accounting exceeds cap");
    if(fi_resmap_bytes>peak_bytes) peak_bytes=fi_resmap_bytes;
    comparisons++; cells+=(uint64_t)a.h*a.w;
    release(&a);release(&b);
}
static void modes(Fixture *f,const char *name)
{
    int tracked,uneven;
    for(tracked=0;tracked<2;tracked++) for(uneven=0;uneven<2;uneven++)
        compare(f,name,tracked,uneven);
}
static void cleanup(void)
{
    fillimp_cached(NULL,NULL,0,0,0,0,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,0,NULL,NULL);
    if(fi_resmap_entries || fi_resmap_bytes || fi_resmap_nslots || fi_resmap_active || fi_resmap_epoch)
        die("NULL cleanup did not release cache");
}
static void map_examples(void)
{
    const char *s[]={"","---","A","-A-","A-C--G-"};
    const int nr[]={0,0,1,1,3},expected[][3]={{0},{0},{0},{1},{0,2,5}};
    int i,j,map[20],n;
    for(i=0;i<5;i++) {
        for(j=0;j<20;j++) map[j]=-70707;
        n=makeresmap((char*)s[i],map);
        if(n!=nr[i] || memcmp(map,expected[i],(size_t)n*sizeof(int))) die("explicit residue map differs");
        if(map[n]!=-70707) die("makeresmap unexpectedly writes a sentinel");
    }
}
typedef struct {
    pthread_mutex_t mutex;
    pthread_cond_t cond;
    int arrived;
    uintptr_t tls_address[2],entries_address[2];
} ThreadSync;
typedef struct {
    Fixture f;
    ThreadSync *sync;
    int index,fits;
    uint64_t initial_seed,completed;
} ThreadCase;
static void *thread_case(void *opaque)
{
    ThreadCase *t=opaque;
    int expected[WIDTH+1],n;
    seed=rng=t->initial_seed;
    modes(&t->f,"pthread-warm");
    pthread_mutex_lock(&t->sync->mutex);
    t->sync->tls_address[t->index]=(uintptr_t)&fi_resmap_epoch;
    t->sync->entries_address[t->index]=(uintptr_t)fi_resmap_entries;
    t->sync->arrived++;
    if(t->sync->arrived==2) pthread_cond_broadcast(&t->sync->cond);
    while(t->sync->arrived<2) pthread_cond_wait(&t->sync->cond,&t->sync->mutex);
    pthread_mutex_unlock(&t->sync->mutex);
    if(t->fits) {
        if(!fi_resmap_entries || strcmp(fi_resmap_entries[0].snapshot,t->f.text1[0]))
            die("thread-local snapshot was replaced by the other thread");
        n=makeresmap(t->f.text1[0],expected);
        if(n!=fi_resmap_entries[0].nres ||
           memcmp(expected,fi_resmap_entries[0].map,(size_t)n*sizeof(int)))
            die("thread-local map differs after synchronization");
    }
    modes(&t->f,"pthread-reuse-after-other-thread-warm");
    t->completed=comparisons;
    cleanup();
    return NULL;
}
static void threads(int fits)
{
    ThreadSync sync;
    ThreadCase cases[2];
    pthread_t handles[2];
    int i;
    memset(&sync,0,sizeof(sync)); memset(cases,0,sizeof(cases));
    pthread_mutex_init(&sync.mutex,NULL); pthread_cond_init(&sync.cond,NULL);
    /* njob and fastathreshold remain shared read-only while these workers run.
     * Fixture generation happens in the parent; only cache state is TLS. */
    for(i=0;i<2;i++) {
        fixture(&cases[i].f,4,1,1,3,4);
        strcpy(cases[i].f.text1[0],i?"-AC":"A-C");
        strcpy(cases[i].f.text2[0],i?"G-TA":"GT-A");
        cases[i].sync=&sync; cases[i].index=i; cases[i].fits=fits;
        cases[i].initial_seed=seed+(unsigned)i+1;
    }
    for(i=0;i<2;i++) if(pthread_create(&handles[i],NULL,thread_case,&cases[i])) die("pthread_create");
    for(i=0;i<2;i++) if(pthread_join(handles[i],NULL)) die("pthread_join");
    if(sync.tls_address[0]==sync.tls_address[1]) die("cache epoch variable is not TLS");
    if(fits && (!sync.entries_address[0] || !sync.entries_address[1] ||
                sync.entries_address[0]==sync.entries_address[1])) die("threads shared cache slots");
    tls_comparisons=cases[0].completed+cases[1].completed;
    pthread_cond_destroy(&sync.cond); pthread_mutex_destroy(&sync.mutex);
}
int main(int argc,char **argv)
{
    static Fixture f;
    int i,j;
    int widths[]={0,1,15,16,31,32,63,64,127,128,129,255};
    uint64_t prior;
    int fits=(size_t)MAFFT_FILLIMP_RESMAP_CACHE_BYTES>=4*sizeof(fillimp_resmap_entry)+45;
    if(argc!=3) return 2;
    seed=rng=strtoull(argv[1],NULL,10); random_cases=(unsigned)strtoul(argv[2],NULL,10);
    map_examples();
    fixture(&f,4,1,1,3,4); modes(&f,"initial"); modes(&f,"unchanged");
    if(fits && (!hits || !stores)) die("hit/store paths not exercised");
    fixture(&f,8,3,3,65,73); modes(&f,"regroup-base");
    for(i=0;i<3;i++) { f.ids1[i]=5-i; f.ids2[i]=i; }
    f.useswap=1; modes(&f,"regroup-and-swap");
    f.text1[0][0]=(f.text1[0][0]=='A')?'C':'A'; modes(&f,"same-length-edit");
    fixture(&f,4,2,2,6,6);
    strcpy(f.text1[0],"A--CGT"); strcpy(f.text1[1],f.text1[0]);
    strcpy(f.text2[0],"A-C-GT"); f.ids1[1]=f.ids1[0]; f.ids2[0]=f.ids1[0];
    modes(&f,"same-and-conflicting-ID-alias");
    f.use1=0; modes(&f,"missing-first-IDs"); f.use1=1; f.use2=0; modes(&f,"missing-second-IDs");
    f.use2=1; f.ids1[0]=-1; modes(&f,"negative-ID");
    f.ids1[0]=njob; modes(&f,"ID-at-upper-bound");
    f.ids1[0]=INT_MAX; modes(&f,"huge-ID"); f.ids1[0]=0;
    njob=MAFFT_FILLIMP_RESMAP_CACHE_MAXSLOTS+1; modes(&f,"too-many-slots");
    njob=0; modes(&f,"zero-job-count"); njob=4; modes(&f,"valid-ID-restoration");
    for(i=0;i<(int)(sizeof(widths)/sizeof(widths[0]));i++) {
        fixture(&f,8,3,3,widths[i],widths[(i+3)%12]); modes(&f,"length-boundaries");
    }
    fixture(&f,12,4,4,193,181); modes(&f,"slot-growth");
    fixture(&f,4,2,2,33,35); modes(&f,"slot-shrink");
    memset(f.text1[0],'-',33); modes(&f,"all-gap-row-no-corresponding-LH");
    f.nohom=1; modes(&f,"no-local-homology");
    fixture(&f,6,3,3,45,49); f.overrun=1; modes(&f,"original-out-of-range-column-walk");
    for(i=0;i<(int)random_cases;i++) {
        int n1=(int)(word()%GROUPS)+1,n2=(int)(word()%GROUPS)+1;
        int w1=(int)(word()%(WIDTH+1)),w2=(int)(word()%(WIDTH+1));
        fixture(&f,n1+n2,n1,n2,w1,w2);
        f.useswap=(int)(word()&1); fastathreshold=(i%3)?1.75:-0.0;
        modes(&f,"seeded-variable-context"); modes(&f,"seeded-unchanged-rows");
    }
    fastathreshold=1.75;
    if(fits) {
        void *saved_entries;
        size_t saved_bytes;
        uint64_t saved_epoch;
        cleanup(); fixture(&f,4,1,1,3,4); compare(&f,"retention-prime",1,1);
        saved_entries=fi_resmap_entries; saved_bytes=fi_resmap_bytes; saved_epoch=fi_resmap_epoch;
        f.use1=0; compare(&f,"warm-then-unsupported-retains-cache",1,1);
        if(fi_resmap_entries!=saved_entries || fi_resmap_bytes!=saved_bytes ||
           fi_resmap_epoch!=saved_epoch || fi_resmap_active) die("unsupported call changed retained cache");
        f.use1=1; compare(&f,"retention-valid-again",1,1); cleanup();
        for(i=1;i<=7;i++) {
            cleanup(); fixture(&f,4,1,1,3,4);
            fail_after=(unsigned)i; prior=failures;
            compare(&f,"optional-allocation-OOM",1,1); fail_after=0;
            if(failures!=prior+1) die("requested optional OOM was not reached");
        }
        for(i=3;i<=4;i++) {
            cleanup(); fixture(&f,4,1,1,3,4); compare(&f,"replacement-prime",1,0);
            f.text1[0][0]=(f.text1[0][0]=='A')?'C':'A';
            fail_after=(unsigned)i; prior=failures;
            compare(&f,"replacement-OOM",1,1); fail_after=0;
            if(failures!=prior+1) die("replacement OOM was not reached");
        }
        cleanup(); fixture(&f,4,1,1,3,4); compare(&f,"wrap-prime",1,0);
        fi_resmap_epoch=UINT64_MAX;
        for(j=0;j<fi_resmap_nslots;j++) fi_resmap_entries[j].pin=1;
        f.text1[0][0]=(f.text1[0][0]=='A')?'C':'A';
        compare(&f,"epoch-wrap",1,1);
        if(fi_resmap_epoch!=1) die("epoch wrap did not reset to one");
    }
    cleanup(); cleanup();
    fixture(&f,4,1,1,3,4); modes(&f,"after-cleanup"); cleanup();
    threads(fits);
    if(fits && !aliases) die("pinned conflicting-ID alias path not exercised");
    if(FILLIMP_BAND>0 && (!bandcalls || !banddone)) die("banded helper not exercised");
    printf("{\"status\":\"passed\",\"comparisons\":%" PRIu64 ",\"cells\":%" PRIu64
           ",\"get_calls\":%" PRIu64 ",\"hits\":%" PRIu64 ",\"stores\":%" PRIu64
           ",\"fallbacks\":%" PRIu64 ",\"pinned_alias_fallbacks\":%" PRIu64
           ",\"optional_OOMs\":%" PRIu64 ",\"band_calls\":%" PRIu64
           ",\"band_success\":%" PRIu64 ",\"max_accounted_cache_bytes\":%" PRIu64
           ",\"pthread_comparisons\":%" PRIu64 ",\"cap\":%zu,\"seed\":%" PRIu64 "}\n",comparisons,cells,gets,hits,stores,
           gets-hits-stores,aliases,failures,bandcalls,banddone,peak_bytes,
           tls_comparisons,(size_t)MAFFT_FILLIMP_RESMAP_CACHE_BYTES,seed);
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cc", default="gcc")
    parser.add_argument("--seed", default=20261020, type=lambda s: int(s, 0))
    parser.add_argument("--random-cases", type=int, default=40)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.seed < 2**64 or not 0 <= args.random_cases <= 10000:
        parser.error("seed must fit uint64; random-cases must be 0..10000")
    path = args.source.resolve()
    raw = path.read_bytes()
    source = raw.decode("latin-1")
    core = path.parent
    start = function(source, "movereg")[0]
    track_start, _, track = function(source, "fillimp_track")
    common = source[start:track_start]
    for name in ("makeresmap", "fillimp_resmap_cache_begin", "fillimp_resmap_cache_get",
                 "fillimp_banded"):
        function(common, name)
    instrumented = replace_function(common, "fillimp_resmap_cache_begin",
                                   lambda s: replace_exact(s, "calloc(", "optional_calloc("))

    def instrument_get(text):
        text = replace_exact(text, "malloc(", "optional_malloc(", 2)
        text = text.replace("{", "{\n\tgets++;", 1)
        text = replace_exact(text, "return( entry->map );", "hits++; return( entry->map );")
        text = replace_exact(text, "return( map );", "stores++; return( map );")
        return replace_exact(text, "if( entry->pin == fi_resmap_epoch ) return( NULL );",
                             "if( entry->pin == fi_resmap_epoch ) { aliases++; return( NULL ); }")

    instrumented = replace_function(instrumented, "fillimp_resmap_cache_get", instrument_get)

    def instrument_band(text):
        text = text.replace("{", "{\n\tbandcalls++;", 1)
        return replace_exact(text, "return( 1 );", "{ banddone++; return( 1 ); }", 2)

    instrumented = replace_function(instrumented, "fillimp_banded", instrument_band)
    fill0 = "#undef MAFFT_FILLIMP_RESMAP_CACHE\n#define MAFFT_FILLIMP_RESMAP_CACHE 0\n" + \
        replace_exact(track, "void fillimp_track(", "static void fillimp0(") + "\n"
    fill1 = "#undef MAFFT_FILLIMP_RESMAP_CACHE\n#define MAFFT_FILLIMP_RESMAP_CACHE 1\n" + \
        replace_exact(track, "void fillimp_track(", "static void fillimp1(") + "\n"
    for group in ("owned1", "owned2"):
        fill1 = replace_exact(fill1, f"{group} = malloc(", f"{group} = optional_malloc(")
    wrapper = function(source, "fillimp")[2]
    wrapper = replace_exact(wrapper, "void fillimp(", "static void fillimp_cached(")
    wrapper = replace_exact(wrapper, "fillimp_track(", "fillimp1(")
    cleanup_checks = {}
    public_calls = []
    required = {
        "Salignmm.c": ["imp_match_init_strict", "A__align", "A__align_variousdist"],
        "partSalignmm.c": ["part_imp_match_init_strict", "partA__align", "partA__align_variousdist"],
        "Dalignmm.c": ["imp_match_init_strictD", "D__align", "D__align_variousdist"],
    }
    core_hashes = {path.name: hashlib.sha256(raw).hexdigest()}
    for filename, names in required.items():
        data = (core / filename).read_bytes()
        core_hashes[filename] = hashlib.sha256(data).hexdigest()
        for name in names:
            text = function(data.decode("latin-1"), name)[2]
            null = text.index("if( seq1 == NULL )")
            null_end = text.index("\n\t}", null) + len("\n\t}")
            null_branch = text[null:null_end]
            forward = null_branch.index("fillimp( NULL, NULL")
            allocation_guard = null_branch.find("if( orlgth1 )")
            if allocation_guard != -1 and forward > allocation_guard:
                raise ValueError(f"{name}: cleanup forwarding is allocation-dependent")
            cleanup_checks[name] = "NULL cleanup forwards before DP allocation guard"
            arguments = null_arguments(text, name)
            public_calls.append((name, f"{name}({','.join(arguments)});"))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "include").mkdir()
    header_hashes = {}
    for header in sorted(core.glob("*.h")):
        shutil.copyfile(header, output / "include" / header.name)
        header_hashes[header.name] = hashlib.sha256(header.read_bytes()).hexdigest()
    pieces = {"helpers-original.c": common, "helpers-instrumented.c": instrumented,
              "fill-original.c": track, "fill0.c": fill0, "fill1.c": fill1,
              "cleanup-wrapper.c": wrapper, "check.c": DRIVER}
    for filename, text in pieces.items():
        (output / filename).write_bytes(text.encode("latin-1"))
    (output / "source-mltaln9.c").write_bytes(raw)
    probe = r'''
/* Full-object integration probe. Includes the exact source to inspect private
 * cache state; link the other MAFFT common objects, excluding mltaln9.o and
 * application-main objects. All four changed objects need the same cache flag. */
#define MAFFT_FILLIMP_RESMAP_CACHE 1
#include "source-mltaln9.c"
static void warm_maps_without_DP(void)
{
    char *a[]={"A-C"},*b[]={"-AC"};
    double block[3][3]={{0}},*matrix[]={block[0],block[1],block[2]},eff[]={1.0};
    int ida[]={0},idb[]={1};
    LocalHom *pair[]={NULL},**lh[]={pair};
    njob=2;fastathreshold=1.75;
    fillimp(matrix,NULL,1,1,3,3,a,b,eff,eff,eff,eff,lh,NULL,1,ida,idb);
    if(!fi_resmap_entries || !fi_resmap_bytes || fi_resmap_active) abort();
}
int main(void)
{
'''
    for name, call in public_calls:
        probe += f'    warm_maps_without_DP();\n    {call}\n'
        probe += ('    if(fi_resmap_entries || fi_resmap_bytes || fi_resmap_nslots || '
                  f'fi_resmap_active) {{ fprintf(stderr,"cleanup failed: {name}\\n"); abort(); }}\n')
    probe += '    puts("PUBLIC_RESMAP_CLEANUP: 9 never-allocated DP/importance paths passed");\n    return 0;\n}\n'
    (output / "public_cleanup_probe.c").write_text(probe)
    strict = ["-std=gnu11", "-march=native", "-Denablemultithread", "-pthread", "-g",
              "-ffp-contract=off", "-fno-fast-math", "-fexcess-precision=standard",
              "-DMAFFT_STOCK_FMA=0", "-DMAFFT_FILLIMP_DIRECT_BYTES=0"]
    variants = [
        ("strict-band", ["-O3", "-DFILLIMP_BAND=128"]),
        ("strict-direct", ["-O3", "-DFILLIMP_BAND=0"]),
        ("strict-tiny", ["-O3", "-DFILLIMP_BAND=128", "-DMAFFT_FILLIMP_RESMAP_CACHE_BYTES=1024"]),
        ("strict-zero-cap", ["-O3", "-DFILLIMP_BAND=128", "-DMAFFT_FILLIMP_RESMAP_CACHE_BYTES=0"]),
        ("sanitized-band", ["-O1", "-DFILLIMP_BAND=128", "-fno-omit-frame-pointer",
                            "-fno-pie", "-no-pie",
                            "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]),
        ("sanitized-tiny", ["-O1", "-DFILLIMP_BAND=128", "-DMAFFT_FILLIMP_RESMAP_CACHE_BYTES=1024",
                            "-fno-pie", "-no-pie",
                            "-fno-omit-frame-pointer", "-fsanitize=address,undefined",
                            "-fno-sanitize-recover=all"]),
    ]
    manifest = {
        "status": "prepared", "source": str(path), "core_sha256": core_hashes,
        "headers_sha256": header_hashes, "cleanup_checks": cleanup_checks,
        "pieces_sha256": {k: hashlib.sha256(v.encode("latin-1")).hexdigest() for k, v in pieces.items()},
        "checker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "public_cleanup_probe": {
            "status": "not-built-not-run",
            "sha256": hashlib.sha256(probe.encode()).hexdigest(),
            "separate_full_object_link_required": True,
            "instructions": "Compile public_cleanup_probe.c with -I include, cache=1, and strict "
                            "GCC flags; link MAFFT common objects excluding mltaln9.o and program "
                            "main objects. Rebuild Salignmm.o, partSalignmm.o, Dalignmm.o with cache=1.",
        },
        "command": sys.argv, "seed": args.seed, "random_cases": args.random_cases,
        "instrumentation": ["optional allocator failure injection only in cache helpers/ownership arrays",
                            "integer get/hit/store/alias/band coverage counters",
                            "fillimp_track renamed fillimp0/1 under macro0/1"],
        "builds": [],
    }
    for name, flags in variants:
        manifest["builds"].append({
            "name": name, "build": [args.cc, *strict, *flags, str(output / "check.c"),
                                    "-lm", "-o", str(output / name)],
            "run": [str(output / name), str(args.seed), str(args.random_cases)]})
    report = output / "manifest.json"

    def save():
        report.write_text(json.dumps(manifest, indent=2) + "\n")

    save()
    if args.run:
        env = dict(os.environ, ASAN_OPTIONS="detect_leaks=1:halt_on_error=1:abort_on_error=1",
                   UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
        try:
            version = subprocess.run([args.cc, "--version"], capture_output=True, text=True, check=True)
            (output / "compiler-version.txt").write_text(version.stdout + version.stderr)
            for build in manifest["builds"]:
                for phase in ("build", "run"):
                    with (output / f"{build['name']}-{phase}.stdout").open("w") as stdout, \
                         (output / f"{build['name']}-{phase}.stderr").open("w") as stderr:
                        subprocess.run(build[phase], stdout=stdout, stderr=stderr, env=env, check=True)
                build["result"] = json.loads((output / f"{build['name']}-run.stdout").read_text())
                if build["result"].get("status") != "passed":
                    raise ValueError(f"{build['name']} did not report success")
                save()
            manifest["status"] = "passed"
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            manifest.update(status="failed", error=str(error)); save()
            print(f"Failed: {error}; inspect {report}", file=sys.stderr)
            return 1
        save()
    print(json.dumps({"status": manifest["status"], "manifest": str(report)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
