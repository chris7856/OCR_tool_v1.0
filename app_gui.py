# -*- coding: utf-8 -*-

# GUI 主程序：
# 1. 提供文件添加、拖拽、批量识别等界面功能
# 2. 通过子进程调用 cpu_infer.py 执行 OCR 推理
# 3. 实时接收日志并更新每个文件的处理状态

import os
import sys
import time
import json
import tempfile
import traceback
import subprocess
import re
from pathlib import Path

# PySide6 核心模块：
# - Qt / Signal / Slot / QObject / QThread 用于界面和线程通信
# - QUrl / QDesktopServices 可用于打开目录或文件
from PySide6.QtCore import Qt, QThread, Signal, Slot, QObject, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QSplitter,
    QAbstractItemView,
)

# 支持导入的文件类型
SUPPORTED_EXTS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".pdf"
}

# 文件状态常量
STATUS_PENDING = "待处理"
STATUS_RUNNING = "处理中"
STATUS_SUCCESS = "成功"
STATUS_FAILED = "失败"

# 匹配 ANSI 转义序列（用于去掉终端颜色控制字符）
ANSI_RE = re.compile(r"\x1b$[0-9;]*[A-Za-z]")

def strip_ansi(text: str) -> str:
    """
    去除日志中的 ANSI 控制字符，避免界面日志框显示乱码或奇怪符号。
    """
    return ANSI_RE.sub("", text)

def get_base_dir():
    """
    获取程序运行根目录。

    规则：
    - 打包成 EXE 后，取 exe 所在目录
    - 直接运行 py 脚本时，取当前脚本所在目录
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# 程序基础目录
BASE_DIR = get_base_dir()

# 模型目录
MODEL_DIR = os.path.join(BASE_DIR, "model_file", "PaddleOCR-VL-1.5-0.9B")
LAYOUT_DIR = os.path.join(BASE_DIR, "model_file", "PP-DocLayoutV3")

# 输出目录
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# 推理脚本路径（非打包模式下会直接调用这个 py 文件）
CPU_INFER_PATH = os.path.join(BASE_DIR, "cpu_infer.py")

# 只保留这些关键日志，避免界面日志过于冗长
KEY_LOG_PREFIXES = (
    "开始加载模型",
    "模型加载完成",
    "开始推理文件:",
    "推理完成",
    "文件处理失败:",
    "运行失败:",
    "[完整异常堆栈]",
    "[错误日志已保存]",
)

def is_key_log_line(text: str) -> bool:
    """
    判断一行日志是否属于需要展示的关键日志。
    """
    text = text.strip()
    if not text:
        return False
    for prefix in KEY_LOG_PREFIXES:
        if text.startswith(prefix):
            return True
    return False


class FileDropListWidget(QListWidget):
    """
    支持拖拽文件/文件夹的列表控件。

    功能：
    - 支持拖入单个文件
    - 支持拖入文件夹，并递归收集其中的图片/PDF
    - 通过 files_dropped 信号把结果传给主窗口
    """
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)

    def dragEnterEvent(self, event):
        """
        拖拽进入控件时，如果是文件 URL，则允许拖入。
        """
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        """
        拖拽移动过程中，持续判断是否允许拖放。
        """
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        """
        文件真正放下时执行：
        - 如果拖入的是文件夹，则递归扫描支持的文件
        - 如果拖入的是单个文件，则直接加入
        """
        paths = []
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            p = url.toLocalFile()

            if os.path.isdir(p):
                # 如果是目录，则递归遍历目录下所有支持的文件
                for root, _, files in os.walk(p):
                    for name in files:
                        full = os.path.join(root, name)
                        if Path(full).suffix.lower() in SUPPORTED_EXTS:
                            paths.append(full)
            else:
                # 如果是单个文件，则判断后缀是否支持
                if Path(p).suffix.lower() in SUPPORTED_EXTS:
                    paths.append(p)

        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class OCRWorker(QObject):
    """
    OCR 后台工作对象。

    说明：
    - 这个对象通常会被移动到单独线程中执行
    - 真正的推理由独立子进程完成，不阻塞 GUI
    - 通过信号把日志、文件状态、批处理结果回传给主界面
    """
    log_signal = Signal(str)                           # 输出日志
    file_start_signal = Signal(str)                   # 某个文件开始处理
    file_done_signal = Signal(str, bool, str, float)  # 路径、是否成功、消息、耗时
    all_done_signal = Signal(int, int, float)         # 成功数、失败数、总耗时
    busy_signal = Signal(bool)                        # 当前是否忙碌

    def __init__(self):
        super().__init__()
        self._stop_flag = False   # 是否请求停止
        self._running = False     # 当前是否已有任务在执行
        self._process = None      # 当前子进程对象

    def _build_command(self, input_list_file, output_dir, result_file):
        """
        构造子进程启动命令。

        两种模式：
        1. 打包 EXE 模式：调用当前 exe，并加上子进程参数
        2. 脚本模式：调用 python + cpu_infer.py
        """
        if getattr(sys, "frozen", False):
            exe_path = sys.executable
            return [
                exe_path,
                "--infer-subprocess",
                "--input_list_file", input_list_file,
                "--output_dir", output_dir,
                "--result_file", result_file,
            ]
        else:
            return [
                sys.executable,
                CPU_INFER_PATH,
                "--input_list_file", input_list_file,
                "--output_dir", output_dir,
                "--result_file", result_file,
            ]

    @Slot(list, str)
    def process_files(self, files, output_dir):
        """
        批量处理文件。

        处理流程：
        1. 生成临时输入列表文件
        2. 启动 OCR 子进程
        3. 实时读取子进程日志
        4. 根据结果文件更新每个文件的成功/失败状态
        """
        if self._running:
            self.log_signal.emit("当前已有任务在运行，忽略本次请求。")
            return

        self._running = True
        self._stop_flag = False
        self.busy_signal.emit(True)

        start_all = time.time()
        ok_count = 0
        fail_count = 0

        # 临时输入列表文件：传给子进程，告诉它要处理哪些文件
        input_list_file = os.path.join(
            tempfile.gettempdir(),
            f"paddleocr_vl_inputs_{int(time.time() * 1000)}_{os.getpid()}.json"
        )

        # 临时结果文件：子进程处理完成后写入，GUI 再统一读取
        result_file = os.path.join(
            tempfile.gettempdir(),
            f"paddleocr_vl_result_{int(time.time() * 1000)}_{os.getpid()}.json"
        )

        try:
            # 把待处理文件列表写入临时 json
            with open(input_list_file, "w", encoding="utf-8") as f:
                json.dump(files, f, ensure_ascii=False, indent=2)

            self.log_signal.emit(f"\n{'=' * 70}")
            self.log_signal.emit(f"开始批量处理，共 {len(files)} 个文件")
            self.log_signal.emit("启动独立推理进程（本批次只加载一次模型）...")

            # 生成命令
            cmd = self._build_command(input_list_file, output_dir, result_file)

            # 强制子进程使用 utf-8 编码，减少日志解码问题
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"

            # 启动子进程，并把 stderr 合并到 stdout，方便统一读取日志
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=BASE_DIR,
                env=env,
            )

            # 用于避免同名文件重复触发“开始处理”状态
            started_files = set()

            if self._process.stdout is not None:
                for line in self._process.stdout:
                    # 如果用户点击了停止，则终止子进程
                    if self._stop_flag:
                        try:
                            self._process.kill()
                        except Exception:
                            pass
                        break

                    line = strip_ansi(line.rstrip("\r\n")).strip()
                    if not line:
                        continue

                    # 根据日志中的“开始推理文件: xxx”定位当前处理的文件
                    if line.startswith("开始推理文件: "):
                        name = line.replace("开始推理文件: ", "", 1).strip()
                        for p in files:
                            if os.path.basename(p) == name and p not in started_files:
                                started_files.add(p)
                                self.file_start_signal.emit(p)
                                break

                    # 只把关键日志推给主界面
                    if is_key_log_line(line):
                        self.log_signal.emit(line)

            return_code = self._process.wait()
            self._process = None

            if self._stop_flag:
                raise RuntimeError("用户已停止任务")

            # 约定：0 表示全成功，1 表示部分失败；这两种都属于正常返回
            if return_code not in (0, 1):
                raise RuntimeError(f"子进程退出码异常: {return_code}")

            if not os.path.isfile(result_file):
                raise RuntimeError("未生成批处理结果文件，无法确认识别结果")

            # 读取子进程输出的批处理结果
            with open(result_file, "r", encoding="utf-8") as f:
                batch_result = json.load(f)

            items = batch_result.get("items", [])
            if not isinstance(items, list):
                raise RuntimeError("结果文件格式错误：items 不是列表")

            # 逐个文件回传处理结果
            for item in items:
                file_path = os.path.abspath(item.get("input_path", ""))
                success = bool(item.get("ok", False))
                infer_cost = float(item.get("infer_cost", 0) or 0)

                if success:
                    ok_count += 1
                    msg = "处理成功"
                    self.file_done_signal.emit(file_path, True, msg, infer_cost)
                else:
                    fail_count += 1
                    err = item.get("traceback") or item.get("error") or "识别失败"
                    self.file_done_signal.emit(file_path, False, err, infer_cost)

        except Exception:
            # 捕获整个批处理过程中的异常，并统一标记为失败
            err = traceback.format_exc()
            self.log_signal.emit("处理失败：\n" + err)

            for p in files:
                fail_count += 1
                self.file_done_signal.emit(p, False, err, 0.0)

        finally:
            # 无论成功失败，都尽量清理临时文件
            try:
                if os.path.isfile(input_list_file):
                    os.remove(input_list_file)
            except Exception:
                pass

            try:
                if os.path.isfile(result_file):
                    os.remove(result_file)
            except Exception:
                pass

            total = time.time() - start_all
            self._running = False
            self.busy_signal.emit(False)
            self.all_done_signal.emit(ok_count, fail_count, total)

    @Slot()
    def stop(self):
        """
        请求停止当前任务。
        """
        self._stop_flag = True
        self.log_signal.emit("已请求停止，正在终止当前推理进程...")
        try:
            if self._process is not None:
                self._process.kill()
        except Exception:
            pass


class MainWindow(QMainWindow):
    """
    主窗口类。

    功能：
    - 管理文件列表
    - 控制开始/停止识别
    - 显示日志和状态
    - 与后台工作线程通信
    """
    start_task_signal = Signal(list, str)
    stop_task_signal = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("OCR_tool_v1.0")
        self.resize(1180, 760)

        # file_map：保存 文件路径 -> 列表项 的映射
        # file_status：记录每个文件当前状态
        self.file_map = {}
        self.file_status = {}
        self.worker_busy = False

        self.init_ui()
        self.init_worker()
        self.check_environment()

    def init_ui(self):
        """
        初始化界面布局和控件。
        """
        central = QWidget(self)
        self.setCentralWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(12, 12, 12, 12)
        root_layout.setSpacing(10)

        # 标题
        title = QLabel("OCR_tool_v1.0")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        root_layout.addWidget(title)

        # 环境/模型信息提示
        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: #444;")
        root_layout.addWidget(self.info_label)

        # 操作说明
        desc = QLabel("说明：点击“开始识别”时，只会处理状态为“待处理”或“失败”的文件；同一批次只加载一次模型。")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666;")
        root_layout.addWidget(desc)

        # 顶部按钮区
        btn_layout = QHBoxLayout()
        root_layout.addLayout(btn_layout)

        self.btn_add_files = QPushButton("添加文件")
        self.btn_add_folder = QPushButton("添加文件夹")
        self.btn_remove = QPushButton("移除选中")
        self.btn_clear = QPushButton("清空列表")
        self.btn_reset_selected = QPushButton("选中设为待处理")
        self.btn_start = QPushButton("开始识别")
        self.btn_stop = QPushButton("停止")
        self.btn_open_output = QPushButton("打开输出目录")

        for btn in [
            self.btn_add_files,
            self.btn_add_folder,
            self.btn_remove,
            self.btn_clear,
            self.btn_reset_selected,
            self.btn_start,
            self.btn_stop,
            self.btn_open_output,
        ]:
            btn.setMinimumHeight(34)
            btn_layout.addWidget(btn)

        # 默认未开始任务时，停止按钮不可用
        self.btn_stop.setEnabled(False)

        # 主体左右分栏
        splitter = QSplitter()
        root_layout.addWidget(splitter, 1)

        # 左侧：文件列表
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_title = QLabel("文件列表（支持拖拽图片/PDF到此区域）")
        left_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        left_layout.addWidget(left_title)

        self.file_list = FileDropListWidget()
        self.file_list.files_dropped.connect(self.add_files)
        left_layout.addWidget(self.file_list, 1)

        splitter.addWidget(left_widget)

        # 右侧：运行日志
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        log_title = QLabel("运行日志")
        log_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        right_layout.addWidget(log_title)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setLineWrapMode(QTextEdit.NoWrap)
        right_layout.addWidget(self.log_edit, 1)

        splitter.addWidget(right_widget)
        splitter.setSizes([520, 660])

        # 底部状态栏文字
        self.status_label = QLabel("状态：就绪")
        self.status_label.setStyleSheet("font-size: 13px; color: #333;")
        root_layout.addWidget(self.status_label)

        # 按钮信号绑定
        self.btn_add_files.clicked.connect(self.on_add_files)
        self.btn_add_folder.clicked.connect(self.on_add_folder)
        self.btn_remove.clicked.connect(self.on_remove_selected)
        self.btn_clear.clicked.connect(self.on_clear_files)
        self.btn_reset_selected.clicked.connect(self.on_reset_selected)
        self.btn_start.clicked.connect(self.on_start)
        self.btn_stop.clicked.connect(self.on_stop)
        self.btn_open_output.clicked.connect(self.open_output_dir)

    def init_worker(self):
        """
        初始化后台工作线程与工作对象。

        说明：
        - OCRWorker 负责执行实际任务调度
        - worker_thread 用于承载 worker，避免阻塞主界面
        - 主窗口通过信号与 worker 通信
        """
        self.worker_thread = QThread(self)
        self.worker = OCRWorker()
        self.worker.moveToThread(self.worker_thread)

        # 主线程 -> worker
        self.start_task_signal.connect(self.worker.process_files)
        self.stop_task_signal.connect(self.worker.stop)

        # worker -> 主线程
        self.worker.log_signal.connect(self.log)
        self.worker.file_start_signal.connect(self.on_file_start)
        self.worker.file_done_signal.connect(self.on_file_done)
        self.worker.all_done_signal.connect(self.on_all_done)
        self.worker.busy_signal.connect(self.on_worker_busy_changed)

        self.worker_thread.start()

    def closeEvent(self, event):
        """
        窗口关闭事件。

        如果当前仍在识别：
        - 先询问用户是否确认退出
        - 若确认，则请求停止任务并退出线程
        """
        try:
            if self.worker_busy:
                reply = QMessageBox.question(
                    self,
                    "确认退出",
                    "当前仍有任务在运行，确定要退出吗？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    event.ignore()
                    return

            self.stop_task_signal.emit()
            self.worker_thread.quit()
            self.worker_thread.wait(3000)
        except Exception:
            pass
        event.accept()

    def log(self, text):
        """
        向右侧日志框追加文本。
        """
        text = strip_ansi(str(text))
        self.log_edit.append(text)
        self.log_edit.ensureCursorVisible()

    def check_environment(self):
        """
        检查运行环境是否完整，并在界面中显示结果。

        检查内容：
        - 输出目录是否可创建
        - 主模型目录是否存在
        - 版面模型目录是否存在
        - 脚本模式下 cpu_infer.py 是否存在
        """
        os.makedirs(OUTPUT_DIR, exist_ok=True)

        msg = (
            f"程序目录：{BASE_DIR}<br>"
            f"VL模型目录：{MODEL_DIR} {'存在' if os.path.isdir(MODEL_DIR) else '缺失'}<br>"
            f"版面模型目录：{LAYOUT_DIR} {'存在' if os.path.isdir(LAYOUT_DIR) else '缺失'}<br>"
            f"输出目录：{OUTPUT_DIR}"
        )
        self.info_label.setText(msg)

        problems = []
        if not os.path.isdir(MODEL_DIR):
            problems.append(f"缺少模型目录：{MODEL_DIR}")
        if not os.path.isdir(LAYOUT_DIR):
            problems.append(f"缺少版面模型目录：{LAYOUT_DIR}")
        if not getattr(sys, "frozen", False) and not os.path.isfile(CPU_INFER_PATH):
            problems.append(f"缺少脚本文件：{CPU_INFER_PATH}")

        if problems:
            self.log("环境检查未通过：")
            for p in problems:
                self.log(" - " + p)
            QMessageBox.warning(self, "环境不完整", "检测到运行环境不完整，请检查文件后再运行。")
        else:
            self.log("环境检查通过。")

    def normalize_paths(self, paths):
        """
        规范化并过滤文件路径。

        处理规则：
        - 去掉空路径
        - 转为绝对路径
        - 过滤不存在的路径
        - 过滤不支持的文件类型
        - 自动去重
        """
        uniq = []
        seen = set()
        for p in paths:
            if not p:
                continue
            p = os.path.abspath(p)
            if not os.path.exists(p):
                continue
            if Path(p).suffix.lower() not in SUPPORTED_EXTS:
                continue
            if p not in seen:
                uniq.append(p)
                seen.add(p)
        return uniq

    def make_item_text(self, file_path, status, cost=None):
        """
        生成文件列表项的显示文本。

        示例：
        - 文件名 [待处理]
        - 文件名 [成功] 1.23s
        """
        name = os.path.basename(file_path)
        if cost is None:
            return f"{name}    [{status}]"
        return f"{name}    [{status}]    {cost:.2f}s"

    def set_item_status(self, file_path, status, color=None, cost=None):
        """
        更新某个文件在列表中的状态、颜色和耗时展示。
        """
        item = self.file_map.get(file_path)
        if not item:
            return
        self.file_status[file_path] = status
        item.setText(self.make_item_text(file_path, status, cost))
        item.setForeground(QColor(color if color else "#000000"))

    def add_files(self, paths):
        """
        向列表中添加文件。

        特点：
        - 自动过滤非法路径和不支持的格式
        - 自动去重
        - 新加入文件默认状态为“待处理”
        """
        paths = self.normalize_paths(paths)
        count = 0

        for p in paths:
            if p in self.file_map:
                continue

            item = QListWidgetItem(self.make_item_text(p, STATUS_PENDING))
            item.setData(Qt.UserRole, p)
            self.file_list.addItem(item)

            self.file_map[p] = item
            self.file_status[p] = STATUS_PENDING
            count += 1

        if count:
            self.log(f"已加入 {count} 个文件。")
            self.update_pending_status_text()

    def get_runnable_files(self):
        """
        获取当前可执行识别的文件列表。

        规则：
        - 仅“待处理”和“失败”的文件会重新执行
        - “成功”状态的文件默认跳过
        """
        runnable = []
        for p in self.file_map.keys():
            st = self.file_status.get(p, STATUS_PENDING)
            if st in (STATUS_PENDING, STATUS_FAILED):
                runnable.append(p)
        return runnable

    def update_pending_status_text(self):
        """
        刷新底部状态栏统计信息。
        """
        total = len(self.file_map)
        pending = sum(1 for s in self.file_status.values() if s == STATUS_PENDING)
        failed = sum(1 for s in self.file_status.values() if s == STATUS_FAILED)
        success = sum(1 for s in self.file_status.values() if s == STATUS_SUCCESS)

        self.status_label.setText(
            f"状态：列表共 {total} 个，待处理 {pending}，失败 {failed}，成功 {success}"
        )

    def on_add_files(self):
        """
        点击“添加文件”按钮后执行：
        弹出文件选择框，选择后加入列表。
        """
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择文件",
            BASE_DIR,
            "支持文件 (*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff *.pdf)",
        )
        if files:
            self.add_files(files)

    def on_add_folder(self):
        """
        点击“添加文件夹”按钮后执行：
        递归扫描文件夹下所有支持的文件并加入列表。
        """
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹", BASE_DIR)
        if not folder:
            return

        paths = []
        for root, _, files in os.walk(folder):
            for name in files:
                full = os.path.join(root, name)
                if Path(full).suffix.lower() in SUPPORTED_EXTS:
                    paths.append(full)
        self.add_files(paths)

    def on_remove_selected(self):
        """
        移除当前选中的文件。

        注意：
        - 识别过程中不允许移除，防止状态错乱
        """
        if self.worker_busy:
            QMessageBox.information(self, "提示", "当前正在识别，不能移除。")
            return

        items = self.file_list.selectedItems()
        if not items:
            return

        for item in items:
            p = item.data(Qt.UserRole)
            self.file_map.pop(p, None)
            self.file_status.pop(p, None)
            self.file_list.takeItem(self.file_list.row(item))

        self.update_pending_status_text()

    def on_clear_files(self):
        """
        清空整个文件列表。

        注意：
        - 识别过程中不允许清空
        """
        if self.worker_busy:
            QMessageBox.information(self, "提示", "当前正在识别，不能清空。")
            return

        self.file_list.clear()
        self.file_map.clear()
        self.file_status.clear()
        self.status_label.setText("状态：就绪")
        self.log("文件列表已清空。")

    def on_reset_selected(self):
        """
        将选中的文件重置为“待处理”状态，
        便于手动重新执行识别。
        """
        if self.worker_busy:
            QMessageBox.information(self, "提示", "当前正在识别，不能修改状态。")
            return

        items = self.file_list.selectedItems()
        if not items:
            QMessageBox.information(self, "提示", "请先选中文件。")
            return

        count = 0
        for item in items:
            p = item.data(Qt.UserRole)
            self.set_item_status(p, STATUS_PENDING, "#000000")
            count += 1

        self.log(f"已将 {count} 个选中文件设为待处理。")
        self.update_pending_status_text()

    def on_start(self):
        """
        点击“开始识别”按钮后执行。

        执行前会检查：
        - 当前是否已有任务在运行
        - 是否存在可运行文件
        - 模型目录是否完整
        """
        if self.worker_busy:
            QMessageBox.information(self, "提示", "任务已经在运行中。")
            return

        files = self.get_runnable_files()
        if not files:
            QMessageBox.information(
                self,
                "提示",
                "当前没有可识别文件。\n只有“待处理”和“失败”的文件会被执行；“成功”的文件会自动跳过。"
            )
            return

        if not os.path.isdir(MODEL_DIR) or not os.path.isdir(LAYOUT_DIR):
            QMessageBox.warning(self, "提示", "模型目录不完整，无法开始识别。")
            return

        os.makedirs(OUTPUT_DIR, exist_ok=True)

        self.status_label.setText(f"状态：开始处理，共 {len(files)} 个文件")
        self.log("")
        self.log("开始批量识别...")
        self.log("本次仅处理状态为“待处理”或“失败”的文件，且本批次只加载一次模型。")
        self.start_task_signal.emit(files, OUTPUT_DIR)

    def on_stop(self):
        """
        点击“停止”按钮后执行：
        向后台 worker 发送停止请求。
        """
        if self.worker_busy:
            self.stop_task_signal.emit()

    def on_worker_busy_changed(self, busy):
        """
        根据 worker 是否忙碌，统一更新界面按钮可用状态。
        """
        self.worker_busy = busy
        self.btn_start.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        self.btn_add_files.setEnabled(not busy)
        self.btn_add_folder.setEnabled(not busy)
        self.btn_remove.setEnabled(not busy)
        self.btn_clear.setEnabled(not busy)
        self.btn_reset_selected.setEnabled(not busy)

    def on_file_start(self, file_path):
        """
        某个文件开始识别时更新界面状态。
        """
        self.set_item_status(file_path, STATUS_RUNNING, "#1f6feb")
        self.status_label.setText(f"状态：处理中 -> {os.path.basename(file_path)}")

    def on_file_done(self, file_path, success, message, cost):
        """
        某个文件处理完成时更新状态和日志。

        参数：
        - file_path: 文件路径
        - success: 是否成功
        - message: 成功提示或错误信息
        - cost: 单文件耗时
        """
        if success:
            self.set_item_status(file_path, STATUS_SUCCESS, "#1a7f37", cost)
            self.log(f"✅ {os.path.basename(file_path)} 处理成功，耗时 {cost:.2f}s")
        else:
            self.set_item_status(file_path, STATUS_FAILED, "#d1242f", cost if cost else None)
            self.log(f"❌ {os.path.basename(file_path)} 处理失败，耗时 {cost:.2f}s")
            self.log(message)

    def on_all_done(self, ok_count, fail_count, total_cost):
        """
        整个批次任务完成后执行：
        - 更新统计
        - 写日志
        - 弹窗提示结果
        """
        self.update_pending_status_text()
        self.log("")
        self.log(f"全部处理完成：成功 {ok_count}，失败 {fail_count}，总耗时 {total_cost:.2f}s")

        QMessageBox.information(
            self,
            "识别完成",
            f"任务完成。\n成功：{ok_count}\n失败：{fail_count}\n总耗时：{total_cost:.2f}s",
        )

    def open_output_dir(self):
        """
        打开输出目录，便于查看识别结果文件。
        """
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(OUTPUT_DIR))


def main():
    """
    程序主入口。

    两种启动模式：
    1. 普通 GUI 模式：启动界面
    2. 推理子进程模式：当带有 --infer-subprocess 参数时，直接转入 cpu_infer.cli_main()
    """
    if "--infer-subprocess" in sys.argv:
        import cpu_infer
        args = [x for x in sys.argv[1:] if x != "--infer-subprocess"]
        sys.argv = [sys.argv[0]] + args
        code = cpu_infer.cli_main()
        sys.exit(code)

    app = QApplication(sys.argv)
    app.setApplicationName("OCR_tool_v1.0")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
