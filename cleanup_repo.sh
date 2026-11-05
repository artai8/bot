#!/usr/bin/env sh
# 一键清理仓库根目录下的 5 个打包残留文件
set -eu
rm -f DIFF_FILE MODIFIED_FILE ROLLBACK.sh SOURCE_MANIFEST.sha256 VERIFICATION.txt
echo "已清理根目录残留文件: DIFF_FILE, MODIFIED_FILE, ROLLBACK.sh, SOURCE_MANIFEST.sha256, VERIFICATION.txt"
