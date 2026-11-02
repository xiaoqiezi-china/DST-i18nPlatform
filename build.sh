#!/usr/bin/env bash
# DST MC I18n Tool 一键发布脚本（git-bash / WSL / Linux 上的 bash）。
#
# 调用 dotnet publish 生成 win-x64 自包含程序（普通多文件发布，不做单文件/自解压），产物输出到 dist/。
# 前端资源（wwwroot）在构建时作为 EmbeddedResource 打进程序集，因此 dist 里不需要带 wwwroot。
#
# 用法：
#   ./build.sh
#   ./build.sh --configuration Debug --output dist-debug
#   ./build.sh --clean
#
# 注意：目标运行时固定为 win-x64，因此即使脚本跑在 Linux 上，产物也是 Windows exe。

set -euo pipefail

CONFIGURATION="Release"
OUTPUT="dist"
CLEAN=0

usage() {
    sed -n '3,13p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -c|--configuration)
            CONFIGURATION="${2:?--configuration 需要参数}"
            shift 2
            ;;
        -o|--output)
            OUTPUT="${2:?--output 需要参数}"
            shift 2
            ;;
        --clean)
            CLEAN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "未知参数: $1" >&2
            echo "用法: ./build.sh [--configuration Release] [--output dist] [--clean]" >&2
            exit 2
            ;;
    esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$ROOT/DstMcI18nTool.csproj"

if [[ ! -f "$PROJECT" ]]; then
    echo "找不到项目文件: $PROJECT" >&2
    exit 1
fi

if ! command -v dotnet >/dev/null 2>&1; then
    echo "没有找到 dotnet 命令，请先安装 .NET SDK 10.0 或更高版本：" >&2
    echo "  https://dotnet.microsoft.com/download" >&2
    exit 1
fi

# 相对路径按仓库根目录解析（与 build.ps1 一致）
case "$OUTPUT" in
    /*|[A-Za-z]:*) OUT_DIR="$OUTPUT" ;;
    *) OUT_DIR="$ROOT/$OUTPUT" ;;
esac

SDK_VERSION="$(dotnet --version | tr -d '\r')"
echo
echo "==> 使用 .NET SDK $SDK_VERSION 发布 $CONFIGURATION / win-x64"

if [[ "$CLEAN" -eq 1 && -d "$OUT_DIR" ]]; then
    echo "==> 清空输出目录 $OUT_DIR"
    rm -rf "$OUT_DIR"
fi

echo "==> dotnet publish -> $OUT_DIR"
dotnet publish "$PROJECT" \
    -c "$CONFIGURATION" \
    -r win-x64 \
    -o "$OUT_DIR" \
    -p:SelfContained=true \
    -p:PublishSingleFile=false \
    -p:IncludeNativeLibrariesForSelfExtract=false \
    --nologo

EXE="$OUT_DIR/DST-MC-I18n-Tool.exe"
if [[ ! -f "$EXE" ]]; then
    echo "发布完成但没有找到 $EXE，请检查 AssemblyName 配置。" >&2
    exit 1
fi

SIZE="$(du -sh "$OUT_DIR" | cut -f1)"
COUNT="$(ls -1 "$OUT_DIR" | wc -l | tr -d ' ')"
echo
echo "==> 发布完成"
echo "  可执行文件 : $EXE"
echo "  输出目录   : $OUT_DIR（$COUNT 个文件，共 $SIZE）"
echo "  注意       : 整个目录一起分发，不要只拷 exe"
echo
echo "运行方式："
echo "  \"$EXE\" --path \"<整合包目录>\""
echo "  \"$EXE\" --help"
echo
