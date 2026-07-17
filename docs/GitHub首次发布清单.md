# GitHub 首次发布清单

## 1. 创建空仓库

推荐仓库名：`small-enterprise-accounting-assistant`

推荐描述：

> 面向一人公司和小微企业的本地记账与报税准备工具：票据 OCR、三层语库语义科目对齐、月结、税务测算和 Excel 导出。源码开放，采用非商业互惠许可。

创建仓库时不要勾选自动生成 README、`.gitignore` 或 License。本项目使用自定义许可证，
GitHub License 选择 **None**，不要误选 MIT、AGPL 或 CC 协议。

推荐 Topics：

`accounting`、`bookkeeping`、`small-business`、`tax-preparation`、`ocr`、
`qwen`、`llama-cpp`、`sqlite`、`excel`、`python`、`windows`、`china`

## 2. 本地发布前检查

在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_public_release.ps1 -RunTests
git status --short
```

检查输出中不得出现模型、运行时、账套、备份、安装包、本机路径、账号凭据和专利通知书
原 PDF。确认 `git status` 中只有计划公开的源码、合成测试数据和文档。

## 3. 首次提交与推送

将下面的远程地址替换为手工创建的仓库地址：

```powershell
git add .
git commit -s -m "chore: prepare initial source release v1.7.0"
git remote add origin https://github.com/<owner>/small-enterprise-accounting-assistant.git
git push -u origin main
```

如果尚未配置 Git 身份，先设置您希望公开显示的 `user.name` 和 `user.email`。公开邮箱可使用
GitHub 提供的 `noreply` 地址。

## 4. GitHub 仓库设置

- 在 About 中填写推荐描述和 Topics。
- 启用 Issues；需要讨论区时再启用 Discussions。
- 在 Security 中启用 Private vulnerability reporting。
- 将默认分支设为 `main`，并为 `main` 开启拉取请求和状态检查保护。
- 不配置 Funding，不发布付费支持入口，避免与非商业许可定位冲突。
- 在仓库首页确认 GitHub 没有把项目误标为 MIT、AGPL 或其他标准许可证。

## 5. 发布版本

源码标签建议使用 `v1.7.0`。模型、OCR 权重、运行时和安装程序不进入 Git 历史。
如需在 GitHub Releases 发布安装包，必须先逐项核验实际捆绑版本的再分发权，并在安装包
内保留所有第三方许可证和通知；同时发布 SHA-256 校验值。

不要上传原始专利通知书。公开页面只引用 `PATENTS.md` 中已核验、已脱敏的信息。

