#ifndef WASABITOR_H
#define WASABITOR_H
#include <stdint.h>
#include <stddef.h>

typedef void (*wasabitor_callback)(void *ctx, int32_t status, const uint8_t *body, size_t len, const char *error);

int32_t wasabitor_start(const char *dir);
int32_t wasabitor_progress(void);
void wasabitor_post(const char *auth_key, uint16_t port, const char *path, const char *host, const char *authorization,
                    const uint8_t *body, size_t len, void *ctx, wasabitor_callback cb);
#endif
