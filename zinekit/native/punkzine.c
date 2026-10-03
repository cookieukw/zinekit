/*
 * punkzine.c - Punk Zine, a 70s punk zine print filter for frei0r
 *
 * Copyright (C) 2026 cookieukw
 * SPDX-License-Identifier: MIT
 *
 * One filter, three behaviours, chosen per frame from the alpha channel:
 *
 *   Text     every glyph is cut out onto its own paper scrap (ransom note),
 *            tilted, with bitten ink edges, a hard shadow and an
 *            off-register second plate.  Meant for title clips.
 *   Element  anything else with a transparent background: scissor-cut paper
 *            backing, ink outline, colours reprinted as riso plates, a zine
 *            palette or xerox.
 *   Image    opaque images and video: xerox halftone, two-colour riso or a
 *            flat photocopy, with toner grain, paper texture and burned edges.
 *
 * The print is still (it does not change with time); pair it with a wobble
 * filter such as squigglevision for boil.  Every length scales with the frame
 * size, so a half-resolution preview matches the final render.
 *
 * See README.md in this directory for the parameters and the algorithms.
 */
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* Threads: pthreads where available, a serial path elsewhere (Windows,
 * Emscripten/WASI without threads, or -DPUNKZINE_NO_THREADS / -DNO_FUTURE as
 * the frei0r Wasm bundle uses for the other threaded filters). */
#if defined(PUNKZINE_NO_THREADS) || defined(NO_FUTURE) || defined(_WIN32) || \
    (defined(__EMSCRIPTEN__) && !defined(__EMSCRIPTEN_PTHREADS__)) ||        \
    (defined(__wasi__) && !defined(_REENTRANT))
#define PZ_THREADS 0
#else
#define PZ_THREADS 1
#endif

#if PZ_THREADS
#include <pthread.h>
#include <unistd.h>
typedef pthread_mutex_t pz_mutex;
static int pz_mutex_init(pz_mutex *m) { return pthread_mutex_init(m, NULL) == 0; }
static int pz_mutex_trylock(pz_mutex *m) { return pthread_mutex_trylock(m) == 0; }
static void pz_mutex_unlock(pz_mutex *m) { pthread_mutex_unlock(m); }
static void pz_mutex_destroy(pz_mutex *m) { pthread_mutex_destroy(m); }
static pthread_once_t g_once = PTHREAD_ONCE_INIT;
static void pz_once(void (*fn)(void)) { pthread_once(&g_once, fn); }
#else
/* hosts serialise updates of one instance (MLT locks the service), so a flag is enough */
typedef int pz_mutex;
static int pz_mutex_init(pz_mutex *m) { *m = 0; return 1; }
static int pz_mutex_trylock(pz_mutex *m) { if (*m) return 0; *m = 1; return 1; }
static void pz_mutex_unlock(pz_mutex *m) { *m = 0; }
static void pz_mutex_destroy(pz_mutex *m) { (void)m; }
static int g_once_done = 0;
static void pz_once(void (*fn)(void)) { if (!g_once_done) { fn(); g_once_done = 1; } }
#endif

#include "frei0r.h"

/* ------------------------------------------------------------------ parameters */
enum {
  P_MODE, P_STYLE, P_PAL, P_ESTYLE, P_MIX, P_ROUGH, P_CHAOS, P_PAD, P_MARGIN, P_OUTLINE, P_RECOLOR,
  P_DOT, P_ANGLE, P_CONTRAST, P_GRAIN, P_BURN, P_PAPERTEX, P_MISREG, P_SHADOW, P_SHADOWOP, P_KEEPCOL, P_SEED,
  P_INK, P_PAPER, P_C1, P_C2, P_C3, NPARAM
};
#define NCOL 5
#define FIRSTCOL P_INK

typedef struct { const char *name; int type; const char *expl; double def; uint32_t rgb; } pdef_t;
static const pdef_t PDEF[NPARAM] = {
  {"mode", F0R_PARAM_DOUBLE, "0 auto, 0.333 text (ransom scraps), 0.667 element (cut-out), 1 image (halftone)", 0, 0},
  {"image_style", F0R_PARAM_DOUBLE, "image: 0 xerox halftone, 0.333 riso ink + plate color, 0.667 riso plate color + plate color 2, 1 flat photocopy", 0, 0},
  {"scrap_palette", F0R_PARAM_DOUBLE, "text scraps: 0 mixed, 0.25 black and white, 0.5 plate color, 0.75 plate color 2, 1 no paper", 0, 0},
  {"element_style", F0R_PARAM_DOUBLE, "element: 0 auto, 0.25 riso ink + plate, 0.5 zine palette, 0.75 xerox, 1 original colors", 0, 0},
  {"mix", F0R_PARAM_DOUBLE, "amount of the effect", 1, 0},
  {"roughness", F0R_PARAM_DOUBLE, "rough printed edges and ragged halftone dots", 0.5, 0},
  {"chaos", F0R_PARAM_DOUBLE, "text: tilt, jump and size of each letter", 0.6, 0},
  {"scrap_padding", F0R_PARAM_DOUBLE, "text: paper around each letter", 0.5, 0},
  {"cut_margin", F0R_PARAM_DOUBLE, "element: paper margin of the scissor cut", 0.5, 0},
  {"outline", F0R_PARAM_DOUBLE, "element: ink outline", 0.3, 0},
  {"recolor", F0R_PARAM_DOUBLE, "element: 0 keeps its colors, 1 reprints it in the chosen style", 1, 0},
  {"dot_size", F0R_PARAM_DOUBLE, "halftone cell size (element and image)", 0.22, 0},
  {"dot_angle", F0R_PARAM_DOUBLE, "halftone angle, x45 degrees (element and image)", 1, 0},
  {"contrast", F0R_PARAM_DOUBLE, "xerox contrast (element and image)", 0.55, 0},
  {"grain", F0R_PARAM_DOUBLE, "toner grain and specks", 0.4, 0},
  {"burn", F0R_PARAM_DOUBLE, "image: burned photocopy edges", 0.35, 0},
  {"paper_texture", F0R_PARAM_DOUBLE, "paper fibre texture", 0.6, 0},
  {"misregistration", F0R_PARAM_DOUBLE, "offset of the second (plate) color", 0.4, 0},
  {"shadow", F0R_PARAM_DOUBLE, "hard shadow distance (text and element)", 0.5, 0},
  {"shadow_opacity", F0R_PARAM_DOUBLE, "hard shadow opacity (text and element)", 0.5, 0},
  {"keep_text_color", F0R_PARAM_BOOL, "text: letters keep their own color on contrasting scraps", 0, 0},
  {"seed", F0R_PARAM_DOUBLE, "layout seed, x1000 (scrap colors and tilts)", 0.001, 0},
  {"ink", F0R_PARAM_COLOR, "ink: letters, outlines, halftone dots, shadows", 0, 0x151311},
  {"paper", F0R_PARAM_COLOR, "paper: scraps, cut-out margins, the printed page", 0, 0xF7F3E8},
  {"color1", F0R_PARAM_COLOR, "plate color: the riso second ink (off-register copy, riso plates), also a scrap color", 0, 0xFF4FA8},
  {"color2", F0R_PARAM_COLOR, "plate color 2: second plate of the 2-color riso image style, also a scrap color", 0, 0x35D45B},
  {"color3", F0R_PARAM_COLOR, "scrap color: extra paper color for letter scraps and the element palette", 0, 0xFFE24A},
};

typedef struct { float r, g, b; } rgbf;

typedef struct {
  unsigned w, h;
  double p[NPARAM];
  rgbf col[NCOL];
  /* element cut-out cache: a still PNG only needs its cut and plates once */
  pz_mutex mu; int mu_ok;
  uint64_t ckey; float *cdist, *cpb, *ckeyA, *ccolA; int cstyle, chas;
} inst_t;

/* ------------------------------------------------------------------ math helpers */
static inline float clamp01(float x) { return x < 0.f ? 0.f : (x > 1.f ? 1.f : x); }
static inline float clampf(float x, float a, float b) { return x < a ? a : (x > b ? b : x); }
static inline float lerpf(float a, float b, float t) { return a + (b - a) * t; }
static inline float smooth(float a, float b, float x) {
  float t = clamp01((x - a) / (b - a));
  return t * t * (3.f - 2.f * t);
}
static inline rgbf mkrgb(float r, float g, float b) { rgbf o; o.r = r; o.g = g; o.b = b; return o; }
static inline rgbf mixc(rgbf a, rgbf b, float t) { rgbf o = {lerpf(a.r, b.r, t), lerpf(a.g, b.g, t), lerpf(a.b, b.b, t)}; return o; }
static inline rgbf mulc(rgbf a, rgbf b) { rgbf o = {a.r * b.r, a.g * b.g, a.b * b.b}; return o; }
static inline rgbf scalec(rgbf a, float s) { rgbf o = {a.r * s, a.g * s, a.b * s}; return o; }
static inline float lum(float r, float g, float b) { return 0.299f * r + 0.587f * g + 0.114f * b; }

static inline uint32_t h32(uint32_t x) {
  x ^= x >> 16; x *= 0x7feb352dU; x ^= x >> 15; x *= 0x846ca68bU; x ^= x >> 16;
  return x;
}
static inline uint32_t h3(uint32_t a, uint32_t b, uint32_t c) { return h32(a ^ h32(b ^ h32(c + 0x9e3779b9U))); }
static inline float r01(uint32_t a, uint32_t b, uint32_t c) { return (float)(h3(a, b, c) >> 8) * (1.0f / 16777216.0f); }
static inline float rr(float lo, float hi, uint32_t a, uint32_t b, uint32_t c) { return lo + (hi - lo) * r01(a, b, c); }

/* ------------------------------------------------------------------ shared textures */
#define TEX 512
#define TEXM (TEX - 1)
#define CLUT 4096
static float *g_noise; /* TEX*TEX*2 displacement field in [-1,1], tileable */
static float *g_paper; /* TEX*TEX paper luminance factor */
static float g_cos[CLUT];

static float lat(int x, int y, int px, int py, uint32_t s) {
  x %= px; if (x < 0) x += px;
  y %= py; if (y < 0) y += py;
  return r01((uint32_t)x, (uint32_t)y, s);
}
static float vnoise(float x, float y, int px, int py, uint32_t s) {
  int xi = (int)floorf(x), yi = (int)floorf(y);
  float fx = x - xi, fy = y - yi;
  fx = fx * fx * (3 - 2 * fx); fy = fy * fy * (3 - 2 * fy);
  float a = lat(xi, yi, px, py, s), b = lat(xi + 1, yi, px, py, s);
  float c = lat(xi, yi + 1, px, py, s), d = lat(xi + 1, yi + 1, px, py, s);
  return lerpf(lerpf(a, b, fx), lerpf(c, d, fx), fy);
}

static void init_tables(void) {
  for (int i = 0; i < CLUT; i++) g_cos[i] = cosf(6.283185307f * (float)i / CLUT);
  g_noise = (float *)malloc(sizeof(float) * TEX * TEX * 2);
  g_paper = (float *)malloc(sizeof(float) * TEX * TEX);
  if (!g_noise || !g_paper) return;
  for (int y = 0; y < TEX; y++)
    for (int x = 0; x < TEX; x++) {
      float n1 = 0, n2 = 0, tot = 0, amp = 1;
      for (int o = 0; o < 3; o++) {
        int per = 32 << o;
        float s = (float)per / TEX;
        n1 += amp * vnoise(x * s, y * s, per, per, 101 + o);
        n2 += amp * vnoise(x * s, y * s, per, per, 201 + o);
        tot += amp; amp *= 0.6f;
      }
      g_noise[2 * (y * TEX + x)] = clampf((n1 / tot - 0.5f) * 3.2f, -1, 1);
      g_noise[2 * (y * TEX + x) + 1] = clampf((n2 / tot - 0.5f) * 3.2f, -1, 1);
      float lo = vnoise(x * 6.f / TEX, y * 6.f / TEX, 6, 6, 301);
      float mid = vnoise(x * 40.f / TEX, y * 40.f / TEX, 40, 40, 302);
      float fib = vnoise(x * 128.f / TEX, y * 16.f / TEX, 128, 16, 303);
      float gr = r01((uint32_t)x, (uint32_t)y, 304);
      g_paper[y * TEX + x] = 1.03f - 0.075f * lo - 0.05f * mid - 0.04f * fib - 0.06f * gr * gr;
    }
}

static inline void tex_disp(float u, float v, float *dx, float *dy) {
  float fu = floorf(u), fv = floorf(v);
  int x0 = ((int)fu) & TEXM, y0 = ((int)fv) & TEXM, x1 = (x0 + 1) & TEXM, y1 = (y0 + 1) & TEXM;
  float tx = u - fu, ty = v - fv;
  const float *a = g_noise + 2 * (y0 * TEX + x0), *b = g_noise + 2 * (y0 * TEX + x1);
  const float *c = g_noise + 2 * (y1 * TEX + x0), *d = g_noise + 2 * (y1 * TEX + x1);
  *dx = lerpf(lerpf(a[0], b[0], tx), lerpf(c[0], d[0], tx), ty);
  *dy = lerpf(lerpf(a[1], b[1], tx), lerpf(c[1], d[1], tx), ty);
}
static inline float tex_paper(float u, float v) {
  float fu = floorf(u), fv = floorf(v);
  int x0 = ((int)fu) & TEXM, y0 = ((int)fv) & TEXM, x1 = (x0 + 1) & TEXM, y1 = (y0 + 1) & TEXM;
  float tx = u - fu, ty = v - fv;
  return lerpf(lerpf(g_paper[y0 * TEX + x0], g_paper[y0 * TEX + x1], tx),
               lerpf(g_paper[y1 * TEX + x0], g_paper[y1 * TEX + x1], tx), ty);
}
static inline float fcos(float u) { return g_cos[((int)floorf(u * CLUT)) & (CLUT - 1)]; }

/* AM halftone: ink amount 0..1 for coverage cov at (x,y). Round dots that merge past 50%. */
typedef struct { float cs, sn, inv, w; } screen_t;
static inline screen_t mkscreen(float cell, float deg) {
  screen_t s;
  float a = deg * 0.0174532925f;
  if (cell < 2.f) cell = 2.f;
  s.cs = cosf(a); s.sn = sinf(a); s.inv = 1.f / cell;
  s.w = clampf(1.1f * s.inv, 0.02f, 0.25f);
  return s;
}
static inline float screen(const screen_t *s, float x, float y, float cov) {
  if (cov <= 0.002f) return 0.f;
  if (cov >= 0.998f) return 1.f;
  float u = (x * s->cs + y * s->sn) * s->inv, v = (y * s->cs - x * s->sn) * s->inv;
  float spot = 0.5f + 0.25f * (fcos(u) + fcos(v));
  float th = (1.f + s->w) - cov * (1.f + 2.f * s->w);
  return smooth(th - s->w, th + s->w, spot);
}

/* ------------------------------------------------------------------ threads */
typedef void (*rowfn)(void *ctx, int y0, int y1);
typedef struct { rowfn fn; void *ctx; int y0, y1; } job_t;
#if PZ_THREADS
static void *job_run(void *a) { job_t *j = (job_t *)a; j->fn(j->ctx, j->y0, j->y1); return NULL; }
#endif
static void par_rows(rowfn fn, void *ctx, int y0, int y1, long work) {
#if PZ_THREADS
  /* row bands, one thread per online CPU (max 8), created per call; serial for small work */
  long nt = sysconf(_SC_NPROCESSORS_ONLN);
  if (nt < 1) nt = 1;
  if (nt > 8) nt = 8;
  if (work < 120000 || nt == 1 || y1 - y0 < 16) { fn(ctx, y0, y1); return; }
  pthread_t th[8]; job_t jobs[8]; int ok[8] = {0};
  int n = y1 - y0;
  for (int i = 0; i < nt; i++) { jobs[i].fn = fn; jobs[i].ctx = ctx; jobs[i].y0 = y0 + (int)((long)n * i / nt); jobs[i].y1 = y0 + (int)((long)n * (i + 1) / nt); }
  for (int i = 1; i < nt; i++) ok[i] = pthread_create(&th[i], NULL, job_run, &jobs[i]) == 0;
  job_run(&jobs[0]);
  for (int i = 1; i < nt; i++) { if (ok[i]) pthread_join(th[i], NULL); else job_run(&jobs[i]); }
#else
  (void)work;
  fn(ctx, y0, y1);
#endif
}

/* ------------------------------------------------------------------ per-frame context */
typedef struct {
  inst_t *in;
  int w, h;
  float k, invk;             /* resolution scale vs 1920x1080 */
  const uint8_t *src;        /* RGBA8, straight alpha */
  uint8_t *dst;
  float mix;
  uint32_t seed, step;
  float ox, oy;              /* noise offsets in texels (fixed: the print is still) */
  rgbf ink, paper, c1, c2, c3, grey, news, kraft;
  float contrast_lo, contrast_hi;
  screen_t s_k, s_a, s_b;    /* screens: ink, plate a, plate b */
  float paper_tex, grain, burn;
  /* text */
  const int *lab; const int *c2g; struct grp_s *groups; int ng; float *acc;
  float amp, amp_edge; float shx, shy; float shop; int keepcol;
  /* element */
  const float *dist; const float *pb; float outline_px; float misx, misy; float recolor; float amp_paper; int has_paper;
  float accY[3]; float accCo[3], accCg[3]; float gain;
  float cell, ang;
  int estyle; rgbf plate; const float *keyA; const float *colA; int underplate;
  /* image */
  int style; float wob, jit; float lv_lo, lv_inv; float tmx, tmy;
} ctx_t;

static inline float contrast(const ctx_t *c, float y) { return smooth(c->contrast_lo, c->contrast_hi, y); }

/* rough printed edge: coarse wobble + fine bite, about -1.4..1.4 */
static inline void rough_disp(const ctx_t *c, float x, float y, float *dx, float *dy) {
  float ax, ay, bx, by;
  tex_disp(x * c->invk + c->ox, y * c->invk + c->oy, &ax, &ay);
  tex_disp(x * c->invk * 2.7f + 133.f, y * c->invk * 2.7f + 71.f, &bx, &by);
  *dx = ax + 0.55f * bx; *dy = ay + 0.55f * by;
}
/* halftone dots go ragged with roughness */
static inline void dot_jitter(const ctx_t *c, int x, int y, float *hx, float *hy) {
  *hx = (float)x; *hy = (float)y;
  if (c->jit > 0) { float a, b; tex_disp(x * c->invk * 1.9f + 211.f, y * c->invk * 1.9f + 97.f, &a, &b); *hx += a * c->jit; *hy += b * c->jit; }
}
/* toner specks and dirt on the copy */
static inline rgbf toner(const ctx_t *c, rgbf col, int x, int y) {
  if (c->grain <= 0) return col;
  if (r01((uint32_t)x, (uint32_t)y, c->seed + 77u) < c->grain * 0.003f) return c->ink;
  return scalec(col, 1.f + (r01((uint32_t)x, (uint32_t)y, c->seed + 78u) - 0.5f) * c->grain * 0.14f);
}

static inline rgbf paper_at(const ctx_t *c, rgbf base, float x, float y) {
  if (c->paper_tex <= 0.f) return base;
  float t = tex_paper(x * c->invk * 0.9f + 37.f, y * c->invk * 0.9f + 91.f) * 0.6f +
            tex_paper(y * c->invk * 0.37f + 211.f, x * c->invk * 0.37f + 5.f) * 0.4f;
  return scalec(base, lerpf(1.f, t, c->paper_tex));
}

/* bilinear sample, premultiplied float RGBA. clamp=1 clamps to edges, else zero outside */
static inline void sample(const ctx_t *c, float x, float y, int clamp, float out[4]) {
  int w = c->w, h = c->h;
  float fx = floorf(x), fy = floorf(y);
  int x0 = (int)fx, y0 = (int)fy;
  float tx = x - fx, ty = y - fy;
  float acc[4] = {0, 0, 0, 0};
  for (int j = 0; j < 4; j++) {
    int xi = x0 + (j & 1), yi = y0 + (j >> 1);
    float wgt = ((j & 1) ? tx : 1 - tx) * ((j >> 1) ? ty : 1 - ty);
    if (wgt <= 0) continue;
    if (clamp) { xi = xi < 0 ? 0 : (xi >= w ? w - 1 : xi); yi = yi < 0 ? 0 : (yi >= h ? h - 1 : yi); }
    else if (xi < 0 || yi < 0 || xi >= w || yi >= h) continue;
    const uint8_t *p = c->src + 4 * ((size_t)yi * w + xi);
    float a = p[3] * (1.f / 255.f);
    acc[0] += wgt * p[0] * (1.f / 255.f) * a; acc[1] += wgt * p[1] * (1.f / 255.f) * a;
    acc[2] += wgt * p[2] * (1.f / 255.f) * a; acc[3] += wgt * a;
  }
  memcpy(out, acc, sizeof(acc));
}
static inline float samplef(const float *buf, int w, int h, float x, float y) {
  float fx = floorf(x), fy = floorf(y);
  int x0 = (int)fx, y0 = (int)fy;
  float tx = x - fx, ty = y - fy, v = 0;
  for (int j = 0; j < 4; j++) {
    int xi = x0 + (j & 1), yi = y0 + (j >> 1);
    if (xi < 0 || yi < 0 || xi >= w || yi >= h) continue;
    v += ((j & 1) ? tx : 1 - tx) * ((j >> 1) ? ty : 1 - ty) * buf[(size_t)yi * w + xi];
  }
  return v;
}
static inline float samplef_far(const float *buf, int w, int h, float x, float y, float far) {
  float fx = floorf(x), fy = floorf(y);
  int x0 = (int)fx, y0 = (int)fy;
  float tx = x - fx, ty = y - fy, v = 0;
  for (int j = 0; j < 4; j++) {
    int xi = x0 + (j & 1), yi = y0 + (j >> 1);
    float s = (xi < 0 || yi < 0 || xi >= w || yi >= h) ? far : buf[(size_t)yi * w + xi];
    v += ((j & 1) ? tx : 1 - tx) * ((j >> 1) ? ty : 1 - ty) * s;
  }
  return v;
}

/* premultiplied "over" into a float RGBA pixel */
static inline void over(float *d, rgbf c, float a) {
  if (a <= 0.f) return;
  float ia = 1.f - a;
  d[0] = c.r * a + d[0] * ia; d[1] = c.g * a + d[1] * ia; d[2] = c.b * a + d[2] * ia; d[3] = a + d[3] * ia;
}

/* final write: mix effect (premult float) with the original, store straight RGBA8 */
static inline void store(const ctx_t *c, size_t i, const float *e) {
  const uint8_t *s = c->src + 4 * i;
  uint8_t *d = c->dst + 4 * i;
  float m = c->mix, o[4];
  if (m >= 0.999f) memcpy(o, e, sizeof(o));
  else {
    float a = s[3] * (1.f / 255.f);
    o[0] = lerpf(s[0] * (1.f / 255.f) * a, e[0], m); o[1] = lerpf(s[1] * (1.f / 255.f) * a, e[1], m);
    o[2] = lerpf(s[2] * (1.f / 255.f) * a, e[2], m); o[3] = lerpf(a, e[3], m);
  }
  if (o[3] <= 0.0005f) { d[0] = d[1] = d[2] = d[3] = 0; return; }
  float ia = 1.f / o[3];
  d[0] = (uint8_t)(clamp01(o[0] * ia) * 255.f + 0.5f);
  d[1] = (uint8_t)(clamp01(o[1] * ia) * 255.f + 0.5f);
  d[2] = (uint8_t)(clamp01(o[2] * ia) * 255.f + 0.5f);
  d[3] = (uint8_t)(clamp01(o[3]) * 255.f + 0.5f);
}

/* ================================================================== IMAGE: xerox / riso */
static void image_rows(void *vc, int y0, int y1) {
  ctx_t *c = (ctx_t *)vc;
  int w = c->w, h = c->h;
  float bw = 0.2f * (float)(w < h ? w : h);
  for (int y = y0; y < y1; y++)
    for (int x = 0; x < w; x++) {
      float dx = 0, dy = 0;
      if (c->wob > 0) { tex_disp(x * c->invk * 0.25f + c->ox, y * c->invk * 0.25f + c->oy, &dx, &dy); dx *= c->wob; dy *= c->wob; }
      float px[4];
      sample(c, x + dx, y + dy, 1, px);
      float a = px[3];
      float r = 0, g = 0, b = 0;
      if (a > 0.0001f) { r = px[0] / a; g = px[1] / a; b = px[2] / a; }
      float gn = (r01((uint32_t)x, (uint32_t)y, c->step * 7919u + c->seed) - 0.5f) * c->grain * 0.22f;
      float Y = (lum(r, g, b) - c->lv_lo) * c->lv_inv + gn;
      float Yc = contrast(c, Y);
      /* burned edges */
      if (c->burn > 0) {
        float e = fminf(fminf((float)x, (float)(w - 1 - x)), fminf((float)y, (float)(h - 1 - y))) / bw;
        float n0, n1;
        tex_disp(x * c->invk * 0.12f + 300.f + c->ox * 0.2f, y * c->invk * 0.12f + c->oy * 0.2f, &n0, &n1);
        float bf = c->burn * (1.f - smooth(0.f, 1.f, e * (0.75f + 0.5f * n0)));
        Yc *= 1.f - 0.92f * bf;
      }
      /* toner specks */
      int speck = c->grain > 0 && r01((uint32_t)x, (uint32_t)y, c->step * 104729u + c->seed + 5u) < c->grain * 0.0012f;
      rgbf pap = paper_at(c, c->paper, (float)x, (float)y);
      float hx, hy; dot_jitter(c, x, y, &hx, &hy);
      rgbf out;
      switch (c->style) {
        default:
        case 0: { /* xerox halftone */
          float amt = speck ? 1.f : screen(&c->s_k, hx, hy, 1.f - Yc);
          out = mixc(pap, c->ink, amt);
          break;
        }
        case 1: { /* riso: ink + colour 1 (colour plate off register) */
          float q[4]; sample(c, x + dx - c->misx, y + dy - c->misy, 1, q);
          float Y2 = q[3] > 0.0001f ? (lum(q[0] / q[3], q[1] / q[3], q[2] / q[3]) - c->lv_lo) * c->lv_inv : 1.f;
          float Yc2 = contrast(c, Y2 + gn);
          float covA = clamp01((1.f - Yc2) * 1.1f);
          float covK = smooth(0.42f, 0.02f, Yc);
          float amtA = screen(&c->s_a, hx, hy, covA);
          float amtK = speck ? 1.f : screen(&c->s_k, hx, hy, covK);
          rgbf one = {1, 1, 1};
          out = mulc(mulc(pap, mixc(one, c->plate, amtA)), mixc(one, c->ink, amtK));
          break;
        }
        case 2: { /* riso: colour 1 + colour 2 */
          float q[4]; sample(c, x + dx - c->misx, y + dy - c->misy, 1, q);
          float r2 = r, g2 = g, b2 = b;
          if (q[3] > 0.0001f) { r2 = q[0] / q[3]; g2 = q[1] / q[3]; b2 = q[2] / q[3]; }
          float d1 = 1.f - Yc, d2 = 1.f - contrast(c, (lum(r2, g2, b2) - c->lv_lo) * c->lv_inv + gn);
          float w1 = clamp01(0.55f + 1.3f * (r - g) + 0.4f * (b - g));
          float w2 = clamp01(0.55f + 1.3f * (g2 - r2) + 0.3f * (g2 - b2));
          float cov1 = clamp01(d1 * (w1 + 0.5f * d1)), cov2 = clamp01(d2 * (w2 + 0.5f * d2));
          float amt1 = screen(&c->s_a, hx, hy, cov1);
          float amt2 = screen(&c->s_b, hx, hy, cov2);
          rgbf one = {1, 1, 1};
          out = mulc(mulc(pap, mixc(one, c->c1, amt1)), mixc(one, c->c2, amt2));
          if (speck) out = c->ink;
          break;
        }
        case 3: { /* flat photocopy: hard threshold with toner noise */
          float t = 1.f - Yc + (r01((uint32_t)x, (uint32_t)y, c->step * 31u + c->seed + 9u) - 0.5f) * (0.06f + 0.2f * c->grain);
          float amt = speck ? 1.f : smooth(0.47f, 0.53f, t);
          out = mixc(pap, c->ink, amt);
          break;
        }
      }
      float e[4] = {out.r * a, out.g * a, out.b * a, a};
      store(c, (size_t)y * w + x, e);
    }
}

/* ================================================================== connected components */
static int uf_find(int *par, int x) {
  while (par[x] != x) { par[x] = par[par[x]]; x = par[x]; }
  return x;
}
static void uf_union(int *par, int a, int b) {
  a = uf_find(par, a); b = uf_find(par, b);
  if (a == b) return;
  if (a < b) par[b] = a; else par[a] = b;
}
/* 8-connected labels 1..n (0 = background). Returns n, or -1 on allocation failure. */
static int ccl(const uint8_t *m, int w, int h, int *lab) {
  int cap = 4096, n = 1;
  int *par = (int *)malloc(sizeof(int) * cap);
  if (!par) return -1;
  par[0] = 0;
  for (int y = 0; y < h; y++)
    for (int x = 0; x < w; x++) {
      size_t i = (size_t)y * w + x;
      if (!m[i]) { lab[i] = 0; continue; }
      int nb[4], nn = 0;
      if (x > 0 && lab[i - 1]) nb[nn++] = lab[i - 1];
      if (y > 0) {
        if (x > 0 && lab[i - w - 1]) nb[nn++] = lab[i - w - 1];
        if (lab[i - w]) nb[nn++] = lab[i - w];
        if (x < w - 1 && lab[i - w + 1]) nb[nn++] = lab[i - w + 1];
      }
      if (!nn) {
        if (n >= cap) {
          cap *= 2;
          int *np = (int *)realloc(par, sizeof(int) * cap);
          if (!np) { free(par); return -1; }
          par = np;
        }
        par[n] = n; lab[i] = n++;
      } else {
        int mn = nb[0];
        for (int j = 1; j < nn; j++) if (nb[j] < mn) mn = nb[j];
        lab[i] = mn;
        for (int j = 0; j < nn; j++) if (nb[j] != mn) uf_union(par, mn, nb[j]);
      }
    }
  int *map = (int *)calloc(n, sizeof(int));
  if (!map) { free(par); return -1; }
  int cnt = 0;
  for (int l = 1; l < n; l++) {
    int r = uf_find(par, l);
    if (r == l) map[l] = ++cnt; else map[l] = map[r];
  }
  size_t N = (size_t)w * h;
  for (size_t i = 0; i < N; i++) lab[i] = map[lab[i]];
  free(map); free(par);
  return cnt;
}

/* ================================================================== TEXT: ransom-note scraps */
typedef struct grp_s {
  int x0, y0, x1, y1; long area;
  float cx, cy;
  int order;
  float rc, rs, sc, tx, ty;   /* forward transform about (cx,cy) */
  float qx[4], qy[4];         /* scrap quad in source space */
  float ex[4], ey[4];         /* unit edge directions */
  rgbf bg, fg; int nopaper;
  float ymode; int multi;     /* dominant tone; multi = has inner detail (logo, outlined text) */
  int ghost;                  /* draw the off-register plate copy of the letter */
  float z;
  int ox0, oy0, ox1, oy1;     /* output bbox */
} grp_t;

typedef struct { int x0, y0, x1, y1; long area; } comp_t;

static const comp_t *g_sort_comps;
static int cmp_comp_x(const void *a, const void *b) {
  const comp_t *A = &g_sort_comps[*(const int *)a], *B = &g_sort_comps[*(const int *)b];
  return A->x0 - B->x0;
}
typedef struct { float key; int idx; } keyidx_t;
static int cmp_key(const void *a, const void *b) {
  float d = ((const keyidx_t *)a)->key - ((const keyidx_t *)b)->key;
  return d < 0 ? -1 : (d > 0 ? 1 : 0);
}

static inline void inv_map(const grp_t *g, float x, float y, float *px, float *py) {
  float ux = x - g->cx - g->tx, uy = y - g->cy - g->ty;
  float is = 1.f / g->sc;
  *px = g->cx + (ux * g->rc + uy * g->rs) * is;
  *py = g->cy + (-ux * g->rs + uy * g->rc) * is;
}
static inline float quad_cov(const grp_t *g, float px, float py) {
  float m = 1e9f;
  for (int i = 0; i < 4; i++) {
    float d = (py - g->qy[i]) * g->ex[i] - (px - g->qx[i]) * g->ey[i];
    if (d < m) m = d;
  }
  return clamp01(m * g->sc + 0.5f);
}
static inline float glyph_alpha(const ctx_t *c, int gi, float px, float py) {
  float fx = floorf(px), fy = floorf(py);
  int x0 = (int)fx, y0 = (int)fy;
  float tx = px - fx, ty = py - fy, v = 0;
  for (int j = 0; j < 4; j++) {
    int xi = x0 + (j & 1), yi = y0 + (j >> 1);
    if (xi < 0 || yi < 0 || xi >= c->w || yi >= c->h) continue;
    size_t i = (size_t)yi * c->w + xi;
    int l = c->lab[i];
    if (l <= 0 || c->c2g[l] != gi) continue;
    v += ((j & 1) ? tx : 1 - tx) * ((j >> 1) ? ty : 1 - ty) * c->src[4 * i + 3] * (1.f / 255.f);
  }
  return v;
}

typedef struct { ctx_t *c; int gi; } gjob_t;
static void group_rows(void *vj, int y0, int y1) {
  gjob_t *j = (gjob_t *)vj;
  ctx_t *c = j->c;
  const grp_t *g = &c->groups[j->gi];
  rgbf sh = scalec(c->ink, 0.6f);
  for (int y = y0; y < y1; y++)
    for (int x = g->ox0; x < g->ox1; x++) {
      float nx = 0, ny = 0;
      if (c->amp > 0) rough_disp(c, (float)x, (float)y, &nx, &ny);
      float dx = nx * c->amp, dy = ny * c->amp, ex = nx * c->amp_edge, ey = ny * c->amp_edge;
      float *d = c->acc + 4 * ((size_t)y * c->w + x);
      /* shadow */
      if (c->shop > 0) {
        float sx, sy;
        if (g->nopaper) { inv_map(g, x - c->shx + dx, y - c->shy + dy, &sx, &sy); float cs = smooth(0.2f, 0.8f, glyph_alpha(c, j->gi, sx, sy)); if (cs > 0) over(d, sh, cs * c->shop); }
        else { inv_map(g, x - c->shx + ex, y - c->shy + ey, &sx, &sy); float cs = quad_cov(g, sx, sy); if (cs > 0) over(d, sh, cs * c->shop); }
      }
      float px, py, qx, qy;
      inv_map(g, x + dx, y + dy, &px, &py);
      float ga = smooth(0.2f, 0.8f, glyph_alpha(c, j->gi, px, py));
      rgbf fg = g->fg;
      float knock = 0.f;
      if (ga > 0 && !g->nopaper && (c->keepcol || g->multi)) {
        float s4[4]; sample(c, px, py, 0, s4);
        if (s4[3] > 0.01f) {
          float r = s4[0] / s4[3], gg = s4[1] / s4[3], b = s4[2] / s4[3];
          if (c->keepcol) { fg.r = r; fg.g = gg; fg.b = b; }
          else knock = smooth(0.14f, 0.26f, fabsf(lum(r, gg, b) - g->ymode));
        }
      }
      if (g->nopaper) {
        if (c->tmx != 0 || c->tmy != 0) {
          float mx, my;
          inv_map(g, x - c->tmx + dx, y - c->tmy + dy, &mx, &my);
          float gm = smooth(0.2f, 0.8f, glyph_alpha(c, j->gi, mx, my));
          if (gm > 0) over(d, c->plate, gm);
        }
        if (ga > 0) {
          float s4[4]; sample(c, px, py, 0, s4);
          rgbf own = s4[3] > 0.01f ? mkrgb(s4[0] / s4[3], s4[1] / s4[3], s4[2] / s4[3]) : fg;
          over(d, own, ga);
        }
      } else {
        inv_map(g, x + ex, y + ey, &qx, &qy);
        float cq = quad_cov(g, qx, qy);
        if (cq > 0) {
          rgbf bg = paper_at(c, g->bg, px, py);
          if (g->ghost) { /* second riso plate printing the letter off register */
            float mx, my;
            inv_map(g, x - c->tmx + dx, y - c->tmy + dy, &mx, &my);
            float gm = smooth(0.2f, 0.8f, glyph_alpha(c, j->gi, mx, my));
            if (gm > 0) bg = mulc(bg, mixc(mkrgb(1, 1, 1), c->plate, gm));
          }
          rgbf col = mixc(bg, fg, ga * (1.f - knock));
          over(d, toner(c, col, x, y), cq);
        }
      }
    }
}

static void pick_scrap(const ctx_t *c, int pal, uint32_t key, rgbf *bg, rgbf *fg, int *nopaper, float ytext) {
  *nopaper = 0;
  if (ytext >= 0 && pal < 4) {
    rgbf P = c->paper, K = c->ink, A = c->c1, B = c->c2, Y = c->c3, G = c->grey, R = c->kraft;
    rgbf L[7] = {P, K, A, B, Y, G, R};
    int n = pal == 1 ? 3 : 7; /* black & white: paper, ink, grey */
    if (pal == 1) L[2] = G;
    if (pal == 2) { L[2] = A; n = 3; }
    if (pal == 3) { L[2] = B; n = 3; }
    int start = (int)(r01(key, 17, c->seed) * n) % n;
    for (int k = 0; k < n; k++) {
      rgbf cand = L[(start + k) % n];
      if (fabsf(lum(cand.r, cand.g, cand.b) - ytext) > 0.38f) { *bg = cand; *fg = K; return; }
    }
    *bg = ytext > 0.5f ? K : P; *fg = K;
    return;
  }
  rgbf P = c->paper, K = c->ink, A = c->c1, B = c->c2, Y = c->c3, G = c->grey, N = c->news, R = c->kraft;
  float u = r01(key, 17, c->seed);
  switch (pal) {
    case 0: {
      rgbf L[12][2] = {{P, K}, {P, K}, {K, P}, {A, K}, {Y, K}, {B, K}, {G, K}, {P, A}, {K, Y}, {R, K}, {P, K}, {K, A}};
      int i = (int)(u * 12) % 12; *bg = L[i][0]; *fg = L[i][1]; break;
    }
    case 1: {
      rgbf L[6][2] = {{P, K}, {K, P}, {G, K}, {N, K}, {P, K}, {K, N}};
      int i = (int)(u * 6) % 6; *bg = L[i][0]; *fg = L[i][1]; break;
    }
    case 2: {
      rgbf L[6][2] = {{A, K}, {A, K}, {P, K}, {K, A}, {A, P}, {P, A}};
      int i = (int)(u * 6) % 6; *bg = L[i][0]; *fg = L[i][1]; break;
    }
    case 3: {
      rgbf L[6][2] = {{B, K}, {B, K}, {P, K}, {K, B}, {B, P}, {P, B}};
      int i = (int)(u * 6) % 6; *bg = L[i][0]; *fg = L[i][1]; break;
    }
    default: *nopaper = 1; *bg = P; *fg = K; break;
  }
}

/* returns 1 if handled as text, 0 if the frame should go to element mode */
static int run_text(ctx_t *c, int force) {
  const inst_t *in = c->in;
  int w = c->w, h = c->h;
  size_t N = (size_t)w * h;
  int ok = 0;
  uint8_t *m = (uint8_t *)malloc(N);
  int *lab = (int *)malloc(sizeof(int) * N);
  comp_t *comps = NULL; int *gpar = NULL, *c2g = NULL, *ord = NULL; grp_t *groups = NULL; float *acc = NULL; keyidx_t *keys = NULL;
  if (!m || !lab) goto done;
  for (size_t i = 0; i < N; i++) m[i] = c->src[4 * i + 3] >= 128;
  int nc = ccl(m, w, h, lab);
  if (nc < 0) goto done;
  if (nc == 0) { /* nothing opaque: keep frame */
    for (size_t i = 0; i < N; i++) { float e[4]; float a = c->src[4 * i + 3] / 255.f; e[0] = c->src[4 * i] / 255.f * a; e[1] = c->src[4 * i + 1] / 255.f * a; e[2] = c->src[4 * i + 2] / 255.f * a; e[3] = a; store(c, i, e); }
    ok = 1; goto done;
  }
  if (nc > 6000 && !force) goto done;
  /* soft edge pixels join their neighbouring letter */
  for (int it = 0; it < 2; it++) {
    for (int y = 0; y < h; y++)
      for (int x = 0; x < w; x++) {
        size_t i = (size_t)y * w + x;
        if (lab[i] || !c->src[4 * i + 3]) continue;
        for (int dy = -1; dy <= 1 && !lab[i]; dy++)
          for (int dx = -1; dx <= 1; dx++) {
            int xx = x + dx, yy = y + dy;
            if (xx < 0 || yy < 0 || xx >= w || yy >= h) continue;
            int l = lab[(size_t)yy * w + xx];
            if (l > 0) { lab[i] = -l; break; } /* negative = provisional for this pass */
          }
      }
    for (size_t i = 0; i < N; i++) if (lab[i] < 0) lab[i] = -lab[i];
  }

  comps = (comp_t *)malloc(sizeof(comp_t) * (nc + 1));
  gpar = (int *)malloc(sizeof(int) * (nc + 1));
  c2g = (int *)malloc(sizeof(int) * (nc + 1));
  ord = (int *)malloc(sizeof(int) * (nc + 1));
  if (!comps || !gpar || !c2g || !ord) goto done;
  for (int l = 0; l <= nc; l++) { comps[l].x0 = w; comps[l].y0 = h; comps[l].x1 = -1; comps[l].y1 = -1; comps[l].area = 0; gpar[l] = l; }
  for (int y = 0; y < h; y++)
    for (int x = 0; x < w; x++) {
      int l = lab[(size_t)y * w + x];
      if (!l) continue;
      comp_t *cp = &comps[l];
      if (x < cp->x0) cp->x0 = x; if (x > cp->x1) cp->x1 = x;
      if (y < cp->y0) cp->y0 = y; if (y > cp->y1) cp->y1 = y;
      cp->area++;
    }
  long minarea = (long)fmaxf(3.f, 3.f * c->k * c->k);
  int nv = 0;
  for (int l = 1; l <= nc; l++) if (comps[l].area >= minarea) ord[nv++] = l;
  if (!nv) goto done;
  /* merge dots / accents / split glyphs into one letter */
  g_sort_comps = comps;
  qsort(ord, nv, sizeof(int), cmp_comp_x);
  for (int a = 0; a < nv; a++) {
    const comp_t *A = &comps[ord[a]];
    for (int b = a + 1; b < nv; b++) {
      const comp_t *B = &comps[ord[b]];
      if (B->x0 > A->x1) break;
      int ov = (A->x1 < B->x1 ? A->x1 : B->x1) - (A->x0 > B->x0 ? A->x0 : B->x0) + 1;
      int wa = A->x1 - A->x0 + 1, wb = B->x1 - B->x0 + 1, ha = A->y1 - A->y0 + 1, hb = B->y1 - B->y0 + 1;
      int minw = wa < wb ? wa : wb, maxh = ha > hb ? ha : hb;
      int gap = (B->y0 - A->y1 > A->y0 - B->y1) ? B->y0 - A->y1 : A->y0 - B->y1;
      int inside = B->x0 >= A->x0 && B->x1 <= A->x1 && B->y0 >= A->y0 && B->y1 <= A->y1;
      if (inside || (ov >= minw / 2 && gap < (int)(0.45f * maxh))) uf_union(gpar, ord[a], ord[b]);
    }
  }
  for (int l = 0; l <= nc; l++) c2g[l] = -1;
  int ng = 0;
  for (int a = 0; a < nv; a++) { int r = uf_find(gpar, ord[a]); if (c2g[r] < 0) c2g[r] = ng++; }
  for (int a = 0; a < nv; a++) c2g[ord[a]] = c2g[uf_find(gpar, ord[a])];
  groups = (grp_t *)calloc(ng, sizeof(grp_t));
  if (!groups) goto done;
  for (int g = 0; g < ng; g++) { groups[g].x0 = w; groups[g].y0 = h; groups[g].x1 = -1; groups[g].y1 = -1; }
  long total = 0, maxa = 0;
  for (int a = 0; a < nv; a++) {
    const comp_t *A = &comps[ord[a]];
    grp_t *g = &groups[c2g[ord[a]]];
    if (A->x0 < g->x0) g->x0 = A->x0; if (A->x1 > g->x1) g->x1 = A->x1;
    if (A->y0 < g->y0) g->y0 = A->y0; if (A->y1 > g->y1) g->y1 = A->y1;
    g->area += A->area; total += A->area;
  }
  for (int g = 0; g < ng; g++) if (groups[g].area > maxa) maxa = groups[g].area;
  if (!force && (ng < 3 || maxa > 0.4 * total || ng > 3000)) goto done;
  /* dominant tone of each letter: inner detail (a logo's knocked-out letter, an outline) stays visible */
  {
    float *hist = (float *)calloc((size_t)ng * 32, sizeof(float));
    if (hist) {
      for (size_t i = 0; i < N; i++) {
        int l = lab[i];
        if (l <= 0 || c2g[l] < 0 || c->src[4 * i + 3] < 128) continue;
        const uint8_t *q = c->src + 4 * i;
        float Y = lum(q[0] / 255.f, q[1] / 255.f, q[2] / 255.f);
        int bin = (int)(Y * 15.999f);
        float *hg = hist + (size_t)c2g[l] * 32;
        hg[bin] += 1.f; hg[16 + bin] += Y;
      }
      for (int g = 0; g < ng; g++) {
        float *hg = hist + (size_t)g * 32, tot = 0, best = -1; int bi = 0;
        for (int b = 0; b < 16; b++) { tot += hg[b]; if (hg[b] > best) { best = hg[b]; bi = b; } }
        groups[g].ymode = best > 0 ? hg[16 + bi] / best : 0.5f;
        float far = 0;
        for (int b = 0; b < 16; b++) if (fabsf((b + 0.5f) / 16.f - groups[g].ymode) > 0.2f) far += hg[b];
        groups[g].multi = tot > 0 && far > 0.03f * tot;
      }
      free(hist);
    }
  }

  /* reading order (lines, then left to right) for a stable random layout */
  keys = (keyidx_t *)malloc(sizeof(keyidx_t) * ng);
  if (!keys) goto done;
  for (int g = 0; g < ng; g++) { keys[g].key = (float)(groups[g].y1 - groups[g].y0 + 1); keys[g].idx = g; }
  qsort(keys, ng, sizeof(keyidx_t), cmp_key);
  float medH = keys[ng / 2].key;
  for (int g = 0; g < ng; g++) {
    grp_t *G = &groups[g];
    G->cx = 0.5f * (G->x0 + G->x1); G->cy = 0.5f * (G->y0 + G->y1);
    keys[g].key = floorf(G->cy / (medH * 1.3f)) * 100000.f + G->cx; keys[g].idx = g;
  }
  qsort(keys, ng, sizeof(keyidx_t), cmp_key);
  for (int i = 0; i < ng; i++) groups[keys[i].idx].order = i;

  int pal = (int)lround(clampf((float)in->p[P_PAL], 0, 1) * 4);
  float chaos = clampf((float)in->p[P_CHAOS], 0, 1);
  float padsc = clampf((float)in->p[P_PAD], 0, 1) * 2.f;
  /* shadow scales with the type size */
  float shd = clampf((float)in->p[P_SHADOW], 0, 1);
  c->shx = shd * 0.08f * medH; c->shy = shd * 0.12f * medH;
  c->shop = shd > 0 ? clampf((float)in->p[P_SHADOWOP], 0, 1) : 0.f;
  { float mis = clampf((float)in->p[P_MISREG], 0, 1); c->tmx = mis * 0.07f * medH; c->tmy = mis * 0.045f * medH; }
  for (int g = 0; g < ng; g++) {
    grp_t *G = &groups[g];
    uint32_t key = (uint32_t)G->order * 2654435761u + c->seed;
    float gh = (float)(G->y1 - G->y0 + 1);
    pick_scrap(c, pal, key, &G->bg, &G->fg, &G->nopaper, c->keepcol ? G->ymode : -1.f);
    {
      float db = fabsf(G->bg.r - c->plate.r) + fabsf(G->bg.g - c->plate.g) + fabsf(G->bg.b - c->plate.b);
      G->ghost = !G->nopaper && (c->tmx != 0 || c->tmy != 0) && db > 0.45f && lum(G->bg.r, G->bg.g, G->bg.b) > 0.3f;
    }
    float rot = chaos * rr(-8.f, 8.f, key, 1, 0);
    float a = rot * 0.0174532925f;
    G->rc = cosf(a); G->rs = sinf(a);
    G->sc = 1.f + chaos * rr(-0.07f, 0.09f, key, 3, 0);
    G->ty = chaos * rr(-0.07f, 0.07f, key, 4, 0) * gh;
    G->tx = 0.f;
    G->z = r01(key, 9, 0);
    float padX = rr(0.05f, 0.2f, key, 7, 0) * gh * padsc + 2.f * c->k;
    float padT = rr(0.0f, 0.1f, key, 8, 0) * gh * padsc + 2.f * c->k;
    float padB = padT * 0.6f + 2.f * c->k;
    float bx0 = G->x0 - padX, bx1 = G->x1 + 1 + padX, by0 = G->y0 - padT, by1 = G->y1 + 1 + padB;
    /* corners pulled in a little (inside the padding, so the letter is never cut) */
    G->qx[0] = bx0 + rr(0, 0.8f, key, 10, 0) * padX; G->qy[0] = by0 + rr(0, 0.8f, key, 11, 0) * padT;
    G->qx[1] = bx1 - rr(0, 0.8f, key, 12, 0) * padX; G->qy[1] = by0 + rr(0, 0.6f, key, 13, 0) * padT;
    G->qx[2] = bx1 - rr(0, 0.5f, key, 14, 0) * padX; G->qy[2] = by1 - rr(0, 0.8f, key, 15, 0) * padB;
    G->qx[3] = bx0 + rr(0, 0.6f, key, 16, 0) * padX; G->qy[3] = by1 - rr(0, 0.7f, key, 18, 0) * padB;
    for (int i = 0; i < 4; i++) {
      float ex = G->qx[(i + 1) & 3] - G->qx[i], ey = G->qy[(i + 1) & 3] - G->qy[i];
      float l = sqrtf(ex * ex + ey * ey);
      if (l < 1e-6f) l = 1e-6f;
      G->ex[i] = ex / l; G->ey[i] = ey / l;
    }
    /* output bbox: forward-map the quad (or the glyph box) */
    float mnx = 1e9f, mny = 1e9f, mxx = -1e9f, mxy = -1e9f;
    for (int i = 0; i < 4; i++) {
      float sx = G->nopaper ? (i & 1 ? G->x1 + 1 : G->x0) : G->qx[i];
      float sy = G->nopaper ? (i & 2 ? G->y1 + 1 : G->y0) : G->qy[i];
      float ux = (sx - G->cx) * G->sc, uy = (sy - G->cy) * G->sc;
      float X = G->cx + G->tx + ux * G->rc - uy * G->rs, Y = G->cy + G->ty + ux * G->rs + uy * G->rc;
      if (X < mnx) mnx = X; if (X > mxx) mxx = X; if (Y < mny) mny = Y; if (Y > mxy) mxy = Y;
    }
    float mg = c->amp + c->amp_edge + 3.f + fabsf(c->tmx) + fabsf(c->tmy);
    mnx -= mg; mny -= mg; mxx += mg + c->shx; mxy += mg + c->shy;
    G->ox0 = mnx < 0 ? 0 : (int)mnx; G->oy0 = mny < 0 ? 0 : (int)mny;
    G->ox1 = mxx > w ? w : (int)mxx + 1; G->oy1 = mxy > h ? h : (int)mxy + 1;
    if (G->ox1 > w) G->ox1 = w;
    if (G->oy1 > h) G->oy1 = h;
  }

  acc = (float *)calloc(N * 4, sizeof(float));
  if (!acc) goto done;
  c->lab = lab; c->c2g = c2g; c->groups = groups; c->ng = ng; c->acc = acc;
  /* draw in z order */
  for (int g = 0; g < ng; g++) { keys[g].key = groups[g].z; keys[g].idx = g; }
  qsort(keys, ng, sizeof(keyidx_t), cmp_key);
  for (int i = 0; i < ng; i++) {
    grp_t *G = &groups[keys[i].idx];
    if (G->ox1 <= G->ox0 || G->oy1 <= G->oy0) continue;
    gjob_t j = {c, keys[i].idx};
    par_rows(group_rows, &j, G->oy0, G->oy1, (long)(G->ox1 - G->ox0) * (G->oy1 - G->oy0));
  }
  for (size_t i = 0; i < N; i++) store(c, i, acc + 4 * i);
  ok = 1;
done:
  free(m); free(lab); free(comps); free(gpar); free(c2g); free(ord); free(groups); free(acc); free(keys);
  return ok;
}

/* ================================================================== ELEMENT: scissor cut-out */
/* exact squared euclidean distance transform (Felzenszwalb & Huttenlocher) */
static void edt1d(const double *f, int n, double *d, int *v, double *z) {
  int k = 0;
  v[0] = 0; z[0] = -1e30; z[1] = 1e30;
  for (int q = 1; q < n; q++) {
    double s = ((f[q] + (double)q * q) - (f[v[k]] + (double)v[k] * v[k])) / (2.0 * q - 2.0 * v[k]);
    while (s <= z[k] && k > 0) {
      k--;
      s = ((f[q] + (double)q * q) - (f[v[k]] + (double)v[k] * v[k])) / (2.0 * q - 2.0 * v[k]);
    }
    k++;
    v[k] = q; z[k] = s; z[k + 1] = 1e30;
  }
  k = 0;
  for (int q = 0; q < n; q++) {
    while (z[k + 1] < q) k++;
    double dq = (double)(q - v[k]);
    d[q] = dq * dq + f[v[k]];
  }
}
static int edt(const uint8_t *mask, int w, int h, float *out) {
  int n = w > h ? w : h;
  double *f = (double *)malloc(sizeof(double) * n), *d = (double *)malloc(sizeof(double) * n), *z = (double *)malloc(sizeof(double) * (n + 1));
  int *v = (int *)malloc(sizeof(int) * n);
  double *tmp = (double *)malloc(sizeof(double) * (size_t)w * h);
  if (!f || !d || !z || !v || !tmp) { free(f); free(d); free(z); free(v); free(tmp); return 0; }
  const double INF = 1e20;
  for (int x = 0; x < w; x++) {
    for (int y = 0; y < h; y++) f[y] = mask[(size_t)y * w + x] ? 0.0 : INF;
    edt1d(f, h, d, v, z);
    for (int y = 0; y < h; y++) tmp[(size_t)y * w + x] = d[y];
  }
  for (int y = 0; y < h; y++) {
    for (int x = 0; x < w; x++) f[x] = tmp[(size_t)y * w + x];
    edt1d(f, w, d, v, z);
    for (int x = 0; x < w; x++) out[(size_t)y * w + x] = (float)sqrt(d[x] > 1e18 ? 1e18 : d[x]);
  }
  free(f); free(d); free(z); free(v); free(tmp);
  return 1;
}

static const int MDX[8] = {-1, -1, 0, 1, 1, 1, 0, -1};
static const int MDY[8] = {0, -1, -1, -1, 0, 1, 1, 1};
static inline int dir_of(int dx, int dy) {
  for (int i = 0; i < 8; i++) if (MDX[i] == dx && MDY[i] == dy) return i;
  return 0;
}
/* Moore-neighbour tracing of the outer boundary of the component containing (sx,sy), the first pixel in raster order. */
static int trace(const int *lab, int l, int w, int h, int sx, int sy, float **pts, int *cap) {
  int n = 0, px = sx, py = sy, bdir = 0, first = -1;
  long maxit = 4L * ((long)w + h) * 8 + 100000;
  for (long it = 0; it < maxit; it++) {
    if (n + 1 >= *cap) {
      *cap *= 2;
      float *np = (float *)realloc(*pts, sizeof(float) * 2 * (*cap));
      if (!np) return n;
      *pts = np;
    }
    (*pts)[2 * n] = (float)px; (*pts)[2 * n + 1] = (float)py; n++;
    int found = -1;
    for (int i = 1; i <= 8; i++) {
      int d = (bdir + i) & 7, nx = px + MDX[d], ny = py + MDY[d];
      if (nx >= 0 && ny >= 0 && nx < w && ny < h && lab[(size_t)ny * w + nx] == l) { found = d; break; }
    }
    if (found < 0) break; /* single pixel */
    if (px == sx && py == sy) {
      if (first < 0) first = found;
      else if (found == first) { n--; break; }
    }
    int bd = (found + 7) & 7;
    int bx = px + MDX[bd], by = py + MDY[bd];
    int nx = px + MDX[found], ny = py + MDY[found];
    bdir = dir_of(bx - nx, by - ny);
    px = nx; py = ny;
  }
  return n;
}
static void rdp(const float *p, int a, int b, float eps, uint8_t *keep, int *stack) {
  int sp = 0;
  stack[sp++] = a; stack[sp++] = b;
  keep[a] = keep[b] = 1;
  while (sp) {
    int e = stack[--sp], s = stack[--sp];
    float ax = p[2 * s], ay = p[2 * s + 1], bx = p[2 * e], by = p[2 * e + 1];
    float vx = bx - ax, vy = by - ay, L2 = vx * vx + vy * vy, dmax = 0;
    int idx = -1;
    for (int i = s + 1; i < e; i++) {
      float wx = p[2 * i] - ax, wy = p[2 * i + 1] - ay, dd;
      if (L2 < 1e-9f) dd = wx * wx + wy * wy;
      else {
        float t = clamp01((wx * vx + wy * vy) / L2);
        float ex = wx - t * vx, ey = wy - t * vy;
        dd = ex * ex + ey * ey;
      }
      if (dd > dmax) { dmax = dd; idx = i; }
    }
    if (idx >= 0 && dmax > eps * eps) { keep[idx] = 1; stack[sp++] = s; stack[sp++] = idx; stack[sp++] = idx; stack[sp++] = e; }
  }
}
typedef struct { float x0, y0, x1, y1; int dir; } edge_t;
typedef struct { float x; int dir; } isect_t;
static int cmp_isect(const void *a, const void *b) {
  float d = ((const isect_t *)a)->x - ((const isect_t *)b)->x;
  return d < 0 ? -1 : (d > 0 ? 1 : 0);
}
/* non-zero winding fill of all edges, 4 sub-scanlines per row, exact horizontal coverage */
static void raster(const edge_t *E, int ne, int w, int h, float *cov) {
  isect_t *is = (isect_t *)malloc(sizeof(isect_t) * (ne + 2));
  if (!is) return;
  float ymin = 1e9f, ymax = -1e9f;
  for (int i = 0; i < ne; i++) {
    float a = fminf(E[i].y0, E[i].y1), b = fmaxf(E[i].y0, E[i].y1);
    if (a < ymin) ymin = a; if (b > ymax) ymax = b;
  }
  int ya = (int)floorf(ymin) - 1, yb = (int)ceilf(ymax) + 1;
  if (ya < 0) ya = 0; if (yb > h) yb = h;
  for (int y = ya; y < yb; y++) {
    float *row = cov + (size_t)y * w;
    for (int s = 0; s < 4; s++) {
      float sy = y - 0.5f + (s + 0.5f) * 0.25f;
      int n = 0;
      for (int i = 0; i < ne; i++) {
        const edge_t *e = &E[i];
        float lo = e->y0 < e->y1 ? e->y0 : e->y1, hi = e->y0 < e->y1 ? e->y1 : e->y0;
        if (sy < lo || sy >= hi) continue;
        is[n].x = e->x0 + (sy - e->y0) * (e->x1 - e->x0) / (e->y1 - e->y0);
        is[n].dir = e->dir; n++;
      }
      if (n < 2) continue;
      qsort(is, n, sizeof(isect_t), cmp_isect);
      int wind = 0;
      for (int i = 0; i < n - 1; i++) {
        wind += is[i].dir;
        if (!wind) continue;
        float xa = is[i].x + 0.5f, xb = is[i + 1].x + 0.5f; /* pixel x covers [x, x+1) after shift */
        if (xb <= xa) continue;
        if (xa < 0) xa = 0; if (xb > w) xb = (float)w;
        int ia = (int)xa, ib = (int)xb;
        if (ia >= w || xb <= 0) continue;
        if (ia == ib) { row[ia] += (xb - xa) * 0.25f; continue; }
        row[ia] += (ia + 1 - xa) * 0.25f;
        for (int x = ia + 1; x < ib && x < w; x++) row[x] += 0.25f;
        if (ib < w) row[ib] += (xb - ib) * 0.25f;
      }
    }
  }
  free(is);
}

/* scissor-cut backing: dilate, trace each piece, simplify into straight cuts, fill */
static void build_backing(ctx_t *c, const float *dist, float R, float *pb) {
  int w = c->w, h = c->h;
  size_t N = (size_t)w * h;
  uint8_t *mb = (uint8_t *)malloc(N);
  int *lab = (int *)malloc(sizeof(int) * N);
  float *pts = NULL; uint8_t *keep = NULL; int *stack = NULL; edge_t *E = NULL;
  if (!mb || !lab) goto out;
  for (size_t i = 0; i < N; i++) mb[i] = dist[i] <= R;
  int nl = ccl(mb, w, h, lab);
  if (nl <= 0) goto out;
  int cap = 4096, ecap = 1024, ne = 0;
  pts = (float *)malloc(sizeof(float) * 2 * cap);
  E = (edge_t *)malloc(sizeof(edge_t) * ecap);
  uint8_t *seen = (uint8_t *)calloc(nl + 1, 1);
  if (!pts || !E || !seen) { free(seen); goto out; }
  float eps = fmaxf(1.0f, 0.7f * R);
  for (int y = 0; y < h; y++)
    for (int x = 0; x < w; x++) {
      int l = lab[(size_t)y * w + x];
      if (!l || seen[l]) continue;
      seen[l] = 1;
      int n = trace(lab, l, w, h, x, y, &pts, &cap);
      if (n < 3) continue;
      free(keep); free(stack);
      keep = (uint8_t *)calloc(n + 2, 1);
      stack = (int *)malloc(sizeof(int) * 4 * (n + 2));
      if (!keep || !stack) continue;
      /* closed curve: split at the point farthest from the start */
      int far = 0; float fd = -1;
      for (int i = 1; i < n; i++) {
        float dx = pts[2 * i] - pts[0], dy = pts[2 * i + 1] - pts[1], dd = dx * dx + dy * dy;
        if (dd > fd) { fd = dd; far = i; }
      }
      /* append the start point so the second chain closes */
      if (n + 1 >= cap) { cap *= 2; float *np = (float *)realloc(pts, sizeof(float) * 2 * cap); if (!np) continue; pts = np; }
      pts[2 * n] = pts[0]; pts[2 * n + 1] = pts[1];
      if (far > 0) { rdp(pts, 0, far, eps, keep, stack); rdp(pts, far, n, eps, keep, stack); }
      /* collect kept vertices, jitter like a hand cut */
      int nk = 0;
      float *poly = (float *)malloc(sizeof(float) * 2 * (n + 1));
      if (!poly) continue;
      for (int i = 0; i < n; i++)
        if (keep[i]) {
          poly[2 * nk] = pts[2 * i] + rr(-0.3f, 0.3f, (uint32_t)l, (uint32_t)nk, c->seed + 41u) * eps;
          poly[2 * nk + 1] = pts[2 * i + 1] + rr(-0.3f, 0.3f, (uint32_t)l, (uint32_t)nk, c->seed + 43u) * eps;
          nk++;
        }
      if (nk < 3) { free(poly); continue; }
      float area = 0;
      for (int i = 0; i < nk; i++) { int j = (i + 1) % nk; area += poly[2 * i] * poly[2 * j + 1] - poly[2 * j] * poly[2 * i + 1]; }
      int flip = area < 0;
      for (int i = 0; i < nk; i++) {
        int a = flip ? nk - 1 - i : i, b = flip ? (nk - 2 - i + nk) % nk : (i + 1) % nk;
        float x0 = poly[2 * a], y0 = poly[2 * a + 1], x1 = poly[2 * b], y1 = poly[2 * b + 1];
        if (y0 == y1) continue;
        if (ne >= ecap) { ecap *= 2; edge_t *nE = (edge_t *)realloc(E, sizeof(edge_t) * ecap); if (!nE) break; E = nE; }
        E[ne].x0 = x0; E[ne].y0 = y0; E[ne].x1 = x1; E[ne].y1 = y1; E[ne].dir = y1 > y0 ? 1 : -1; ne++;
      }
      free(poly);
    }
  free(seen);
  if (ne) raster(E, ne, w, h, pb);
  for (size_t i = 0; i < N; i++) if (pb[i] > 1.f) pb[i] = 1.f;
out:
  free(mb); free(lab); free(pts); free(keep); free(stack); free(E);
}

static inline rgbf recolor(const ctx_t *c, float r, float g, float b, float x, float y) {
  float Y = lum(r, g, b), co = r - b, cg = g - 0.5f * (r + b);
  float chroma = sqrtf(co * co + cg * cg);
  if (chroma < 0.13f || Y < 0.06f || Y > 0.96f) {
    float amt = screen(&c->s_k, x, y, 1.f - contrast(c, Y));
    return mixc(c->paper, c->ink, amt);
  }
  int best = 0; float bd = 1e9f;
  for (int i = 0; i < 3; i++) {
    float d = (co - c->accCo[i]) * (co - c->accCo[i]) + (cg - c->accCg[i]) * (cg - c->accCg[i]);
    if (d < bd) { bd = d; best = i; }
  }
  rgbf base = best == 0 ? c->c1 : (best == 1 ? c->c2 : c->c3);
  float ref = c->accY[best]; /* the element's own average tone for this colour prints flat */
  float dY = Y - ref;
  if (dY < 0) {
    float cov = clamp01(-dY / fmaxf(ref, 0.15f) * c->gain);
    return mixc(base, c->ink, screen(&c->s_k, x, y, cov));
  }
  float cov = clamp01(dY / fmaxf(1.f - ref, 0.15f) * c->gain);
  return mixc(base, c->paper, screen(&c->s_a, x, y, cov));
}

/* 3-pass box blur ~ gaussian(sigma), in place, clamp to edges */
static void blur3(float *a, float *tmp, int w, int h, float sigma) {
  int r = (int)lroundf((sqrtf(4.f * sigma * sigma + 1.f) - 1.f) * 0.5f);
  if (r < 1) return;
  float inv = 1.f / (2 * r + 1);
  float *col = (float *)malloc(sizeof(float) * w);
  if (!col) return;
  for (int pass = 0; pass < 3; pass++) {
    for (int y = 0; y < h; y++) {
      const float *row = a + (size_t)y * w; float *o = tmp + (size_t)y * w;
      float acc = 0;
      for (int i = -r; i <= r; i++) acc += row[i < 0 ? 0 : (i >= w ? w - 1 : i)];
      for (int x = 0; x < w; x++) {
        o[x] = acc * inv;
        int ad = x + r + 1, sb = x - r;
        acc += row[ad >= w ? w - 1 : ad] - row[sb < 0 ? 0 : sb];
      }
    }
    for (int x = 0; x < w; x++) col[x] = 0;
    for (int i = -r; i <= r; i++) { const float *row = tmp + (size_t)(i < 0 ? 0 : (i >= h ? h - 1 : i)) * w; for (int x = 0; x < w; x++) col[x] += row[x]; }
    for (int y = 0; y < h; y++) {
      float *o = a + (size_t)y * w;
      for (int x = 0; x < w; x++) o[x] = col[x] * inv;
      int ad = y + r + 1, sb = y - r;
      const float *ra = tmp + (size_t)(ad >= h ? h - 1 : ad) * w, *rs = tmp + (size_t)(sb < 0 ? 0 : sb) * w;
      for (int x = 0; x < w; x++) col[x] += ra[x] - rs[x];
    }
  }
  free(col);
}

enum { ES_AUTO, ES_RISO, ES_PALETTE, ES_XEROX, ES_ORIGINAL };

/* flat graphics (logos, icons) print best with the palette; shaded art with riso plates */
static int is_flat(const ctx_t *c) {
  size_t N = (size_t)c->w * c->h, step = N > 400000 ? 3 : 1;
  unsigned *hist = (unsigned *)calloc(4096, sizeof(unsigned));
  if (!hist) return 0;
  double tot = 0;
  for (size_t i = 0; i < N; i += step) {
    const uint8_t *q = c->src + 4 * i;
    if (q[3] < 128) continue;
    hist[((q[0] >> 4) << 8) | ((q[1] >> 4) << 4) | (q[2] >> 4)]++; tot++;
  }
  double top = 0;
  for (int k = 0; k < 6; k++) {
    unsigned best = 0; int bi = -1;
    for (int i = 0; i < 4096; i++) if (hist[i] > best) { best = hist[i]; bi = i; }
    if (bi < 0) break;
    top += best; hist[bi] = 0;
  }
  free(hist);
  return tot > 0 && top / tot >= 0.72;
}

/* xerox / riso plates of the element (trailer cut-outs): auto levels, local contrast, edges */
static int prep_plates(ctx_t *c, float *keyA, float *colA, int riso) {
  int w = c->w, h = c->h;
  size_t N = (size_t)w * h;
  float *L = (float *)malloc(sizeof(float) * N), *A = (float *)malloc(sizeof(float) * N);
  float *B = (float *)malloc(sizeof(float) * N), *BA = (float *)malloc(sizeof(float) * N), *T = (float *)malloc(sizeof(float) * N);
  if (!L || !A || !B || !BA || !T) { free(L); free(A); free(B); free(BA); free(T); return 0; }
  unsigned hist[256] = {0}; double tot = 0;
  for (size_t i = 0; i < N; i++) {
    const uint8_t *q = c->src + 4 * i;
    A[i] = q[3] * (1.f / 255.f);
    L[i] = lum(q[0] / 255.f, q[1] / 255.f, q[2] / 255.f);
    if (q[3] >= 128) { hist[(int)(L[i] * 255.f + 0.5f)]++; tot++; }
  }
  float lo = 0, hi = 1; double run = 0;
  for (int b = 0, gl = 0; b < 256; b++) { run += hist[b]; if (!gl && run >= 0.03 * tot) { lo = b / 255.f; gl = 1; } if (run >= 0.97 * tot) { hi = b / 255.f; break; } }
  if (hi - lo < 0.05f) { lo = 0; hi = 1; }
  for (size_t i = 0; i < N; i++) { L[i] = clamp01((L[i] - lo) / (hi - lo)); B[i] = L[i] * A[i]; BA[i] = A[i]; }
  /* local contrast: unsharp against the element's own surroundings only */
  blur3(B, T, w, h, 6.f * c->k); blur3(BA, T, w, h, 6.f * c->k);
  float ct = clampf((float)c->in->p[P_CONTRAST], 0, 1);
  float sharp = 0.5f + 1.8f * ct, kth = 0.48f - 0.26f * ct, xth = 0.2f - 0.18f * ct, xg = 1.f + 0.4f * ct;
  for (size_t i = 0; i < N; i++) { float m = BA[i] > 0.02f ? B[i] / BA[i] : L[i]; B[i] = clamp01(L[i] + (L[i] - m) * sharp); }
  /* edges on a lightly blurred copy */
  for (size_t i = 0; i < N; i++) { T[i] = L[i] * A[i]; }
  memcpy(BA, A, sizeof(float) * N);
  float *T2 = L; /* L no longer needed after this copy */
  blur3(T, T2, w, h, 1.5f * c->k);
  blur3(BA, T2, w, h, 1.5f * c->k);
  for (size_t i = 0; i < N; i++) T[i] = BA[i] > 0.02f ? T[i] / BA[i] : 0.f;
  for (int y = 0; y < h; y++)
    for (int x = 0; x < w; x++) {
      size_t i = (size_t)y * w + x;
      if (A[i] <= 0.f) { keyA[i] = colA[i] = 0.f; continue; }
      float gx = (T[y * (size_t)w + (x < w - 1 ? x + 1 : x)] - T[y * (size_t)w + (x > 0 ? x - 1 : x)]) * 0.5f;
      float gy = (T[(size_t)(y < h - 1 ? y + 1 : y) * w + x] - T[(size_t)(y > 0 ? y - 1 : y) * w + x]) * 0.5f;
      float edge = smooth(0.045f, 0.11f, sqrtf(gx * gx + gy * gy) * c->k);
      float dark = powf(1.f - B[i], 1.15f);
      const uint8_t *q = c->src + 4 * i;
      float mx = fmaxf(fmaxf(q[0], q[1]), q[2]), mn = fminf(fminf(q[0], q[1]), q[2]);
      float sat = mx > 0 ? (mx - mn) / mx : 0.f;
      if (riso) {
        float key = powf(clamp01((dark - kth) * 1.5f), 1.1f);
        keyA[i] = fmaxf(key, edge * 0.75f);
        colA[i] = clamp01(0.25f + dark * 0.55f + sat * 0.25f);
      } else {
        keyA[i] = fmaxf(powf(clamp01((dark - xth) * xg), 1.35f), edge * 0.8f);
        colA[i] = 0.f;
      }
    }
  free(L); free(A); free(B); free(BA); free(T);
  return 1;
}

static void element_rows(void *vc, int y0, int y1) {
  ctx_t *c = (ctx_t *)vc;
  int w = c->w, h = c->h;
  float far = 1e6f;
  rgbf sh = scalec(c->ink, 0.6f);
  int plates = c->estyle == ES_RISO || c->estyle == ES_XEROX;
  for (int y = y0; y < y1; y++)
    for (int x = 0; x < w; x++) {
      float nx = 0, ny = 0;
      if (c->amp > 0) rough_disp(c, (float)x, (float)y, &nx, &ny);
      float sx = nx * c->amp, sy = ny * c->amp, qx = nx * c->amp_paper, qy = ny * c->amp_paper;
      float hx, hy; dot_jitter(c, x, y, &hx, &hy);
      float d[4] = {0, 0, 0, 0};
      rgbf pap = paper_at(c, c->paper, (float)x, (float)y);
      if (c->has_paper) {
        if (c->shop > 0) { float cs = samplef(c->pb, w, h, x - c->shx + qx, y - c->shy + qy); if (cs > 0) over(d, sh, cs * c->shop); }
        if (c->underplate) { float cp = samplef(c->pb, w, h, x - c->misx + qx, y - c->misy + qy); if (cp > 0) over(d, c->plate, cp); }
        float cb = samplef(c->pb, w, h, x + qx, y + qy);
        if (cb > 0) over(d, pap, cb);
      } else if (c->shop > 0) {
        float s4[4]; sample(c, x - c->shx + sx, y - c->shy + sy, 0, s4);
        if (s4[3] > 0) over(d, sh, smooth(0.2f, 0.8f, s4[3]) * c->shop);
      }
      if (c->outline_px > 0.01f) {
        float dd = samplef_far(c->dist, w, h, x + sx, y + sy, far);
        float co = clamp01(c->outline_px - dd + 0.5f);
        if (co > 0) over(d, c->ink, co);
      }
      float s4[4]; sample(c, x + sx, y + sy, 0, s4);
      float a = 0, amtK = 0;
      if (s4[3] > 0.0005f) {
        a = c->amp > 0 ? smooth(0.2f, 0.8f, s4[3]) : s4[3];
        rgbf O = {s4[0] / s4[3], s4[1] / s4[3], s4[2] / s4[3]};
        rgbf col = O;
        if (plates) {
          col = mixc(O, pap, c->recolor);
          amtK = screen(&c->s_k, hx, hy, samplef(c->keyA, w, h, x + sx, y + sy)) * c->recolor;
        } else if (c->estyle == ES_PALETTE && c->recolor > 0) {
          col = mixc(O, recolor(c, O.r, O.g, O.b, hx, hy), c->recolor);
        }
        over(d, col, a);
      }
      if (c->estyle == ES_RISO && c->recolor > 0) {
        float cv = samplef(c->colA, w, h, x + sx - c->misx, y + sy - c->misy);
        float amt = screen(&c->s_a, hx, hy, cv) * c->recolor;
        if (amt > 0) { d[0] *= lerpf(1.f, c->plate.r, amt); d[1] *= lerpf(1.f, c->plate.g, amt); d[2] *= lerpf(1.f, c->plate.b, amt); }
      }
      if (amtK > 0) over(d, c->ink, amtK * a);
      if (c->grain > 0 && d[3] > 0.5f) { /* toner dirt on the print */
        rgbf cur = {d[0] / d[3], d[1] / d[3], d[2] / d[3]};
        rgbf t = toner(c, cur, x, y);
        d[0] = t.r * d[3]; d[1] = t.g * d[3]; d[2] = t.b * d[3];
      }
      store(c, (size_t)y * w + x, d);
    }
}

static uint64_t fnv64(const void *p, size_t n, uint64_t hsh) {
  const uint64_t *q = (const uint64_t *)p;
  size_t nw = n / 8;
  for (size_t i = 0; i < nw; i++) { hsh ^= q[i]; hsh *= 0x100000001b3ULL; }
  const uint8_t *b = (const uint8_t *)p;
  for (size_t i = nw * 8; i < n; i++) { hsh ^= b[i]; hsh *= 0x100000001b3ULL; }
  return hsh;
}

static void run_element(ctx_t *c) {
  inst_t *in = (inst_t *)c->in;
  int w = c->w, h = c->h;
  size_t N = (size_t)w * h;
  c->outline_px = clampf((float)in->p[P_OUTLINE], 0, 1) * 16.f * c->k;
  float margin = clampf((float)in->p[P_MARGIN], 0, 1) * 40.f * c->k;
  float shd = clampf((float)in->p[P_SHADOW], 0, 1);
  c->shx = shd * 24.f * c->k; c->shy = shd * 34.f * c->k;
  c->shop = shd > 0 ? clampf((float)in->p[P_SHADOWOP], 0, 1) : 0.f;
  c->amp_paper = c->amp * 0.4f;
  c->recolor = clampf((float)in->p[P_RECOLOR], 0, 1);
  int estyle = (int)lround(clampf((float)in->p[P_ESTYLE], 0, 1) * 4);

  /* cache key: the frame itself + everything the cut and the plates depend on */
  double kp[8] = {in->p[P_OUTLINE], in->p[P_MARGIN], in->p[P_ESTYLE], in->p[P_CONTRAST], in->p[P_SEED], (double)w, (double)h, 1.0};
  uint64_t key = fnv64(c->src, N * 4, fnv64(kp, sizeof(kp), 0xcbf29ce484222325ULL));
  int locked = in->mu_ok && pz_mutex_trylock(&in->mu);
  float *dist = NULL, *pb = NULL, *keyA = NULL, *colA = NULL;
  int own = 1, has = 0;
  if (locked && in->ckey == key && in->cdist) {
    dist = in->cdist; pb = in->cpb; keyA = in->ckeyA; colA = in->ccolA; estyle = in->cstyle; has = in->chas; own = 0;
  } else {
    uint8_t *m = (uint8_t *)malloc(N);
    dist = (float *)malloc(sizeof(float) * N);
    pb = (float *)calloc(N, sizeof(float));
    int any = 0;
    if (m && dist && pb) {
      for (size_t i = 0; i < N; i++) { m[i] = c->src[4 * i + 3] >= 128; any |= m[i]; }
      if (any && edt(m, w, h, dist)) {
        has = margin >= 0.75f;
        if (has) build_backing(c, dist, c->outline_px + margin, pb);
        if (estyle == ES_AUTO) estyle = is_flat(c) ? ES_PALETTE : ES_RISO;
        if (estyle == ES_RISO || estyle == ES_XEROX) {
          keyA = (float *)malloc(sizeof(float) * N); colA = (float *)malloc(sizeof(float) * N);
          if (!keyA || !colA || !prep_plates(c, keyA, colA, estyle == ES_RISO)) { free(keyA); free(colA); keyA = colA = NULL; estyle = ES_ORIGINAL; }
        }
      } else any = 0;
    }
    free(m);
    if (!any) {
      free(dist); free(pb);
      if (locked) pz_mutex_unlock(&in->mu);
      for (size_t i = 0; i < N; i++) { float a = c->src[4 * i + 3] / 255.f; float e[4] = {c->src[4 * i] / 255.f * a, c->src[4 * i + 1] / 255.f * a, c->src[4 * i + 2] / 255.f * a, a}; store(c, i, e); }
      return;
    }
    if (locked) { /* keep for the next frame */
      free(in->cdist); free(in->cpb); free(in->ckeyA); free(in->ccolA);
      in->cdist = dist; in->cpb = pb; in->ckeyA = keyA; in->ccolA = colA; in->cstyle = estyle; in->chas = has; in->ckey = key;
      own = 0;
    }
  }
  c->estyle = estyle; c->has_paper = has; c->dist = dist; c->pb = pb; c->keyA = keyA; c->colA = colA;
  c->s_k = mkscreen(c->cell * 0.75f, c->ang); c->s_a = mkscreen(c->cell * 0.86f, c->ang - 30.f);
  c->underplate = (estyle == ES_PALETTE || estyle == ES_ORIGINAL) && (c->misx != 0 || c->misy != 0);
  if (estyle == ES_PALETTE) {
    /* each riso colour prints flat at the element's own average tone for it */
    rgbf A[3] = {c->c1, c->c2, c->c3};
    double sum[3] = {0, 0, 0}, cnt[3] = {0, 0, 0};
    for (int i = 0; i < 3; i++) { c->accCo[i] = A[i].r - A[i].b; c->accCg[i] = A[i].g - 0.5f * (A[i].r + A[i].b); }
    size_t stepi = N > 400000 ? 3 : 1;
    for (size_t i = 0; i < N; i += stepi) {
      const uint8_t *p = c->src + 4 * i;
      if (p[3] < 128) continue;
      float r = p[0] / 255.f, g = p[1] / 255.f, b = p[2] / 255.f, Y = lum(r, g, b), co = r - b, cg = g - 0.5f * (r + b);
      if (sqrtf(co * co + cg * cg) < 0.13f || Y < 0.06f || Y > 0.96f) continue;
      int best = 0; float bd = 1e9f;
      for (int k = 0; k < 3; k++) { float d = (co - c->accCo[k]) * (co - c->accCo[k]) + (cg - c->accCg[k]) * (cg - c->accCg[k]); if (d < bd) { bd = d; best = k; } }
      sum[best] += Y; cnt[best] += 1;
    }
    for (int i = 0; i < 3; i++) c->accY[i] = cnt[i] > 0 ? (float)(sum[i] / cnt[i]) : lum(A[i].r, A[i].g, A[i].b);
    c->gain = 1.f + 3.f * clampf((float)in->p[P_CONTRAST], 0, 1);
  }
  par_rows(element_rows, c, 0, h, (long)N);
  if (locked) pz_mutex_unlock(&in->mu);
  if (own) { free(dist); free(pb); free(keyA); free(colA); }
}

/* ================================================================== frei0r API */
int f0r_init(void) { pz_once(init_tables); return 1; }
void f0r_deinit(void) {}

void f0r_get_plugin_info(f0r_plugin_info_t *info) {
  info->name = "Punk Zine";
  info->author = "cookieukw";
  info->plugin_type = F0R_PLUGIN_TYPE_FILTER;
  info->color_model = F0R_COLOR_MODEL_RGBA8888;
  info->frei0r_version = FREI0R_MAJOR_VERSION;
  info->major_version = 1;
  info->minor_version = 0;
  info->num_params = NPARAM;
  info->explanation = "70s punk zine print: ransom-note letters, scissor cut-outs, xerox and riso halftone";
}

void f0r_get_param_info(f0r_param_info_t *info, int i) {
  if (i < 0 || i >= NPARAM) return;
  info->name = PDEF[i].name;
  info->type = PDEF[i].type;
  info->explanation = PDEF[i].expl;
}

static rgbf hexc(uint32_t v) { rgbf c = {((v >> 16) & 255) / 255.f, ((v >> 8) & 255) / 255.f, (v & 255) / 255.f}; return c; }

f0r_instance_t f0r_construct(unsigned int width, unsigned int height) {
  pz_once(init_tables);
  if (!g_noise || !g_paper) return NULL;
  inst_t *in = (inst_t *)calloc(1, sizeof(inst_t));
  if (!in) return NULL;
  in->w = width; in->h = height;
  in->mu_ok = pz_mutex_init(&in->mu);
  for (int i = 0; i < NPARAM; i++) {
    in->p[i] = PDEF[i].def;
    if (PDEF[i].type == F0R_PARAM_COLOR) in->col[i - FIRSTCOL] = hexc(PDEF[i].rgb);
  }
  return (f0r_instance_t)in;
}
void f0r_destruct(f0r_instance_t inst) {
  inst_t *in = (inst_t *)inst;
  if (!in) return;
  free(in->cdist); free(in->cpb); free(in->ckeyA); free(in->ccolA);
  if (in->mu_ok) pz_mutex_destroy(&in->mu);
  free(in);
}

void f0r_set_param_value(f0r_instance_t inst, f0r_param_t param, int i) {
  inst_t *in = (inst_t *)inst;
  if (!in || !param || i < 0 || i >= NPARAM) return;
  if (PDEF[i].type == F0R_PARAM_COLOR) {
    const f0r_param_color_t *c = (const f0r_param_color_t *)param;
    rgbf v = {clamp01(c->r), clamp01(c->g), clamp01(c->b)};
    in->col[i - FIRSTCOL] = v;
  } else {
    double v = *(const double *)param;
    if (v != v) v = PDEF[i].def; /* NaN */
    in->p[i] = v;
  }
}
void f0r_get_param_value(f0r_instance_t inst, f0r_param_t param, int i) {
  inst_t *in = (inst_t *)inst;
  if (!in || !param || i < 0 || i >= NPARAM) return;
  if (PDEF[i].type == F0R_PARAM_COLOR) {
    f0r_param_color_t *c = (f0r_param_color_t *)param;
    c->r = in->col[i - FIRSTCOL].r; c->g = in->col[i - FIRSTCOL].g; c->b = in->col[i - FIRSTCOL].b;
  } else *(double *)param = in->p[i];
}

void f0r_update(f0r_instance_t inst, double time, const uint32_t *inframe, uint32_t *outframe) {
  inst_t *in = (inst_t *)inst;
  if (!in || !inframe || !outframe) return;
  int w = (int)in->w, h = (int)in->h;
  size_t N = (size_t)w * h;
  if (w <= 0 || h <= 0) return;
  const uint8_t *src = (const uint8_t *)inframe;
  uint8_t *copy = NULL;
  if ((const void *)inframe == (const void *)outframe) {
    copy = (uint8_t *)malloc(N * 4);
    if (!copy) return;
    memcpy(copy, inframe, N * 4);
    src = copy;
  }
  float mix = clampf((float)in->p[P_MIX], 0, 1);
  if (mix <= 0.0005f) { if (!copy) memcpy(outframe, inframe, N * 4); free(copy); return; }

  ctx_t c;
  memset(&c, 0, sizeof(c));
  c.in = in; c.w = w; c.h = h; c.src = src; c.dst = (uint8_t *)outframe; c.mix = mix;
  c.k = sqrtf((float)w * (float)h / (1920.f * 1080.f));
  if (c.k < 0.05f) c.k = 0.05f;
  c.invk = 1.f / c.k;
  c.seed = (uint32_t)lround(clampf((float)in->p[P_SEED], 0, 1) * 1000.0);
  /* still print: the result never changes over time (use a wobble effect such as Squigglevision for boil) */
  (void)time;
  c.step = 0u;
  c.ox = r01(c.step, 1, c.seed) * TEX; c.oy = r01(c.step, 2, c.seed) * TEX;
  c.ink = in->col[0]; c.paper = in->col[1]; c.c1 = in->col[2]; c.c2 = in->col[3]; c.c3 = in->col[4];
  c.plate = c.c1; /* color1 = the riso plate ink */
  c.grey = scalec(c.paper, 0.875f);
  rgbf nf = {0.956f, 0.94f, 0.9f}; c.news = mulc(c.paper, nf);
  rgbf kr = {0.788f, 0.659f, 0.467f}; c.kraft = kr;
  float ct = clampf((float)in->p[P_CONTRAST], 0, 1);
  c.contrast_lo = 0.3f * ct; c.contrast_hi = 1.f - 0.3f * ct;
  float cell = (3.f + 20.f * clampf((float)in->p[P_DOT], 0, 1)) * c.k;
  float ang = clampf((float)in->p[P_ANGLE], 0, 1) * 45.f; /* the dot grid repeats every 90 degrees and mirrors at 45 */
  c.cell = cell; c.ang = ang;
  c.s_k = mkscreen(cell, ang); c.s_a = mkscreen(cell * 1.08f, ang - 30.f); c.s_b = mkscreen(cell * 1.04f, ang + 30.f);
  c.paper_tex = clampf((float)in->p[P_PAPERTEX], 0, 1);
  c.grain = clampf((float)in->p[P_GRAIN], 0, 1);
  c.burn = clampf((float)in->p[P_BURN], 0, 1);
  float mis = clampf((float)in->p[P_MISREG], 0, 1);
  c.misx = mis * 30.f * c.k; c.misy = mis * 22.f * c.k;
  c.keepcol = in->p[P_KEEPCOL] >= 0.5;
  float rough = clampf((float)in->p[P_ROUGH], 0, 1);

  int mode = (int)lround(clampf((float)in->p[P_MODE], 0, 1) * 3);
  if (mode == 0) {
    /* auto: anything with a transparent background is a graphic */
    size_t step = N > 200000 ? 7 : 1, tr = 0, tot = 0;
    for (size_t i = 0; i < N; i += step) { tot++; if (src[4 * i + 3] < 16) tr++; }
    mode = (tr * 200 > tot) ? -1 : 3; /* > 0.5% transparent */
  }
  if (mode == 3) {
    c.style = (int)lround(clampf((float)in->p[P_STYLE], 0, 1) * 3);
    { /* auto levels (1%..99%) so dark or flat footage still prints */
      unsigned hist[256] = {0}; double tot = 0, run = 0;
      size_t st = N > 300000 ? 5 : 1;
      for (size_t i = 0; i < N; i += st) { const uint8_t *q = src + 4 * i; hist[(int)(lum(q[0], q[1], q[2]) + 0.5f) & 255]++; tot++; }
      int lo = 0, hi = 255, got = 0;
      for (int b = 0; b < 256; b++) { run += hist[b]; if (!got && run >= 0.01 * tot) { lo = b; got = 1; } if (run >= 0.99 * tot) { hi = b; break; } }
      if (hi - lo < 40) { int mid = (hi + lo) / 2; lo = mid - 20 < 0 ? 0 : mid - 20; hi = lo + 40; }
      c.lv_lo = lo / 255.f; c.lv_inv = 255.f / (float)(hi - lo);
    }
    c.wob = (1.f * rough + 3.f * rough * rough) * c.k;
    c.jit = rough * 0.4f * cell;
    par_rows(image_rows, &c, 0, h, (long)N);
  } else {
    int done = 0;
    if (mode == -1 || mode == 1) {
      c.amp = (3.f * rough + 9.f * rough * rough) * 0.7f * c.k;
      c.amp_edge = (1.5f * rough + 5.f * rough * rough) * 0.7f * c.k;
      done = run_text(&c, mode == 1);
    }
    if (!done) {
      c.amp = (3.f * rough + 8.f * rough * rough) * 0.7f * c.k;
      c.jit = rough * 0.3f * cell * 0.75f;
      run_element(&c);
    }
  }
  free(copy);
}
