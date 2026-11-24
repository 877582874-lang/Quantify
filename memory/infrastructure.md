# 基础设施事实（经核实）

最后更新：2026-11-25 03:30 CST（Muse）。

## 主服务器（量化平台 + AI 工人驻地）

- 阿里云香港 ECS，购于 2026-11-24。公网 IP **47.239.82.63**，2 vCPU / 4 GB（实测 3.5 GiB）/ 40 GB 盘，Ubuntu 22.04.5 LTS，主机名 `iZj6cdxsi6dmudpx5kbey3Z`，时区 Asia/Shanghai，NTP 已同步。
- SSH：仅密钥登录（PasswordAuthentication no，PermitRootLogin yes，root 无口令）。主机 ED25519 指纹 `SHA256:ABzxHTx916yoMgLH1ZYm3exVev2+sREJDJcfYMG+QZw`（购机至今未变，三路核对一致）。安全组开放 22/80/443。
- 基础栈（2026-11-24 建，验收通过）：4 GiB swap（fstab，swappiness 10）；Docker 29.10.0 + Compose v5.8.0（0 容器）；Python 3.12.16（deadsnakes，系统 3.10 未动）；Caddy v2.11.9 监听 80（默认页，无域名）。
- 常驻进程参考占用（2026-11-25 03:20 实测）：全机已用 657 MB / 3623 MB；Codex 约 220 MB，采集器约 178 MB，mihomo 约 53 MB。
- **VM 更换注意**：运行环境更换会清掉 `/etc/systemd/system`（mihomo 等自建 unit 需按本库记录重建）；`~` 与服务器磁盘不受影响。

## 密钥清单（仅指纹；私钥本体永不入库）

| 钥匙 | 指纹 | 用途 / 存放 |
|---|---|---|
| Muse 的 ECS 登录钥 | `SHA256:FqFI5sjQrEXBCxAgalnLOfuFDQTOS+BTQVzA/igUzAQ`（comment: hatch） | Muse 的 `~/.ssh/id_ed25519`；在 ECS root authorized_keys 中 |
| Cl 本人 ECS 登录钥 | `SHA256:1aHWgVJPRvGxLxCT+4vzJMj/gCEoqbv8m1KpUpUmTTs`（cl-aliyun-ecs） | 私钥已交付 Cl 本人保管；在 ECS root authorized_keys 中 |
| ChatGPT 的钥（现行） | `SHA256:QY9hO2ZNXM6U3zbLF4YJeMHy9825tAOb+/4Cg6yX9/E`（chatgpt-quantify-write） | 同一把钥两用：Quantify 仓库部署钥（可写）+ ECS root 登录钥（2026-11-25 00:29 按 Cl 明确指令加入 authorized_keys）。原件为 PKCS#8 格式，存服务器 `/opt/github-keys/quantify_deploy_pkcs8.pem`；其 OpenSSH 格式转换件在服务器 `/root/.ssh/chatgpt_openssh_key`（同指纹） |
| ChatGPT 旧部署钥（已撤销） | `SHA256:Irc1a5bx0AL43fL4SUb56bGAhETw/WmivA4yQIr17K4` | 服务器上文件仍在 `/opt/github-keys/quantify_deploy`，但 GitHub 端已被换下，实测认证被拒——**引用部署钥时勿再使用此把** |
| Muse 的仓库部署钥 | comment: muse-quantify-archive | Muse 的 `~/.ssh/quantify_archive_ed25519`；Quantify 部署钥（可写），已验通 |

- ECS root `authorized_keys` 现共 3 把：hatch（Muse）、cl-aliyun-ecs（Cl）、chatgpt-quantify-write（ChatGPT）。2026-11-25 02:52 复核一致。
- **撤销杠杆**：删 authorized_keys 中对应行 = 立即断其 SSH；删服务器 `/root/.ssh/chatgpt_openssh_key` 只断 ChatGPT 沙箱内的原生 ssh 路径（App 通道不受影响）；删 `/root/.codex/auth.json` = 断 Codex 远程控制。

## 代理（Codex 出网用）

- 背景：OpenAI 对香港出口 403（地区不支持），须借道新加坡。Cl 的星际订阅（到期 2027-01-20）配置只存服务器（`/root/.config/proxy/sub.yaml`，600），**订阅链接永不入库**。
- mihomo v1.19.34 以 systemd 服务运行，配置 `/root/.config/mihomo/config.yaml`（600）；**强制仅监听 127.0.0.1**（订阅默认 allow-lan 已纠正）；mixed-port 127.0.0.1:7890，控制器 127.0.0.1:9090；主组「星际穿越」钉死节点「新加坡 星际隧道 02（稳定版）」（自动选择曾选到香港节点，会绕回封锁）。
- 代理环境变量注入点：`/root/.ssh/environment`（sshd 已开 PermitUserEnvironment）+ `/etc/profile.d/codex-proxy.sh`；NO_PROXY 含 localhost/127.0.0.1/::1。
- 实测：直连 api.openai.com = 403，经代理 = 401（可达），出口 loc=SG。

—— Muse，2026-11-25
