#include "signature_scanner.h"

#include <sstream>
#include <string>

namespace nova::sig {
BytePattern parse(const std::string& text) {
    BytePattern pattern;
    std::istringstream input(text);
    std::string token;
    while (input >> token) {
        if (token == "?" || token == "??") {
            pattern.bytes.push_back(-1);
            continue;
        }
        try {
            size_t consumed = 0;
            const int value = std::stoi(token, &consumed, 16);
            if (consumed != token.size() || value < 0 || value > 255) {
                pattern.bytes.clear();
                return pattern;
            }
            pattern.bytes.push_back(value);
        } catch (...) {
            pattern.bytes.clear();
            return pattern;
        }
    }
    return pattern;
}

std::vector<uintptr_t> findAll(const uint8_t* begin, size_t size,
                               const BytePattern& pattern) {
    std::vector<uintptr_t> matches;
    if (begin == nullptr || pattern.bytes.empty() || size < pattern.bytes.size()) {
        return matches;
    }
    const size_t last = size - pattern.bytes.size();
    for (size_t offset = 0; offset <= last; ++offset) {
        bool match = true;
        for (size_t i = 0; i < pattern.bytes.size(); ++i) {
            if (pattern.bytes[i] >= 0 &&
                begin[offset + i] != static_cast<uint8_t>(pattern.bytes[i])) {
                match = false;
                break;
            }
        }
        if (match) matches.push_back(reinterpret_cast<uintptr_t>(begin + offset));
    }
    return matches;
}
}
