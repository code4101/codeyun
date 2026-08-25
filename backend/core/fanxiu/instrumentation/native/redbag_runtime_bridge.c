#include <stdint.h>

#if defined(__GNUC__)
#define CODEYUN_EXPORT __attribute__((visibility("default")))
#else
#define CODEYUN_EXPORT
#endif

typedef int (*lua_gettop_fn)(void *state);
typedef int (*lua_loadstring_fn)(void *state, const char *chunk);
typedef int (*lua_pcall_fn)(void *state, int nargs, int nresults, int errfunc);
typedef void (*lua_settop_fn)(void *state, int top);

/*
 * NativeBridge creates JNI-shaped host trampolines.  The adapter intentionally
 * ignores JNIEnv/jobject and exposes only two bounded operations; it is not a
 * generic Lua evaluator.
 */
CODEYUN_EXPORT int codeyun_redbag_bridge_probe(
    void *env,
    void *object,
    int64_t value
) {
    (void)env;
    (void)object;
    return value == INT64_C(0x434f444558) ? 1 : 0;
}

CODEYUN_EXPORT int codeyun_ensure_redbag_manager(
    void *env,
    void *object,
    int64_t state_address,
    int64_t gettop_address,
    int64_t loadstring_address,
    int64_t pcall_address,
    int64_t settop_address
) {
    static const char chunk[] =
        "local M=require('GameSystem.Game.Redbag.Mgr.RedbagMgr');"
        "M.Inst_get()";
    void *state = (void *)(uintptr_t)state_address;
    lua_gettop_fn gettop = (lua_gettop_fn)(uintptr_t)gettop_address;
    lua_loadstring_fn loadstring =
        (lua_loadstring_fn)(uintptr_t)loadstring_address;
    lua_pcall_fn pcall = (lua_pcall_fn)(uintptr_t)pcall_address;
    lua_settop_fn settop = (lua_settop_fn)(uintptr_t)settop_address;
    int original_top;
    int status;

    (void)env;
    (void)object;
    if (
        state == 0
        || gettop == 0
        || loadstring == 0
        || pcall == 0
        || settop == 0
    ) {
        return -100;
    }

    original_top = gettop(state);
    status = loadstring(state, chunk);
    if (status == 0) {
        status = pcall(state, 0, 0, 0);
    }
    settop(state, original_top);
    return status;
}
