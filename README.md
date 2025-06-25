# VEMU Scheduling Service

## 业务定位 (Business Positioning)

本服务在 VEMU 系统中扮演 **"运筹与优化算法引擎"** 的角色。

## 核心职责 (Core Responsibility)

其核心职责是解决一个经典的调度问题：如何将一个已知的任务图 (DAG) 高效地映射到一组已知的异构硬件资源上，其目标是获得最短的整体完成时间。目前，该服务实现了 HEFT (Heterogeneous Earliest Finish Time) 算法来达成此目标。

### 业务边缘 (Business Boundary)

- **输入 (Input)**:
    1.  任务依赖图 (DAG)，由 `vemu-dsl-service` 生成。
    2.  硬件资源描述 (Hardware Resource Description)。
- **输出 (Output)**: 一个具体的、带有时间戳的执行时间表 (Schedule)。

## 分析 (Analysis)

该服务体现了技术选型的灵活性和优势：
- **API 包装层**: 使用 Python 的 FastAPI 实现，开发效率高，易于集成。
- **核心算法层**: 使用 C++ 实现 HEFT 算法，保证了计算密集型任务的性能。

它完全不关心 DSL 是如何编写的，也不关心底层的 RISC-V 指令如何执行，其业务边界非常清晰，只专注于"调度"这一特定问题域。 