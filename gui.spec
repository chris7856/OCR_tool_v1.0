# -*- mode: python ; coding: utf-8 -*-

import os
import sys
import site
import sysconfig
from pathlib import Path

from PyInstaller.utils.hooks import (
    copy_metadata,
    collect_submodules,
    collect_data_files,
    collect_dynamic_libs,
)

block_cipher = None


# ================= 自动获取当前环境的 site-packages 路径 =================
# 目的：
# 1. 尽量自动适配不同机器/环境，避免把 site-packages 路径写死
# 2. 后面需要从这里定位 paddle/cv2/pypdfium2_raw 等包的文件
def find_site_packages():
    candidates = []

    # 方式1：通过标准库 site 获取
    try:
        for p in site.getsitepackages():
            if p and os.path.isdir(p):
                candidates.append(os.path.abspath(p))
    except Exception:
        pass

    # 方式2：通过 sysconfig 获取 purelib / platlib
    try:
        paths = sysconfig.get_paths()
        for key in ("purelib", "platlib"):
            p = paths.get(key)
            if p and os.path.isdir(p):
                candidates.append(os.path.abspath(p))
    except Exception:
        pass

    # 方式3：根据当前 Python/conda 环境的前缀路径拼接常见目录
    version_tag = f"python{sys.version_info.major}.{sys.version_info.minor}"
    for prefix in [sys.prefix, sys.exec_prefix]:
        if not prefix:
            continue

        for p in [
            os.path.join(prefix, "Lib", "site-packages"),               # Windows
            os.path.join(prefix, "lib", version_tag, "site-packages"),  # Linux/macOS
            os.path.join(prefix, "lib", "site-packages"),
        ]:
            if os.path.isdir(p):
                candidates.append(os.path.abspath(p))

    # 去重
    uniq = []
    seen = set()
    for p in candidates:
        if p not in seen:
            seen.add(p)
            uniq.append(p)

    # 优先返回真正的 site-packages / dist-packages
    for p in uniq:
        low = p.lower().replace("/", "\\")
        if low.endswith("\\site-packages") or low.endswith("\\dist-packages"):
            return p

    # 兜底：如果目录里含有 paddle，也认为它是目标 site-packages
    for p in uniq:
        if os.path.isdir(os.path.join(p, "paddle")):
            return p

    raise RuntimeError(f"未能自动找到当前环境的 site-packages 目录。候选路径: {uniq}")


SITE = find_site_packages()

# 常用第三方库目录
PADDLE_LIBS = os.path.join(SITE, "paddle", "libs")
PADDLE_BASE = os.path.join(SITE, "paddle", "base")
CV2_DIR = os.path.join(SITE, "cv2")
PYPDFIUM_RAW = os.path.join(SITE, "pypdfium2_raw")


# ================= 辅助函数：显式收集 .dist-info =================
# 目的：
# 某些库（如 paddlex）会通过 importlib.metadata 检查依赖/extra 信息。
# 在 PyInstaller 打包环境中，如果缺少 dist-info，可能会误判“依赖未安装”。
# 所以这里主动把关键包的 .dist-info 一并打包进去。
def collect_dist_info_dirs(pkg_names):
    datas = []
    roots = []

    try:
        for p in site.getsitepackages():
            if p and os.path.isdir(p):
                roots.append(Path(p))
    except Exception:
        pass

    try:
        p = site.getusersitepackages()
        if p and os.path.isdir(p):
            roots.append(Path(p))
    except Exception:
        pass

    try:
        paths = sysconfig.get_paths()
        for key in ("purelib", "platlib"):
            p = paths.get(key)
            if p and os.path.isdir(p):
                roots.append(Path(p))
    except Exception:
        pass

    # roots 去重
    uniq_roots = []
    seen_roots = set()
    for r in roots:
        try:
            rr = str(r.resolve())
        except Exception:
            rr = str(r)
        if rr not in seen_roots:
            seen_roots.add(rr)
            uniq_roots.append(r)

    # 扫描目标包对应的 .dist-info 目录
    seen = set()
    for root in uniq_roots:
        for pkg in pkg_names:
            patterns = [
                f"{pkg}-*.dist-info",
                f"{pkg.replace('-', '_')}-*.dist-info",
                f"{pkg.replace('_', '-')}-*.dist-info",
            ]
            for pat in patterns:
                for dist in root.glob(pat):
                    if dist.is_dir():
                        try:
                            key = str(dist.resolve())
                        except Exception:
                            key = str(dist)
                        if key not in seen:
                            seen.add(key)
                            # 目标路径写 "."，在 one-folder 下通常会被整理到运行目录/_internal
                            datas.append((str(dist), "."))

    return datas


# ================= 在 spec 内自动生成 runtime hook =================
# 目的：
# 1. 启动 exe 时，把 _internal 加进 sys.path，方便 importlib.metadata 找到 dist-info
# 2. 对 paddlex 的 OCR extra 检查做运行时补丁，避免 frozen 环境误判依赖缺失
RUNTIME_HOOK = "rthook_add_internal_path_and_patch_paddlex.py"
with open(RUNTIME_HOOK, "w", encoding="utf-8") as f:
    f.write(
        "import os\n"
        "import sys\n"
        "\n"
        "# 1) 把 _internal 加入 sys.path，提升 frozen 环境下 metadata 查找成功率\n"
        "if getattr(sys, 'frozen', False):\n"
        "    exe_dir = os.path.dirname(sys.executable)\n"
        "    internal_dir = os.path.join(exe_dir, '_internal')\n"
        "    if os.path.isdir(internal_dir) and internal_dir not in sys.path:\n"
        "        sys.path.insert(0, internal_dir)\n"
        "\n"
        "# 2) 对 paddlex 的 OCR extra 检查做兼容补丁\n"
        "#    原因：exe 环境里即使依赖已打包，paddlex 仍可能误判 ocr extra 不可用\n"
        "try:\n"
        "    import paddlex.utils.deps as _deps\n"
        "\n"
        "    _orig_require_extra = _deps.require_extra\n"
        "    _orig_is_extra_available = _deps.is_extra_available\n"
        "\n"
        "    def _patched_is_extra_available(extra):\n"
        "        if extra == 'ocr':\n"
        "            try:\n"
        "                import importlib.util as _u\n"
        "                needed = [\n"
        "                    'paddle',\n"
        "                    'paddleocr',\n"
        "                    'tokenizers',\n"
        "                    'sentencepiece',\n"
        "                    'safetensors',\n"
        "                    'pypdfium2',\n"
        "                ]\n"
        "                if all(_u.find_spec(x) is not None for x in needed):\n"
        "                    return True\n"
        "            except Exception:\n"
        "                return True\n"
        "            return True\n"
        "        return _orig_is_extra_available(extra)\n"
        "\n"
        "    def _patched_require_extra(extra, obj_name=None, alt=None):\n"
        "        if extra == 'ocr':\n"
        "            return\n"
        "        return _orig_require_extra(extra, obj_name=obj_name, alt=alt)\n"
        "\n"
        "    _deps.is_extra_available = _patched_is_extra_available\n"
        "    _deps.require_extra = _patched_require_extra\n"
        "\n"
        "except Exception:\n"
        "    pass\n"
    )


# ================= datas：非 .py 资源文件 =================
datas = []

# ---- 复制关键包的 metadata ----
# copy_metadata 会把包的元信息带进去，供 importlib.metadata 等机制使用
meta_pkgs = [
    "paddleocr",
    "paddlepaddle",
    "paddlex",
    "numpy",
    "safetensors",
    "sentencepiece",
    "Jinja2",
    "MarkupSafe",
    "opencv-contrib-python",
    "pypdfium2",
    "tokenizers",
    "huggingface_hub",
    "packaging",
    "filelock",
    "regex",
    "tqdm",
    "openpyxl",
    "beautifulsoup4",
    "et_xmlfile",
]

for pkg in meta_pkgs:
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

# 某些环境里会有 lazy_paddle，存在的话也一起带上
try:
    datas += copy_metadata("lazy_paddle")
except Exception:
    pass

# ---- 显式补充 dist-info 目录 ----
# 这是对 copy_metadata 的补强，尽量避免 frozen 环境下 metadata 丢失
datas += collect_dist_info_dirs([
    "paddlex",
    "paddleocr",
    "paddlepaddle",
    "numpy",
    "safetensors",
    "sentencepiece",
    "tokenizers",
    "pypdfium2",
    "packaging",
    "filelock",
    "regex",
    "tqdm",
    "openpyxl",
    "beautifulsoup4",
    "et_xmlfile",
    "Jinja2",
    "MarkupSafe",
    "huggingface_hub",
])

# ---- 收集包内资源文件 ----
# 例如配置文件、模板、字典、模型说明等非 Python 文件
for pkg in [
    "paddle",
    "paddleocr",
    "paddlex",
    "cv2",
    "pypdfium2",
    "pypdfium2_raw",
    "sentencepiece",
    "tokenizers",
    "jinja2",
    "markupsafe",
    "openpyxl",
    "bs4",
    "shapely",
]:
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

# ---- 项目自身资源目录 ----
# 如果这些目录存在，也一起打进产物中
for name in ["model_file", "test_img", "output"]:
    p = os.path.join(".", name)
    if os.path.exists(p):
        datas.append((p, name))

# datas 去重，避免重复打包同一路径
_unique_datas = []
_seen_data = set()
for item in datas:
    key = (os.path.normpath(item[0]), item[1])
    if key not in _seen_data:
        _seen_data.add(key)
        _unique_datas.append(item)
datas = _unique_datas


# ================= hiddenimports：隐藏导入模块 =================
# 这些模块可能通过动态导入方式加载，PyInstaller 静态分析不一定能发现，
# 所以需要手工声明。
hiddenimports = [
    # 主程序 / 子进程入口
    "cpu_infer",

    # GUI
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",

    # paddle 主体
    "paddle",
    "paddle.base",
    "paddle.base.core",
    "paddle.fluid",
    "paddle.utils",
    "paddle.nn",
    "paddle.nn.functional",
    "paddle.optimizer",
    "paddle.io",
    "paddle.vision",
    "paddle.vision.transforms",

    # paddleocr
    "paddleocr",
    "paddleocr.paddleocr",

    # paddlex
    "paddlex",
    "paddlex.inference",
    "paddlex.inference.pipelines",
    "paddlex.inference.pipelines.doc_preprocessor",
    "paddlex.inference.pipelines.ocr",
    "paddlex.inference.models",
    "paddlex.inference.components",
    "paddlex.repo_apis",
    "paddlex.utils",
    "paddlex.utils.deps",

    # cv2 / numpy
    "cv2",
    "numpy",

    # PIL
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",

    # tokenizer / transformer 相关
    "tokenizers",
    "sentencepiece",
    "huggingface_hub",
    "safetensors",

    # 科学计算
    "einops",
    "scipy",
    "scipy.special",
    "scipy.ndimage",

    # sklearn
    "sklearn",
    "sklearn.utils",
    "joblib",

    # shapely
    "shapely",
    "shapely.geometry",

    # Excel / HTML
    "openpyxl",
    "openpyxl.workbook",
    "openpyxl.worksheet",
    "openpyxl.styles",
    "openpyxl.cell",
    "openpyxl.writer",
    "openpyxl.reader",
    "et_xmlfile",
    "bs4",
    "bs4.builder",

    # 通用依赖
    "pyclipper",
    "lxml",
    "lxml.etree",
    "pypdfium2",
    "pypdfium2_raw",
    "yaml",
    "pyyaml",
    "tqdm",
    "regex",
    "filelock",
    "packaging",
    "importlib_metadata",
    "importlib.metadata",
    "ruamel.yaml",
    "ruamel.yaml.comments",
    "colorlog",
    "prettytable",
    "ujson",
    "psutil",
    "jinja2",
    "jinja2.sandbox",
    "jinja2.environment",
    "jinja2.runtime",
    "jinja2.utils",
    "jinja2.filters",
    "jinja2.tests",
    "jinja2.lexer",
    "jinja2.parser",
    "jinja2.compiler",
    "jinja2.optimizer",
    "jinja2.ext",
    "jinja2.defaults",
    "jinja2.exceptions",
    "jinja2.loaders",
    "markupsafe",
]

# 自动递归收集常见动态导入模块，减少漏包风险
for pkg in [
    "PySide6",
    "paddle",
    "paddleocr",
    "paddlex",
    "safetensors",
    "jinja2",
    "sentencepiece",
    "tokenizers",
    "pypdfium2",
    "pypdfium2_raw",
    "shapely",
    "openpyxl",
    "bs4",
]:
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

# 去重
hiddenimports = sorted(set(hiddenimports))


# ================= binaries：原生库 / DLL / pyd =================
binaries = []

# 手工补充关键二进制文件
# 原因：这类文件经常是运行时真正会报错的点，显式带上更稳
manual_binaries = [
    # Paddle 核心
    (os.path.join(PADDLE_BASE, "libpaddle.pyd"), "paddle/base"),

    # Paddle 依赖 DLL
    (os.path.join(PADDLE_LIBS, "common.dll"), "paddle/libs"),
    (os.path.join(PADDLE_LIBS, "libblas.dll"), "paddle/libs"),
    (os.path.join(PADDLE_LIBS, "libgcc_s_seh-1.dll"), "paddle/libs"),
    (os.path.join(PADDLE_LIBS, "libgfortran-3.dll"), "paddle/libs"),
    (os.path.join(PADDLE_LIBS, "libiomp5md.dll"), "paddle/libs"),
    (os.path.join(PADDLE_LIBS, "liblapack.dll"), "paddle/libs"),
    (os.path.join(PADDLE_LIBS, "libquadmath-0.dll"), "paddle/libs"),
    (os.path.join(PADDLE_LIBS, "mkldnn.dll"), "paddle/libs"),
    (os.path.join(PADDLE_LIBS, "mklml.dll"), "paddle/libs"),
    (os.path.join(PADDLE_LIBS, "phi.dll"), "paddle/libs"),
    (os.path.join(PADDLE_LIBS, "warpctc.dll"), "paddle/libs"),
    (os.path.join(PADDLE_LIBS, "warprnnt.dll"), "paddle/libs"),

    # OpenCV
    (os.path.join(CV2_DIR, "cv2.pyd"), "cv2"),
    (os.path.join(CV2_DIR, "opencv_videoio_ffmpeg4100_64.dll"), "cv2"),

    # PDFium
    (os.path.join(PYPDFIUM_RAW, "pdfium.dll"), "pypdfium2_raw"),
]

for src, dst in manual_binaries:
    if os.path.exists(src):
        binaries.append((src, dst))

# 再自动补一层动态库，提升兼容性
for pkg in ["paddle", "cv2", "pypdfium2_raw"]:
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass

# binaries 去重
_unique_binaries = []
_seen_bin = set()
for item in binaries:
    key = (os.path.normpath(item[0]), item[1])
    if key not in _seen_bin:
        _seen_bin.add(key)
        _unique_binaries.append(item)
binaries = _unique_binaries


# ================= Analysis：分析入口脚本及依赖 =================
a = Analysis(
    ["app_gui.py"],           # 程序入口文件
    pathex=["."],             # 当前项目目录加入搜索路径
    binaries=binaries,        # 原生库 / DLL
    datas=datas,              # 数据文件 / metadata / 资源文件
    hiddenimports=hiddenimports,  # 隐藏导入模块
    hookspath=[],
    runtime_hooks=[RUNTIME_HOOK],  # 启动时执行的 hook
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

# 打包 Python 字节码
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# 生成 exe
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OCR_tool_v1.0",     # 生成的 exe 名称
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # False 表示窗口程序；调试时可改成 True 看控制台输出
    icon="app.ico",
)

# 收集所有文件到最终发布目录
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="OCR_tool_v1.0",     # dist 下生成的目录名
)
