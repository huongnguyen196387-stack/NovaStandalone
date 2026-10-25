#include <jni.h>
#include <android/log.h>
#include <atomic>
#include <chrono>
#include <string>
#include <thread>
#include "hook_engine.h"
#include "culling_hooks.h"

#define NOVA_BOOT_I(...) __android_log_print(ANDROID_LOG_INFO, "NovaBootstrap", __VA_ARGS__)
#define NOVA_BOOT_E(...) __android_log_print(ANDROID_LOG_ERROR, "NovaBootstrap", __VA_ARGS__)

namespace {
std::atomic<bool> g_gameWatcherStarted{false};

void startGameSideWatcher() {
    bool expected = false;
    if (!g_gameWatcherStarted.compare_exchange_strong(expected, true)) {
        NOVA_BOOT_I("Game-side watcher already started");
        return;
    }
    std::thread([] {
        NOVA_BOOT_I("Waiting for libminecraftpe.so in this process (maximum 120 seconds)");
        bool gameLoaded = false;
        for (int attempt = 0; attempt < 600; ++attempt) {
            if (nova::hooks::isMinecraftLoadedInThisProcess()) {
                gameLoaded = true;
                break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }
        if (!gameLoaded) {
            NOVA_BOOT_E("Timed out: libminecraftpe.so did not appear in this process");
            return;
        }

        // Give the engine a short grace period after the ELF is mapped before scanning it.
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
        const bool installed = NovaInitializeCullingHooks();
        const char* status = NovaGetCullingHookStatus();
        if (installed) {
            NOVA_BOOT_I("Culling hook initialization finished: %s", status ? status : "(no status)");
        } else {
            NOVA_BOOT_E("Culling hook initialization failed: %s", status ? status : "(no status)");
        }
    }).detach();
}
}

namespace nova {
constexpr const char* kVersion = "Nova Native Core 0.4.0";
std::atomic<bool> g_hud_enabled{false};

std::string status() {
    const bool game = hooks::isMinecraftLoadedInThisProcess();
    return std::string("Native core loaded\nProcess: ") +
        (game ? "Minecraft process" : "launcher process") +
        "\nMinecraft library present here: " + (game ? "yes" : "no") +
        "\nHUD flag: " + (g_hud_enabled.load() ? "enabled (state only)" : "disabled") +
        "\n\n" + culling::status().message;
}
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_nova_standalone_MainActivity_nativeGetVersion(JNIEnv* env, jobject) {
    return env->NewStringUTF(nova::kVersion);
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_nova_standalone_MainActivity_nativeGetStatus(JNIEnv* env, jobject) {
    const auto value = nova::status();
    return env->NewStringUTF(value.c_str());
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_nova_standalone_MainActivity_nativeSetHudEnabled(JNIEnv*, jobject, jboolean enabled) {
    nova::g_hud_enabled.store(enabled == JNI_TRUE);
    return JNI_TRUE;
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_nova_standalone_MainActivity_nativeCanHookMinecraft(JNIEnv*, jobject) {
    return nova::hooks::isMinecraftLoadedInThisProcess() ? JNI_TRUE : JNI_FALSE;
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_nova_standalone_MainActivity_nativeGetCullingStatus(JNIEnv* env, jobject) {
    const auto value = nova::culling::status().message;
    return env->NewStringUTF(value.c_str());
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_nova_standalone_MainActivity_nativeInitializeHooks(JNIEnv*, jobject) {
    const auto result = nova::culling::initialize();
    return result.queryAHooked && result.queryBHooked ? JNI_TRUE : JNI_FALSE;
}

extern "C" JNIEXPORT void JNICALL
Java_com_nova_standalone_NativeBootstrap_startWatcherNative(JNIEnv*, jclass) {
    startGameSideWatcher();
}

JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM*, void*) {
    // Deliberately do not start a polling thread here: the standalone launcher
    // also loads this library. The patched game-side bootstrap explicitly calls
    // Java_com_nova_standalone_NativeBootstrap_startWatcherNative().
    return JNI_VERSION_1_6;
}
