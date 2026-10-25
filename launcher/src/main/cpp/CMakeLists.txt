cmake_minimum_required(VERSION 3.22.1)
project(nova_core LANGUAGES C CXX)

include(FetchContent)
set(DOBBY_GENERATE_SHARED OFF CACHE BOOL "" FORCE)
set(DOBBY_DEBUG OFF CACHE BOOL "" FORCE)
set(Plugin.SymbolResolver ON CACHE BOOL "" FORCE)
FetchContent_Declare(
    Dobby
    GIT_REPOSITORY https://github.com/jmpews/Dobby.git
    GIT_TAG master
    GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(Dobby)

add_library(nova_core SHARED
    nova_core.cpp
    hook_engine.cpp
    culling_hooks.cpp
    signature_scanner.cpp
)
target_compile_features(nova_core PRIVATE cxx_std_17)
target_compile_options(nova_core PRIVATE -Wall -Wextra)
target_include_directories(nova_core PRIVATE
    ${dobby_SOURCE_DIR}/include
    ${dobby_SOURCE_DIR}/source
)
find_library(log-lib log)
find_library(dl-lib dl)
find_library(egl-lib EGL)
target_link_libraries(nova_core PRIVATE dobby ${log-lib} ${dl-lib} ${egl-lib})
