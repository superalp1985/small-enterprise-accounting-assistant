# 第三方组件说明

本项目的自定义许可证只覆盖项目权利人自行编写并明确以该许可证发布的材料，
不覆盖以下第三方组件。各组件仍由其原权利人拥有，并适用各自发行版本随附的
许可证和声明。

## 源码运行依赖

- [NumPy](https://numpy.org/)：BSD-3-Clause。
- [pandas](https://pandas.pydata.org/)：BSD-3-Clause。
- [Requests](https://requests.readthedocs.io/)：Apache-2.0。
- [openpyxl](https://openpyxl.readthedocs.io/)：MIT。
- Python 标准库和 Tk：以相应 Python/Tcl/Tk 发行版许可证为准。

`requirements.txt` 使用版本范围，实际部署时应以环境中安装版本的元数据和许可证
文件为准。如果新增依赖，提交者必须同步更新本文件。

## 可选本地模型与 OCR 组件

- Qwen3.5 模型：以模型发布方提供的模型许可证为准。
- [llama.cpp / llama-server](https://github.com/ggml-org/llama.cpp)：以对应发行版本
  附带的许可证为准。
- [RapidOCR](https://github.com/RapidAI/RapidOCR) 及其检测、识别、方向分类模型：
  软件代码和每个模型权重分别以其仓库或模型文件附带许可证为准。
- ONNX Runtime、OpenCV、Pillow、pypdfium2、PyYAML、Shapely、pyclipper、protobuf
  及 OCR 运行时的其他依赖：以对应安装包内的许可证和第三方声明为准。

## 构建与分发工具

- CPython 3.12 嵌入式运行时：以 Python Software Foundation License 为准。
- PyInstaller：仅作为构建工具使用；以 PyInstaller 许可证及其 bootloader 例外为准。
- Inno Setup：仅作为安装程序构建工具使用；以其发行版本许可证为准。

## 仓库与二进制发布边界

公开源码仓库默认排除 `models/`、`runtime/`、`build/`、`dist/` 和 `release/`，
因此不分发模型权重、推理二进制、OCR 运行时或安装程序。`.gitignore` 和
`scripts/check_public_release.ps1` 会在首次提交前检查这一边界。

发布安装包前，发布者必须逐项确认实际捆绑版本允许再分发，并在安装包中保留所需的
许可证全文、版权声明、模型使用条款和第三方通知。本文件是索引，不替代任何第三方
许可证全文，也不保证任意第三方组件可以用于本项目许可之外的用途。
