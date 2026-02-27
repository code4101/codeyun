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

*   **`check_yun_server.py`**: 检查服务器上的 Nginx 状态、端口占用情况及关键日志。
*   **`check_nginx_conf.py`**: 验证 Nginx 配置文件及站点设置是否正确。
*   **`fix_nginx_final.py`**: 执行 Nginx 配置修复操作（如路径修正、权限调整、重启服务）。
*   **`fix_yun_server.py`**: 综合修复服务器常见问题。
*   **`build_dsp_static.py`**: 自动化构建 DSP 计算器前端并部署到静态资源目录。
