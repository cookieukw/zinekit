/* Minimal frei0r 1.2 API declarations (the plugin ABI MLT/Kdenlive load). */
#ifndef PUNKZINE_FREI0R_H
#define PUNKZINE_FREI0R_H
#include <stdint.h>

#define FREI0R_MAJOR_VERSION 1
#define FREI0R_MINOR_VERSION 2

#define F0R_PLUGIN_TYPE_FILTER 0
#define F0R_COLOR_MODEL_BGRA8888 0
#define F0R_COLOR_MODEL_RGBA8888 1
#define F0R_COLOR_MODEL_PACKED32 2

#define F0R_PARAM_BOOL 0
#define F0R_PARAM_DOUBLE 1
#define F0R_PARAM_COLOR 2
#define F0R_PARAM_POSITION 3
#define F0R_PARAM_STRING 4

typedef struct f0r_plugin_info {
  const char *name;
  const char *author;
  int plugin_type;
  int color_model;
  int frei0r_version;
  int major_version;
  int minor_version;
  int num_params;
  const char *explanation;
} f0r_plugin_info_t;

typedef struct f0r_param_info {
  const char *name;
  int type;
  const char *explanation;
} f0r_param_info_t;

typedef double f0r_param_bool;
typedef double f0r_param_double;
typedef struct f0r_param_color { float r, g, b; } f0r_param_color_t;
typedef struct f0r_param_position { double x, y; } f0r_param_position_t;
typedef char *f0r_param_string;

typedef void *f0r_instance_t;
typedef void *f0r_param_t;

int f0r_init(void);
void f0r_deinit(void);
void f0r_get_plugin_info(f0r_plugin_info_t *info);
void f0r_get_param_info(f0r_param_info_t *info, int param_index);
f0r_instance_t f0r_construct(unsigned int width, unsigned int height);
void f0r_destruct(f0r_instance_t instance);
void f0r_set_param_value(f0r_instance_t instance, f0r_param_t param, int param_index);
void f0r_get_param_value(f0r_instance_t instance, f0r_param_t param, int param_index);
void f0r_update(f0r_instance_t instance, double time, const uint32_t *inframe, uint32_t *outframe);

#endif
