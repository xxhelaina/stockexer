# StockLab Windows x64 免安装版

1. 将 ZIP **完整解压**到一个文件夹，不要直接在压缩包内运行。
2. 双击 `StockLab.exe`。不需要安装 Python，也不需要管理员权限。
3. 保留 `_internal` 文件夹；不要只复制 EXE。

适用范围：Windows 10/11 64位 x64。未在所有 Windows 版本和设备上验证。
不适用于 macOS、Linux 或 Windows ARM 原生运行。

## 数据和设置

- 选股票、时间和周期，点击开始训练后才检索本地行情。
- 本地不足时选择“本地导入”（选择文件夹）或“网上获取”；取消不会联网。
- 下载缓存和日志默认位于 EXE 同级 `data/` 文件夹。
- 如果程序目录不可写，则改用 `%LOCALAPPDATA%/StockLab/`。
- 用户设置保存在 Windows 当前用户的 `YourCompany/StockTrainer` 设置项中。
- 免安装不等于所有设置都随文件夹移动；复制存档和行情可在其他电脑继续训练。
- 软件不附带个人行情、训练存档或账户信息。

在线源仍可能受网络、限流、服务故障和历史范围限制。
打包不保证网络可用，也不会让免费源提供其原本没有的数据。
若需要长期分钟线而数据仅到上一交易日，请按实际数据范围选择结束时间。

## 故障排查

- 检查是否完整解压且 `_internal` 文件夹存在。
- 查看 `data/trainer.log`；只读目录时查看 `%LOCALAPPDATA%/StockLab/trainer.log`。
- 本版本未使用代码签名，Windows SmartScreen 或杀毒软件可能提示未知发布者。
  请核对 GitHub Release 来源和 SHA256；不要为运行程序而关闭安全软件。
- `StockLab.exe --self-test report.json` 执行离线自检并生成 JSON 报告。
- 加上 `--network` 会额外请求 BaoStock 最近完成交易日的5分钟行情；需要网络。

## 授权和源码

StockLab 项目按 GPLv3 发布，见 `LICENSE`。第三方组件保留各自许可证，
见 `licenses/` 和 `THIRD_PARTY_NOTICES.md`。
对应项目源码和 PyQt6 源码通过同一 Release 的源码附件提供。
Qt 使用动态 DLL；可以替换为兼容构建，不限制为调试修改而进行的逆向工程。
Qt 官方对应版本源码及构建信息列于第三方说明中。
