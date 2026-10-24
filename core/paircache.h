/* Exact reuse of repeated ordered sequence pairs within one pairalign() invocation.
 *
 * Group membership requires strcmp equality of the actual ungapped input strings;
 * hashing only finds candidates. A key is ordered: (A,B) and (B,A) are distinct,
 * since MAFFT's traceback need not be symmetric. Only the fixed-matrix, zero-offset
 * L__align11_pair call is cached; dynamic-matrix realignment remains outside it.
 * Constants, penalties and alphabet are fixed during pairalign(). All distance,
 * localhom and output processing still runs for every original pair, in its usual order.
 *
 * Entries are immutable after publication, shared under a mutex, and released only
 * after every worker has joined. No memory or scoring state survives pairalign().
 * Table <= 16 MiB; table plus result strings <= 64 MiB, regardless of input size.
 * A failed allocation or exhausted budget simply recomputes the original alignment.
 */
#ifndef MAFFT_DUPLICATE_CACHE
#define MAFFT_DUPLICATE_CACHE 1
#endif
#if MAFFT_DUPLICATE_CACHE && !defined(__APPLE__) && !defined(MAFFT_CUDA)
#define MAFFT_PAIR_CACHE 1
typedef struct {
	double score;
	int off1, off2;
	size_t len1, len2;
	char text[];
} dup_result;
static dup_result **dup_table;
static int *dup_ids, *dup_counts, dup_n, dup_unique;
static size_t dup_bytes;
static unsigned long long dup_hits, dup_misses;
#if enablemultithread
static pthread_mutex_t dup_mutex = PTHREAD_MUTEX_INITIALIZER;
#define DUP_LOCK() pthread_mutex_lock(&dup_mutex)
#define DUP_UNLOCK() pthread_mutex_unlock(&dup_mutex)
#else
#define DUP_LOCK() ((void)0)
#define DUP_UNLOCK() ((void)0)
#endif

static void duppairs_free(void)
{
	size_t k;
#ifdef MAFFT_DUPCACHE_STATS
	if(dup_table) fprintf(stderr,"DUP_CACHE: %llu hits, %llu misses, %zu bytes, %d/%d unique\n",
	                     dup_hits,dup_misses,dup_bytes,dup_unique,dup_n);
#endif
	if(dup_table) for(k=0;k<(size_t)dup_unique*dup_unique;k++) free(dup_table[k]);
	free(dup_table); free(dup_ids); free(dup_counts);
	dup_table=NULL; dup_ids=dup_counts=NULL; dup_n=dup_unique=0; dup_bytes=0;
	dup_hits=dup_misses=0;
}

static void duppairs_prepare(int n, char **seq)
{
	int i,k,u=0,ntab=1, *ids=NULL,*counts=NULL,*first=NULL,*tab=NULL;
	duppairs_free();
	if(alg!='L' || use_fft || nadd || trywarp || n<2 || n>10000 || getenv("MAFFT_NODUPCACHE")) return;
	while(ntab<2*n) ntab*=2;
	ids=malloc(sizeof(int)*n); counts=calloc(n,sizeof(int)); first=malloc(sizeof(int)*n);
	tab=malloc(sizeof(int)*ntab);
	if(!ids || !counts || !first || !tab) goto done;
	for(k=0;k<ntab;k++) tab[k]=-1;
	for(i=0;i<n;i++)
	{
		const unsigned char *p=(const unsigned char *)seq[i];
		uint64_t h=UINT64_C(14695981039346656037);
		while(*p) { h^=*p++; h*=UINT64_C(1099511628211); }
		k=(int)(h&(ntab-1));
		while(tab[k]>=0 && strcmp(seq[first[tab[k]]],seq[i])) k=(k+1)&(ntab-1);
		if(tab[k]<0) { tab[k]=u; first[u++]=i; }
		ids[i]=tab[k]; counts[ids[i]]++;
	}
	if(u==n || (size_t)u*u > ((size_t)1<<24)/sizeof(dup_result *)) goto done;
	dup_table=calloc((size_t)u*u,sizeof(dup_result *));
	if(!dup_table) goto done;
	dup_ids=ids; ids=NULL; dup_counts=counts; counts=NULL; dup_n=n; dup_unique=u;
	dup_bytes=(size_t)u*u*sizeof(dup_result *);
done:
	free(ids); free(counts); free(first); free(tab);
}

static dup_result *duppairs_get(int i, int j, size_t *slot)
{
	int a,b;
	dup_result *r;
	*slot=(size_t)-1;
	if(!dup_table || i<0 || j<0 || i>=dup_n || j>=dup_n) return NULL;
	a=dup_ids[i]; b=dup_ids[j];
	if(dup_counts[a]==1 && dup_counts[b]==1) return NULL;
	*slot=(size_t)a*dup_unique+b;
	DUP_LOCK();
	r=dup_table[*slot];
	if(r) dup_hits++; else dup_misses++;
	DUP_UNLOCK();
	return r;
}

static void duppairs_put(size_t slot, double score, int off1, int off2, const char *s1, const char *s2)
{
	size_t l1,l2,bytes;
	dup_result *r;
	if(slot==(size_t)-1) return;
	l1=strlen(s1); l2=strlen(s2); bytes=sizeof(dup_result)+l1+l2+2;
	DUP_LOCK();
	if(!dup_table[slot] && bytes<=((size_t)64<<20)-dup_bytes)
	{
		r=malloc(bytes);
		if(r)
		{
			r->score=score; r->off1=off1; r->off2=off2; r->len1=l1; r->len2=l2;
			memcpy(r->text,s1,l1+1); memcpy(r->text+l1+1,s2,l2+1);
			dup_table[slot]=r; dup_bytes+=bytes;
		}
	}
	DUP_UNLOCK();
}
#endif
