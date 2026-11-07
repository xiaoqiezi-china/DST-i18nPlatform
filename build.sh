set -euo pipefail

CONFIGURATION="Release"
OUTPUT="dist"
CLEAN=0
PACK=0

usage() {
    cat <<'USAGE'
DST MC I18n Tool 一键发布脚本（git-bash / WSL / Linux）

用法：
  ./build.sh                         发布 Release 到 dist/
  ./build.sh -c Debug -o dist-debug  指定配置与输出目录
  ./build.sh --clean                 发布前清空输出目录
  ./build.sh --pack                  额外打成可分发的 zip（发版用）

产物为 win-x64 自包含程序（exe + 依赖 dll，非单文件、非自解压）。
整个输出目录一起分发，不要只拷 exe。
USAGE
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
        --pack)
            PACK=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "未知参数: $1" >&2
            echo "用法: ./build.sh [--configuration Release] [--output dist] [--clean] [--pack]" >&2
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

if [[ "$PACK" -eq 1 ]]; then
    if ! command -v python >/dev/null 2>&1; then
        echo "没有找到 python，无法打包。请安装 Python 3 或手动打包。" >&2
        exit 1
    fi
    ZIP="$ROOT/DST-MC-I18n-Tool.zip"
    python "$ROOT/tools/pack.py" "$OUT_DIR" "$ZIP"
    echo "  压缩包     : $ZIP"
    echo
fi
