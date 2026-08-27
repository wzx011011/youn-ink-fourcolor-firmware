# compat/

工具链兼容垫片目录。

`cxx_math_compat.h`：由根 `CMakeLists.txt` 通过
`add_compile_options("$<$<COMPILE_LANGUAGE:CXX>:-include ...cxx_math_compat.h>")`
强制注入到每个 C++ 编译单元。作用：ESP-IDF 工具链的 libstdc++ 缺少部分
`std::sqrt/std::sin` 等重载声明，此头把 newlib 的 C 数学函数引入 `std::`
命名空间作兜底。无逻辑、无可配置项。
