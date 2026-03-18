# OCR_tool_v1.0

一个基于 PaddleOCR-VL-1.5 的本地离线 OCR 图形工具，通过独立子进程执行推理任务，支持批量处理图片与 PDF 文件。

## 1.项目简介

OCR_tool_v1.0是一个面向本地离线场景的 OCR 图形界面工具。  
项目提供简洁直观的桌面端操作方式，便于用户对图片和 PDF 文件进行批量识别，并查看处理日志与输出结果。

## 2.功能特性

- 支持批量导入图片与 PDF 文件
- 支持拖拽添加文件和文件夹
- 支持仅处理“待处理”或“失败”状态的文件
- 单批次任务中只加载一次模型，减少重复初始化开销
- 实时显示关键运行日志
- 支持中止当前识别任务
- 支持快速打开输出目录
- 支持本地离线运行

## 3.支持的文件格式

- .jpg
- .jpeg
- .png
- .bmp
- .webp
- .tif
- .tiff
- .pdf

## 4.项目结构

```text
OCR_tool_v1.0/
├─ app_gui.py                  # GUI 主程序
├─ cpu_infer.py                # OCR 推理脚本
├─ gui.spec                    # 打包配置
├─ app.ico                     # 应用图标
├─ model_file/                 # 第三方开源模型文件
    ├─ PaddleOCR-VL-1.5-0.9B/  # VL模型文件
    ├─ PP-DocLayoutV3/         # 版面分析模型文件
├─ test_img/                   # 测试样例文件
├─ LICENSE                     # Apache License 2.0
├─ NOTICE                      # 项目说明 / 版权声明
├─ THIRD_PARTY_NOTICES.md      # 第三方组件说明
└─ README.md
```

## 5.基础配置

建议使用 Conda 虚拟环境运行本项目
- 操作系统：Windows 11专业版
- Python 版本：3.9.25

## 6.快速开始

### 6.1.获取项目

- 将项目克隆或下载到本地：
```bash
git clone <your-repository-url>
cd PADDLEOCR-VL-1.5-EXE
```

若你当前并未使用 Git，也可以直接下载仓库压缩包并解压到本地。
注意：model_file模型需自行去huggingface下载，链接如下

```text
https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5/tree/main
https://huggingface.co/PaddlePaddle/PP-DocLayoutV3/tree/main
```

### 6.2.安装依赖

在控制台逐个执行以下命令以安装依赖项
```bash
pip install paddlepaddle==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
pip install "paddleocr[doc-parser]"
pip install PySide6
pip install pyinstaller
```

### 6.3启动程序
在项目根目录下执行：

```bash
python app_gui.py
```

## 7.打包运行

控制台执行如下命令

```bash
pyinstaller gui.spec
```

执行完毕后项目根目录将生成dist文件夹，将model_file文件夹全部复制进exe同级目录，点击exe运行

## 8.使用说明

1.启动程序
2.添加图片或 PDF 文件
3.根据需要选择处理范围或处理策略
4.开始识别
5.在日志区域查看处理状态与运行信息
6.识别完成后，在输出目录中查看结果
7.若任务执行过程中需要中断，可使用程序提供的停止功能终止当前识别流程。

## 9.输出说明

识别结果默认输出到项目配置对应的输出目录中。
如程序内已设置固定输出路径，请以程序实际行为为准。

示例：
```text
output/
```

建议在实际使用前先通过少量样本测试输出内容与目录结构，以确认结果符合你的使用需求。

## 10.第三方内容说明
本仓库中实际包含的第三方内容主要位于以下目录：

```text
model_file/
```

相关说明如下：
- model_file/ 目录中的模型文件及其附带材料来源于第三方开源项目
- 该部分内容的版权归原作者或原项目方所有
- 本仓库不会改变该部分内容的原始许可证
- 若该目录内附带原始 LICENSE、NOTICE、README 或其他声明文件，应一并保留，并以其原始内容为准

更多信息请参见：

- THIRD_PARTY_NOTICES.md
- NOTICE

## 11.许可证
本仓库中由项目作者编写的原创代码采用以下许可证：

Apache License 2.0

完整许可证文本请参见根目录中的 LICENSE 文件。

## 12.许可证适用范围说明

请注意，本仓库中的许可证适用范围区分如下：

- 仓库内由项目作者原创并享有版权的代码部分，适用 Apache License 2.0
- model_file/ 目录中的第三方模型文件及附带材料，仍适用其各自原始许可证
- 未直接随本仓库分发的外部运行时依赖（例如 Conda 环境中的 Python 包），不属于本仓库内分发内容
- 若你在后续打包、再分发、部署或商用过程中引入更多第三方依赖，请自行核查并遵守相应许可证要求

## 13.使用注意事项

- 请确保你对输入文件拥有合法使用权
- 如测试数据、图片、PDF 或其他文档来源于第三方，请自行确认其授权状态
- 若将本项目用于再分发、部署或商业用途，请务必提前核查相关第三方组件、模型及依赖的许可证要求
- 在生产环境中使用前，建议先进行充分测试与验证

## 14.免责声明

本项目按“现状”（AS IS）提供，不附带任何明示或暗示的担保。
项目作者不对因使用本项目而产生的任何直接、间接、附带、特殊或衍生性损失承担责任。

## 15.联系与维护

如对项目内容、许可证适用范围或第三方说明存在疑问，可联系项目维护者进一步确认。

## 16.致谢

感谢相关第三方开源项目、模型作者及社区贡献者提供的基础能力与支持。