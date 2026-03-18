# -*- mode: python ; coding: utf-8 -*-

# PyInstaller 打包配置文件
# 作用：
# 1. 指定 GUI 主入口 app_gui.py
# 2. 收集 PaddleOCR / PaddleX / PaddlePaddle / OpenCV 等依赖
# 3. 收集模型目录、测试目录、输出目录等项目资源
# 4. 收集 Excel 导出相关依赖（openpyxl / beautifulsoup4 / et_xmlfile）
# 5. 自动适配当前 Python/conda 环境中的 site-packages 路径，避免写死环境路径

import os
import sys
import site
import sysconfig
from PyInstaller.utils.hooks import copy_metadata, collect_submodules


# ================= 自动获取当前环境的 site-packages 路径 =================
# 自动查找当前解释器对应的 site-packages 路径。
def find_site_packages():
    """
    自动寻找当前 Python 环境的 site-packages 目录。
    优先级：
    1. site.getsitepackages()
    2. sysconfig.get_paths()
    3. 根据 sys.prefix / sys.exec_prefix 拼接常见路径
    """
    candidates = []

    # 方式1：标准 site 接口
    try:
        for p in site.getsitepackages():
            if p and os.path.isdir(p):
                candidates.append(p)
    except Exception:
        pass

    # 方式2：sysconfig
    try:
        paths = sysconfig.get_paths()
        for key in ("purelib", "platlib"):
            p = paths.get(key)
            if p and os.path.isdir(p):
                candidates.append(p)
    except Exception:
        pass

    # 方式3：基于当前 Python/conda 环境路径拼接
    # Windows 常见路径：<env>\Lib\site-packages
    # Linux/macOS 常见路径：<env>/lib/pythonX.Y/site-packages
    version_tag = f"python{sys.version_info.major}.{sys.version_info.minor}"
    prefix_list = [sys.prefix, sys.exec_prefix]

    for prefix in prefix_list:
        if not prefix:
            continue

        win_path = os.path.join(prefix, "Lib", "site-packages")
        unix_path = os.path.join(prefix, "lib", version_tag, "site-packages")

        if os.path.isdir(win_path):
            candidates.append(win_path)
        if os.path.isdir(unix_path):
            candidates.append(unix_path)

    # 去重后返回第一个存在的目录
    seen = set()
    for p in candidates:
        p = os.path.abspath(p)
        if p not in seen and os.path.isdir(p):
            seen.add(p)
            return p

    raise RuntimeError("未能自动找到当前环境的 site-packages 目录，请检查 Python/conda 环境。")


# 当前环境的 site-packages 根目录
SITE = find_site_packages()

# Paddle 动态库目录
PADDLE_LIBS = os.path.join(SITE, "paddle", "libs")

# Paddle base 目录，libpaddle.pyd 在这里
PADDLE_BASE = os.path.join(SITE, "paddle", "base")

# OpenCV 安装目录
CV2_DIR = os.path.join(SITE, "cv2")

# pypdfium2 的底层原生 DLL 所在目录
PYPDFIUM_RAW = os.path.join(SITE, "pypdfium2_raw")

# 新版 PyInstaller 中通常保持 None 
block_cipher = None


# ================= metadata 复制 =================
# 复制安装包的 dist-info / 元数据
# 某些库在运行时会通过 importlib.metadata 读取版本信息，
# 若 metadata 丢失，打包后可能报错
datas = []
for pkg in [
    "paddleocr", "paddlepaddle", "paddlex", "numpy",
    "safetensors", "sentencepiece", "Jinja2",
    "opencv-contrib-python", "pypdfium2", "tokenizers",
    "huggingface_hub", "packaging", "filelock", "regex", "tqdm",
    "openpyxl", "beautifulsoup4", "et_xmlfile",
]:
    try:
        datas += copy_metadata(pkg)
    except Exception:
        # 个别包可能没有 metadata，忽略
        pass


# ================= 项目资源 =================
# 收集项目目录下需要一起发布的资源文件夹
# 打包后会复制到 dist 输出目录中
for name in ["model_file", "test_img", "output"]:
    p = os.path.join(".", name)
    if os.path.exists(p):
        datas.append((p, name))


# ================= 原有依赖目录 =================
# 手工补充一些重要依赖目录/文件
# 原因：
# 某些库存在动态导入、内部资源查找、运行时配置读取等行为，
# PyInstaller 自动分析不一定能完整收集到
datas += [
    # PaddleX / PaddleOCR 包目录
    (os.path.join(SITE, "paddlex"), "paddlex"),
    (os.path.join(SITE, "paddleocr"), "paddleocr"),

    # OpenCV 配置文件
    (os.path.join(CV2_DIR, "__init__.py"), "cv2"),
    (os.path.join(CV2_DIR, "config.py"), "cv2"),
    (os.path.join(CV2_DIR, "config-3.py"), "cv2"),
    (os.path.join(CV2_DIR, "load_config_py3.py"), "cv2"),

    # 其它常见依赖目录
    (os.path.join(SITE, "shapely"), "shapely"),
    (os.path.join(SITE, "pypdfium2"), "pypdfium2"),
    (os.path.join(SITE, "pypdfium2_raw"), "pypdfium2_raw"),
    (os.path.join(SITE, "sentencepiece"), "sentencepiece"),
    (os.path.join(SITE, "tokenizers"), "tokenizers"),
    (os.path.join(SITE, "jinja2"), "jinja2"),
    (os.path.join(SITE, "markupsafe"), "markupsafe"),
]


# ================= hiddenimports =================
# 显式声明隐藏导入模块
# 对大量动态导入的库非常重要，能减少打包后运行时报错：
# “No module named xxx”
hiddenimports = [
    # 主程序/子进程入口
    "cpu_infer",

    # PySide6 图形界面模块
    "PySide6", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",

    # paddle 主体模块
    "paddle", "paddle.base", "paddle.base.core", "paddle.fluid",
    "paddle.utils", "paddle.nn", "paddle.nn.functional",
    "paddle.optimizer", "paddle.io", "paddle.vision", "paddle.vision.transforms",

    # paddleocr
    "paddleocr", "paddleocr.paddleocr",

    # paddlex 相关
    "paddlex",
    "paddlex.inference",
    "paddlex.inference.pipelines",
    "paddlex.inference.pipelines.doc_preprocessor",
    "paddlex.inference.pipelines.ocr",
    "paddlex.inference.models",
    "paddlex.inference.components",
    "paddlex.repo_apis",
    "paddlex.utils",

    # cv2 / numpy
    "cv2", "numpy",

    # PIL 图像处理
    "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont",

    # tokenizer / transformer 相关
    "tokenizers", "sentencepiece", "huggingface_hub", "safetensors",

    # 科学计算相关
    "einops", "scipy", "scipy.special", "scipy.ndimage",

    # sklearn
    "sklearn", "sklearn.utils", "joblib",

    # shapely
    "shapely", "shapely.geometry",

    # Excel / HTML 解析
    # 对应 cpu_infer.py 中的 Excel 导出功能
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

    # 其它通用依赖
    "pyclipper", "lxml", "lxml.etree", "openpyxl",
    "pypdfium2", "pypdfium2_raw", "yaml", "pyyaml", "tqdm", "regex",
    "filelock", "packaging", "importlib_metadata", "importlib.metadata",
    "ruamel.yaml", "ruamel.yaml.comments", "colorlog", "prettytable",
    "ujson", "psutil", "jinja2", "jinja2.sandbox", "jinja2.environment",
    "jinja2.runtime", "jinja2.utils", "jinja2.filters", "jinja2.tests",
    "jinja2.lexer", "jinja2.parser", "jinja2.compiler", "jinja2.optimizer",
    "jinja2.ext", "jinja2.defaults", "jinja2.exceptions",
    "jinja2.loaders", "markupsafe",
]


# ================= 自动补动态子模块 =================
# 对部分复杂库递归收集所有子模块，
# 进一步降低“打包后缺模块”的概率
for pkg in [
    "PySide6", "paddleocr", "paddlex", "safetensors",
    "jinja2", "sentencepiece", "tokenizers", "pypdfium2", "shapely",
    "openpyxl", "bs4",
]:
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

# 去重并排序
hiddenimports = sorted(set(hiddenimports))


# ================= binaries =================
# 手工收集运行时必须的原生二进制文件（.pyd / .dll）
# 对 Paddle / OpenCV / PDF 渲染功能尤其关键
binaries = [
    # Paddle 核心 pyd
    (os.path.join(PADDLE_BASE, "libpaddle.pyd"), "paddle/base"),

    # Paddle 运行依赖 dll
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

    # OpenCV 核心文件
    (os.path.join(CV2_DIR, "cv2.pyd"), "cv2"),
    (os.path.join(CV2_DIR, "opencv_videoio_ffmpeg4100_64.dll"), "cv2"),

    # PDF 渲染依赖
    (os.path.join(PYPDFIUM_RAW, "pdfium.dll"), "pypdfium2_raw"),
]


# ================= Analysis =================
# 分析主程序及所有依赖
# app_gui.py 是 GUI 主入口
a = Analysis(
    ["app_gui.py"],             # 主入口脚本
    pathex=["."],               # 模块搜索路径，"." 表示当前项目目录
    binaries=binaries,          # 二进制依赖
    datas=datas,                # 资源文件/数据文件
    hiddenimports=hiddenimports,# 隐藏导入模块
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)


# 将纯 Python 模块打包成归档
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)


# ================= EXE =================
# 生成主 exe
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,      # 二进制文件延后由 COLLECT 收集
    name="OCR_tool_v1.0",    # 生成的 exe 名称
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                  # 通常建议先关闭 upx，避免某些 dll 被压缩后异常
    console=False,              # False 表示 GUI 程序，不弹控制台黑框
    icon="app.ico",             # 程序图标
)


# ================= COLLECT =================
# 收集 exe、dll、资源文件、数据文件到 dist 目录
# 最终通常分发整个 dist/OCR_tool_v1.0 文件夹
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="OCR_tool_v1.0",
)
