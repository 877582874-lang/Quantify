# ChatGPT / Codex 远程控制（ECS 47.239.82.63）

状态：**完全可用**（2026-11-25 02:32 验收通过）。最后更新：2026-11-25 03:30 CST（Muse）。

## 架构（一句话版）

Codex 程序（app-server）跑在 Cl 的阿里云 ECS 上，是「手和工地」；GPT 模型在 OpenAI 云端，是「大脑」（调用经新加坡代理出网，见 infrastructure.md）；Cl 的 **iPhone ChatGPT App** 是遥控端（注意：不是桌面端，Cl 没有桌面端）。ECS 关机则远程控制断，App 普通聊天不受影响。

## 安装与登录

- Node v26.13.1（官方 LTS tar 包入 /usr/local，SHA 校验过），npm 11.20.0；`@openai/codex` **0.172.0** 全局安装，`codex` 在 /usr/local/bin。安装报告：服务器 `/opt/okx-quant/diagnostics/CODEX_REMOTE_INSTALL_REPORT_20261125.txt`。
- 登录：设备码流程（auth.openai.com/codex/device）于 2026-11-25 02:16 由 Cl 本人完成（含手机验证）；`codex login status` = Logged in using ChatGPT。凭据 `/root/.codex/auth.json`（600，目录 700）含 access+refresh token，服务器可自续期，无需再验证。**Cl 提醒：其验证手机号即将停用**——已建议其尽快给 ChatGPT 账号加非短信 MFA（验证器/通行密钥）并检查 GitHub 与阿里云的绑定手机；是否已办，**未确认**。
- 服务器侧实测 `codex exec` 通过（02:18）。注意：`codex exec` 必须带 `< /dev/null`，否则等 stdin 挂起。

## 沙箱配置（/root/.codex/config.toml，600）

- `sandbox_mode = "workspace-write"`；`writable_roots = ["/opt/okx-quant"]`；`network_access = true`（2026-11-25 02:47 Cl 批准后开启；沙箱内实测出网 loc=SG，阿里云元数据服务亦可达——注意这只是「从服务器出网」，GPT 没有阿里云账号权限）；`[projects."/opt/okx-quant"] trust_level = "trusted"`。
- App 会话默认工作区是 `/root/Documents/Codex/<日期>/<会话名>` 的草稿目录（非 git 仓库）；靠 writable_roots 才能写 /opt/okx-quant。

## 已验证能力（均有 Muse 独立复核）

1. **项目文件全操作**（2026-11-25 03:00–03:04）：在 /opt/okx-quant/edit-test/ 三个埋暗号文件上完成读原文引用、改、追加、删整树，报告与服务器实况零出入。
2. **Git 全流程**（2026-11-25 03:06–03:11）：克隆 Quantify → 读 5 个埋暗号文件 → 改/追加/删/新建四项变更与任务书逐项吻合（对照组字节级未动）→ 以署名 `ChatGPT <chatgpt@local>` 提交 `4b2a045` 并推送分支 `codex-edit-test-20261125`；Muse 拉取逐行验 diff 通过。测试分支与种子文件已清理（main 提交 09677b4）。
3. **图像生成：不可用**（2026-11-25 03:25 实测）：exec 模式无原生生图工具；其自带 fallback 脚本 `scripts/image_gen.py` 需要 `OPENAI_API_KEY`（API 按量计费，与 ChatGPT 订阅分开），服务器未配置，实测报错停止。如需开通，须 Cl 决定是否创建 API 钥并承担费用。

## 经验教训（血泪版）

- **钥匙格式**：Ubuntu 自带 OpenSSH 8.9 的 ssh 客户端读不了 PKCS#8 格式的 Ed25519 私钥（报 invalid format）；GPT 的 App 通道用别的 SSH 库所以没事，沙箱内原生 ssh/git 须用转换件 `/root/.ssh/chatgpt_openssh_key`。GitHub 部署钥现行只有 QY9hO2ZN 一把（见 infrastructure.md），旧钥 Irc1a5 已被换下。
- **pkill 陷阱**：`pkill -f` / `pgrep -f` 的匹配串若含 "codex login" 等词，会连 Muse 自己的 SSH 命令行一起匹配误杀。只能用 `pgrep -x codex` 枚举 PID、查 /proc/<pid>/cmdline、按确切 PID 动手。
- **沙箱策略在会话创建时冻结**：旧会话是只读策略时，改 config.toml 不生效，必须开新会话；且**严禁**在 Codex 沙箱会话内再嵌套跑 `codex exec`（内层写不了只读的 ~/.codex，报 EROFS/os error 30）——CLI 验收一律在普通 SSH shell 里做。
- **git 信任**：项目目录须先 git init + trust 配置，否则 `codex exec` 以「directory not trusted」退出码 1 失败（与登录无关）。/opt/okx-quant 已 init（无提交）。
- **GPT 甩锅模式**：当晚它多次把自身姿势问题（没装 Codex、旧会话只读、嵌套执行、无钥匙环境）报成服务器故障。**裁决顺序：先查服务器 auth.log 与实况，再下结论。**
- **定时任务**（2026-11-25 03:10 与 Cl 议定，尚未搭建）：技术上可用 systemd timer + 包装脚本（注入代理变量）定时拉起 `codex exec`。Cl 尚未指定任务内容；原则：先只读报告类试点，验收数轮后再议写入类；每次运行消耗其 ChatGPT 额度。

## 补充（2026-11-25 10:41）：MULTI-AGENT-002 调用链正式验证

Cl 下达三 AI 协作体系任务书（ChatGPT=总体技术负责人，Muse=运维+Codex 调度者，Codex=主要开发者）。阶段 1–3 已实测完成：在独立测试目录 /opt/ai-coordinator/chain-test 内以 SPEC+测试+git 仓库为测试台，任务书 13 项逐项通过——含注入 bug 后命其返工修复（diff 与 Muse 独立复核逐字一致）、五次连续任务、以及长任务中途按进程组终止后用 `codex exec resume --last` 恢复同一会话并完成（退出码 0）。调用机制选定 **CLI `codex exec`（+ resume）** 为协调器 v1 的调度通道；app-server 保留为 iPhone 交互通道。报告：Quantify 仓库 reports/2026-11-25/multi-agent-002-stage1-3-report.txt。阶段 4–10 待 Cl 批准。—— Muse，2026-11-25

—— Muse，2026-11-25
