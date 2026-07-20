# CodeYun 运维脚本

这些脚本用于辅助 CodeYun 项目的服务器维护和部署检查。

## 准备工作

1.  **安装依赖**：
    脚本依赖于 `paramiko` 和 `python-dotenv`。
    如果在项目根目录使用 `uv` 管理依赖，这些依赖已包含在 `dev` 组中，运行：
    ```bash
    uv sync
    ```
    或者手动安装：
    ```bash
    pip install paramiko python-dotenv
    ```

2.  **环境变量配置**：
    脚本会自动从项目根目录读取 `.env` 文件。请确保该文件存在并包含正确的服务器连接信息（请参考根目录的 `.env.example`）：
    ```ini
    YUN_SERVER_HOST=your_server_ip
    YUN_SERVER_USER=deploy
    YUN_SERVER_PASS=your_password
    YUN_SERVER_PORT=22
    # Windows 上启用 TUN/虚拟网卡时，可指定真实出口网卡，避免 SSH 被代理路由接管。
    # 通过 Get-NetRoute -DestinationPrefix '0.0.0.0/0' 查看 InterfaceIndex。
    YUN_SERVER_DIRECT_INTERFACE_INDEX=8
    ```

    > **注意**：不要将 `.env` 文件提交到版本控制系统。
    > `YUN_SERVER_DIRECT_INTERFACE_INDEX` 仅影响公网前端发布器的 SSH/SFTP 连接；不配置时保持系统默认路由。

## 脚本说明

*   **`build_dsp_static.py`**: 自动化构建 DSP 计算器前端并部署到静态资源目录。脚本会把源码指纹写到 `frontend/.codeyun-state/`，重复执行时若源码未变则直接跳过，适合日常手动保底同步。
*   **`check_prod.py`**: 本地生产式检查。会执行 Linux 大小写兼容性预检查、前端类型检查、`vite build`、后端 production smoke test、前端 preview smoke test。
*   *(其他运维脚本已归档或移除，请使用 `skills/yun-server` 技能或手动维护)*
