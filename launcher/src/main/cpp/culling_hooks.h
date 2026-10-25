#pragma once

#include <cstdint>
#include <string>

namespace nova::culling {
struct Status {
    bool gameLibraryLoaded;
    bool queryAHooked;
    bool queryBHooked;
    std::string message;
};

// Scans the executable PT_LOAD segments of libminecraftpe.so, refuses ambiguous
// signatures, and installs pass-through observation hooks for culler slots 8/11.
Status initialize();
Status status();
uint64_t queryACalls();
uint64_t queryBCalls();
}

extern "C" bool NovaInitializeCullingHooks();
extern "C" const char* NovaGetCullingHookStatus();
