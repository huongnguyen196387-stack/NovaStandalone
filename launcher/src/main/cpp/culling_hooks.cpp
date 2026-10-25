#include "culling_hooks.h"

#include "signature_scanner.h"

#include <android/log.h>
#include <dobby.h>
#include <link.h>
#include <atomic>
#include <algorithm>
#include <cstring>
#include <mutex>
#include <sstream>
#include <vector>

#define NOVA_CULL_I(...) __android_log_print(ANDROID_LOG_INFO, "NovaCulling", __VA_ARGS__)
#define NOVA_CULL_E(...) __android_log_print(ANDROID_LOG_ERROR, "NovaCulling", __VA_ARGS__)

namespace {
// Both disassembled functions use x0=this, x1=an opaque query argument and
// return their predicate in w0. Keep the argument opaque until the exact C++
// type is proven; these hooks do not modify it or the return value.
using CullerQueryFn = bool (*)(void* self, const void* query);
CullerQueryFn g_originalQueryA = nullptr;
CullerQueryFn g_originalQueryB = nullptr;
std::atomic<bool> g_queryAHooked{false};
std::atomic<bool> g_queryBHooked{false};
std::atomic<uint64_t> g_queryACalls{0};
std::atomic<uint64_t> g_queryBCalls{0};
std::mutex g_installMutex;
std::string g_lastStatus = "Not initialized";

// Extracted from nova_client/resources/signatures.ini in the supplied ARM64
// LeviPack. Every fixed byte was compared against culler_asm.txt at the
// two labelled function starts; '?' bytes remain wildcards.
constexpr char kQueryASignature[] =
    "FF 83 01 D1 FD 7B 02 A9 F7 1B 00 F9 F6 57 04 A9 "
    "F4 4F 05 A9 FD 83 00 91 55 D0 3B D5 F3 03 00 AA "
    "00 C0 03 91 A8 16 40 F9";
constexpr char kQueryBSignature[] =
    "FF 83 01 D1 FD 7B 02 A9 F7 1B 00 F9 F6 57 04 A9 "
    "F4 4F 05 A9 FD 83 00 91 55 D0 3B D5 F4 03 01 AA "
    "A8 16 40 F9 A8 83 1F F8 ? ? ? ? F3 03 00 AA "
    "00 C0 03 91 ? ? ? ?";

int findMinecraftOnly(dl_phdr_info* info, size_t, void* opaque) {
    if (info && info->dlpi_name &&
        std::strstr(info->dlpi_name, "libminecraftpe.so") != nullptr) {
        *static_cast<bool*>(opaque) = true;
        return 1;
    }
    return 0;
}

struct ModuleScan {
    bool found = false;
    std::vector<uintptr_t> queryA;
    std::vector<uintptr_t> queryB;
};

int scanModule(dl_phdr_info* info, size_t, void* opaque) {
    if (!info || !info->dlpi_name ||
        std::strstr(info->dlpi_name, "libminecraftpe.so") == nullptr) {
        return 0;
    }
    auto* result = static_cast<ModuleScan*>(opaque);
    result->found = true;
    const auto patternA = nova::sig::parse(kQueryASignature);
    const auto patternB = nova::sig::parse(kQueryBSignature);

    for (ElfW(Half) i = 0; i < info->dlpi_phnum; ++i) {
        const ElfW(Phdr)& phdr = info->dlpi_phdr[i];
        if (phdr.p_type != PT_LOAD || (phdr.p_flags & PF_X) == 0 || phdr.p_memsz == 0) {
            continue;
        }
        auto* segment = reinterpret_cast<const uint8_t*>(info->dlpi_addr + phdr.p_vaddr);
        const size_t segmentSize = static_cast<size_t>(phdr.p_memsz);
        auto a = nova::sig::findAll(segment, segmentSize, patternA);
        auto b = nova::sig::findAll(segment, segmentSize, patternB);
        result->queryA.insert(result->queryA.end(), a.begin(), a.end());
        result->queryB.insert(result->queryB.end(), b.begin(), b.end());
    }
    return 1;
}

bool queryAHook(void* self, const void* query) {
    const uint64_t call = g_queryACalls.fetch_add(1, std::memory_order_relaxed);
    if (call == 0) NOVA_CULL_I("cullerQueryA hook was called; pass-through mode");
    CullerQueryFn original = g_originalQueryA;
    return original ? original(self, query) : false;
}

bool queryBHook(void* self, const void* query) {
    const uint64_t call = g_queryBCalls.fetch_add(1, std::memory_order_relaxed);
    if (call == 0) NOVA_CULL_I("cullerQueryB hook was called; pass-through mode");
    CullerQueryFn original = g_originalQueryB;
    return original ? original(self, query) : false;
}

void uniqueAddresses(std::vector<uintptr_t>& addresses) {
    std::sort(addresses.begin(), addresses.end());
    addresses.erase(std::unique(addresses.begin(), addresses.end()), addresses.end());
}
}

namespace nova::culling {
Status status() {
    bool loaded = false;
    dl_iterate_phdr(findMinecraftOnly, &loaded);
    std::ostringstream out;
    out << "libminecraftpe.so: " << (loaded ? "loaded in this process" : "not loaded in this process")
        << "\ncullerQueryA: " << (g_queryAHooked.load() ? "hook installed" : "not hooked")
        << " (calls=" << g_queryACalls.load() << ")"
        << "\ncullerQueryB: " << (g_queryBHooked.load() ? "hook installed" : "not hooked")
        << " (calls=" << g_queryBCalls.load() << ")";
    {
        std::lock_guard<std::mutex> guard(g_installMutex);
        out << "\nLast init: " << g_lastStatus;
    }
    return {loaded, g_queryAHooked.load(), g_queryBHooked.load(), out.str()};
}

Status initialize() {
    std::lock_guard<std::mutex> guard(g_installMutex);
    if (g_queryAHooked.load() && g_queryBHooked.load()) {
        g_lastStatus = "Both culling observation hooks are already installed";
        return {true, true, true, g_lastStatus};
    }
    ModuleScan scan;
    dl_iterate_phdr(scanModule, &scan);
    if (!scan.found) {
        g_lastStatus = "Refused: libminecraftpe.so is not loaded in this process";
        NOVA_CULL_E("%s", g_lastStatus.c_str());
        return {false, false, false, g_lastStatus};
    }

    uniqueAddresses(scan.queryA);
    uniqueAddresses(scan.queryB);
    if ((!g_queryAHooked.load() && scan.queryA.size() != 1) ||
        (!g_queryBHooked.load() && scan.queryB.size() != 1)) {
        std::ostringstream out;
        out << "Refused ambiguous/missing signatures for unhooked targets: cullerQueryA matches="
            << scan.queryA.size() << ", cullerQueryB matches=" << scan.queryB.size()
            << ". Need exactly one match for each target that is not already hooked.";
        g_lastStatus = out.str();
        NOVA_CULL_E("%s", g_lastStatus.c_str());
        return {true, g_queryAHooked.load(), g_queryBHooked.load(), g_lastStatus};
    }

    if (!g_queryAHooked.load()) {
        void* original = nullptr;
        const int rc = DobbyHook(reinterpret_cast<void*>(scan.queryA[0]),
                                 reinterpret_cast<void*>(queryAHook), &original);
        if (rc == 0 && original != nullptr) {
            g_originalQueryA = reinterpret_cast<CullerQueryFn>(original);
            g_queryAHooked.store(true);
            NOVA_CULL_I("Installed cullerQueryA at runtime address %p", reinterpret_cast<void*>(scan.queryA[0]));
        } else {
            NOVA_CULL_E("DobbyHook failed for cullerQueryA (rc=%d)", rc);
        }
    }

    if (!g_queryBHooked.load()) {
        void* original = nullptr;
        const int rc = DobbyHook(reinterpret_cast<void*>(scan.queryB[0]),
                                 reinterpret_cast<void*>(queryBHook), &original);
        if (rc == 0 && original != nullptr) {
            g_originalQueryB = reinterpret_cast<CullerQueryFn>(original);
            g_queryBHooked.store(true);
            NOVA_CULL_I("Installed cullerQueryB at runtime address %p", reinterpret_cast<void*>(scan.queryB[0]));
        } else {
            NOVA_CULL_E("DobbyHook failed for cullerQueryB (rc=%d)", rc);
        }
    }

    if (g_queryAHooked.load() && g_queryBHooked.load()) {
        g_lastStatus = "Both culling observation hooks installed; pass-through mode (no culling behavior changed)";
    } else {
        g_lastStatus = "Partial/failed install. Check logcat tag NovaCulling; no FPS gain is implied.";
    }
    NOVA_CULL_I("%s", g_lastStatus.c_str());
    return {true, g_queryAHooked.load(), g_queryBHooked.load(), g_lastStatus};
}

uint64_t queryACalls() { return g_queryACalls.load(std::memory_order_relaxed); }
uint64_t queryBCalls() { return g_queryBCalls.load(std::memory_order_relaxed); }
}

extern "C" bool NovaInitializeCullingHooks() {
    const auto result = nova::culling::initialize();
    return result.queryAHooked && result.queryBHooked;
}

extern "C" const char* NovaGetCullingHookStatus() {
    static thread_local std::string text;
    text = nova::culling::status().message;
    return text.c_str();
}
