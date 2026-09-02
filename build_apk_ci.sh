#!/usr/bin/env bash
# JM 漫画导出 App APK 构建脚本（GitHub Actions 专用）
# 使用 NDK r25b（p4a 官方经典稳定版本，规避 NDK r28 的全部交叉编译兼容问题）
set -e

export ANDROIDSDK="${ANDROID_HOME:-$ANDROID_SDK_ROOT}"
export ANDROIDNDK="$ANDROIDSDK/ndk/25.2.9519653"
export ANDROIDAPI=33
export ANDROIDNDKVER=r25b

echo "ANDROIDSDK=$ANDROIDSDK"
echo "ANDROIDNDK=$ANDROIDNDK"
test -d "$ANDROIDNDK" || { echo "NDK 未找到: $ANDROIDNDK"; exit 1; }

# python3 锁定 3.11.6：kivy 2.3.1 官方支持范围（3.7~3.13），p4a 交叉编译成熟
# 不引入 jmcomic（vendor/ 内置纯 Python 补丁版，避免 curl_cffi 原生库）
# 不引入 pyyaml（jmcomic 仅 zip 插件迁移日志用到，非运行时必需）
p4a apk \
  --private . \
  --package org.jmexport.app \
  --name "JM漫画导出" \
  --version 1.1.0 \
  --bootstrap sdl2 \
  --requirements "python3==3.11.6,kivy==2.3.1,pillow,pycryptodome,requests,img2pdf,pyjnius" \
  --arch arm64-v8a \
  --permission INTERNET \
  --icon icon.png \
  --presplash presplash.png \
  --color always

echo "=== 构建完成 ==="
ls -la bin/*.apk 2>/dev/null || ls -la dists/*/bin/*.apk 2>/dev/null
