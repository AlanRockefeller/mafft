/*
 * GPU (Metal) batch version of the integer L__align11 fill + traceback.
 * Integer arithmetic only, same comparisons and tie rules as Lfill_int() and Ltracking(),
 * so results are identical; pairs it cannot take are reported with status -1.
 */
#ifndef L11GPU_H
#define L11GPU_H

typedef struct
{
	int status;      /* 0: aligned, 1: no local alignment (localstop at the end cell), -1: not done */
	int maxwm;
	int off1, off2;
	char *s1, *s2;   /* malloc'd aligned strings (status 0) */
} l11res;

/*
 * seqs[k]: the sequences (lens[k] = strlen).  code[c]: small code of character c, or -1.
 * mtx: (ncode+1) x (ncode+1) int scores, mtx[a*(ncode+1)+b] = amino_dynamicmtx[char a][char b];
 * row/column ncode must be 0 (stands for the 0 past the end of seq2's profile).
 * pen, ext: penalty, penalty_ex;  thr: the integer local threshold.
 * Returns 0 if no GPU could be used at all (every status is then -1).
 */
int l11gpu_align( int nseq, char **seqs, const int *lens, const int *code, int ncode, const int *mtx,
                  int pen, int ext, int thr, char gapc,
                  int npairs, const int *pi, const int *pj, l11res *out );

#endif
