#pragma once
#include <cstddef>
#include <cstdint>

namespace nova::hooks {
struct HookResult {
    bool ok;
    const char* message;
};

bool isMinecraftLoadedInThisProcess();
HookResult installFrameHook();
uint64_t frameCount();
}
extern "C" bool NovaInitializeInGameProcess();
extern "C" bool NovaIsMinecraftProcess();
