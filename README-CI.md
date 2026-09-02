# JM漫画导出 App — APK 构建说明（GitHub Actions 一键出包）

本目录是**可直接产出安卓 APK** 的完整工程包。因为本地沙箱环境禁止编译器（clang/gcc）在
工作区写入编译产物（所有 .o/.so 落盘为 0 字节，交叉编译无法完成），所以改为
**GitHub Actions 云端构建**——GitHub 免费 runner 是标准环境，配合 NDK r25b（p4a 官方
经典稳定版），一次构建即可产出 APK，无需任何本地工具链。

## 使用方法（约 10 分钟操作，构建 30~60 分钟）

1. **注册 GitHub 账号**（https://github.com），免费。
2. **新建仓库**：右上角 + → New repository → 填仓库名（如 `jm-export`）→ 公开或私有均可 → Create。
3. **上传本目录全部文件**：把 `jm_export_ci/` 里的所有文件（main.py、jm_export.py、vendor/、
   icon.png、presplash.png、option_android.yml、build_apk_ci.sh、.github/）上传到仓库根目录。
   - 网页方式：仓库页面 → Add file → Upload files → 拖入全部文件 → Commit。
   - 或命令行：`git init && git add . && git commit -m init && git remote add origin <仓库地址> && git push -u origin main`
4. **开启 Actions**：仓库 Settings → Actions → General → 选 "Allow all actions and reusable workflows" → Save。
5. **触发构建**：仓库 Actions 标签页 → 左侧 "构建 JM漫画导出 APK" → Run workflow → 绿色按钮。
6. **等待构建**（首次约 30~60 分钟，日志可实时查看）。
7. **下载 APK**：构建完成后在 Actions 页面该次运行的 "Artifacts" 区，下载 `jm-export-apk`，
   解压即得到 `org.jmexport.app-1.1.0-arm64-v8a-release.apk`，传到手机安装即可。

## App 功能（与 CLI v1.1.0 一致）

- 输入禁漫车号（可多个，逗号分隔）批量下载
- 导出 PDF / APNG / 两者同时
- APNG 支持：封面首帧（自动下载本子封面）、每张图片切换间隔（逐帧/统一）、缩放
- 输出目录：`/storage/emulated/0/Android/data/org.jmexport.app/files/jm_export`
- 传输层：内置纯 Python requests（无 curl_cffi 依赖），首次联网需保持网络通畅

## 注意事项

- **要求 Android 7.0（API 24）及以上**（ndk-api 24）。
- APK 体积约 60~80MB（Kivy + SDL2 + Python 运行时）。
- 禁漫站点域名可能变化，若下载失败，可修改 `option_android.yml` 中的 `client.domain` 后重新构建。
- 国内访问 GitHub 可能较慢，建议使用代理或稍后重试；Actions 执行本身在 GitHub 服务器完成，不受影响。
