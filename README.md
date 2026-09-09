# envshift-runner

跨操作系统执行器(Linux / macOS / Windows)。**本仓库只放执行器,不含任何题目材料**——
题目在运行时从仓库 secret 还原,产物加密后上传。

## 可用的 runner(公开仓,标准 runner 免费无限)

| 标签 | 系统 | 架构 | 说明 |
|---|---|---|---|
| `ubuntu-24.04` | Linux | x86_64 | 与下一行**同内核版本**,只差架构,是最干净的架构对照 |
| `ubuntu-24.04-arm` | Linux | aarch64 | |
| `windows-2022` | Windows Server 2022 | AMD64 | 与下一行架构不同,**OS 版本也不同**,归因需注明 |
| `windows-11-arm` | Windows 11 | ARM64 | |
| `macos-14` | macOS 14 | arm64 | Apple 芯片真机 |

arm 机器**只在公开仓库免费**。larger runner 任何情况下都收费,不要用。
