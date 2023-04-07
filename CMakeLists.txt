cmake_minimum_required(VERSION 3.14)

project(wyag++)

include(FetchContent)
FetchContent_Declare(
  googletest
  GIT_REPOSITORY https://github.com/google/googletest.git
  GIT_TAG main
)

# For Windows: Prevent overriding the parent project's compiler/linker settings
set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)

# Ensure content is actually available
FetchContent_MakeAvailable(googletest)

add_executable(example example.cpp)

# Link against gtest_main to use the default entry point
target_link_libraries(example gtest_main)
#add_test(NAME example_test COMMAND example)

