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
    脚本会自动从项目根目录读取 `.env` 文件。请确保该文件存在并包含正确的 SSH 连接信息（请参考根目录的 `.env.example`）：
    ```ini
    SSH_HOST=your_server_ip
    SSH_USER=ubuntu
    SSH_PASSWORD=your_password
    ```

    > **注意**：不要将 `.env` 文件提交到版本控制系统。

## 脚本说明

*   **`build_dsp_static.py`**: 自动化构建 DSP 计算器前端并部署到静态资源目录。
*   *(其他运维脚本已归档或移除，请使用 `skills/yun-server` 技能或手动维护)*
