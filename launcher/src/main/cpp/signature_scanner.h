#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace nova::sig {
struct BytePattern {
    std::vector<int> bytes; // -1 is wildcard
};
BytePattern parse(const std::string& text);
std::vector<uintptr_t> findAll(const uint8_t* begin, size_t size,
                               const BytePattern& pattern);
}
