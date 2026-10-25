#include "hook_engine.h"
#include "culling_hooks.h"

#include <EGL/egl.h>
#include <android/log.h>
#include <dobby.h>
#include <dlfcn.h>
#include <link.h>
#include <atomic>
#include <cstring>

#define NOVA_LOGI(...) __android_log_print(ANDROID_LOG_INFO, "NovaCore", __VA_ARGS__)
#define NOVA_LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "NovaCore", __VA_ARGS__)

namespace {
std::atomic<uint64_t> g_frames{0};
using EglSwapBuffersFn = EGLBoolean (*)(EGLDisplay, EGLSurface);
EglSwapBuffersFn g_originalSwapBuffers = nullptr;

EGLBoolean novaEglSwapBuffers(EGLDisplay display, EGLSurface surface) {
    g_frames.fetch_add(1, std::memory_order_relaxed);
    if (g_originalSwapBuffers == nullptr) return EGL_FALSE;
    return g_originalSwapBuffers(display, surface);
}

int findMinecraftModule(dl_phdr_info* info, size_t, void* data) {
    if (info && info->dlpi_name &&
        std::strstr(info->dlpi_name, "libminecraftpe.so") != nullptr) {
        *static_cast<bool*>(data) = true;
        return 1;
    }
    return 0;
}
}

namespace nova::hooks {
bool isMinecraftLoadedInThisProcess() {
    bool found = false;
    dl_iterate_phdr(findMinecraftModule, &found);
    return found;
}

HookResult installFrameHook() {
    if (!isMinecraftLoadedInThisProcess()) {
        return {false, "libminecraftpe.so is not loaded in this process"};
    }
    void* target = dlsym(RTLD_DEFAULT, "eglSwapBuffers");
    if (target == nullptr) {
        return {false, "eglSwapBuffers was not resolved"};
    }
    if (g_originalSwapBuffers != nullptr) {
        return {true, "frame hook already installed"};
    }
    int rc = DobbyHook(target, reinterpret_cast<void*>(novaEglSwapBuffers),
                       reinterpret_cast<void**>(&g_originalSwapBuffers));
    if (rc != 0 || g_originalSwapBuffers == nullptr) {
        g_originalSwapBuffers = nullptr;
        return {false, "DobbyHook failed for eglSwapBuffers"};
    }
    NOVA_LOGI("Installed EGL frame hook inside Minecraft process");
    return {true, "EGL frame hook installed"};
}

uint64_t frameCount() {
    return g_frames.load(std::memory_order_relaxed);
}
}

extern "C" bool NovaIsMinecraftProcess() {
    return nova::hooks::isMinecraftLoadedInThisProcess();
}

extern "C" bool NovaInitializeInGameProcess() {
    if (!nova::hooks::isMinecraftLoadedInThisProcess()) {
        NOVA_LOGE("Refusing to install game hooks outside Minecraft process");
        return false;
    }
    const bool cullingOk = NovaInitializeCullingHooks();
    const auto frameResult = nova::hooks::installFrameHook();
    NOVA_LOGI("Frame hook: %s; culling hooks: %s",
              frameResult.message, cullingOk ? "installed" : "not fully installed");
    // This API reports culling readiness specifically; the EGL hook is an
    // independent optional instrumentation hook.
    return cullingOk;
}
