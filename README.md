# CursorHub

CursorHub 是一个 Windows 鼠标光标主题管理工具，用来本地收藏、预览和一键切换整套 `.cur` / `.ani` 光标主题。

## 功能

- 支持把主题文件夹直接拖进窗口添加（也可拖入单个 `.cur` / `.ani` 文件）
- 支持手动刷新（右上角按钮或 F5）重新检测主题文件夹是否失效，无需重启程序
- 顶部搜索框可按主题名称或路径过滤；失效主题可一键批量清理
- 从其他程序切回窗口时自动检测一次；只有状态变化时才刷新列表，不会打断滚动
- 自动扫描主题文件夹，根据文件名匹配 Windows 的 17 个标准光标角色
- 应用结果直接显示在对应卡片的按钮下方，不再弹窗；停留时间可在设置中选择（2/4/6/10 秒或常驻）
- 设置内提供「使用说明 / 关于」窗口：快速开始、数据文件位置、注意事项、常见问题，并可打开日志与数据目录
- 一键应用整套主题，写入注册表并立即刷新当前会话的光标
- 主题管理面板：添加、删除、重命名、应用、查看上次使用时间
- 图片预览：显示 Normal 光标的真实图片，`.ani` 会按原始帧率循环播放动画
- 预览窗口列出文件明细、对应角色和完整度
- 应用主题前自动备份当前光标（最多 20 份），设置里可一键恢复上次状态
- 可点击的预览列表：点击任意文件即可切换顶部图片，鼠标悬停时整行高亮
- 支持 PNG-in-CUR：兼容把 PNG 图片封装进 CUR 帧的高分辨率动态光标
- 内置 Windows 默认主题，一键恢复系统默认光标
- 深色 / 浅色 / 跟随系统外观切换，自动保存设置
- 专属应用图标：深色渐变圆角背景 + 白蓝渐变发光光标，嵌入 exe 与所有窗口
- 支持使用 PyInstaller 打包为单文件 exe

## 环境要求

- Windows 10 / Windows 11
- Python 3.10 及以上
- `customtkinter`、`Pillow`、`ani_file`、`tkinterdnd2`

安装依赖：

```bat
python -m pip install -r requirements.txt
```

## 运行

```bat
python CursorHub.py
```

程序启动后会打开主题管理面板：

1. 点击右上角 **➕ 添加新主题**
2. 选择一个包含 `.cur` / `.ani` 文件的文件夹，也可以直接把文件夹拖进窗口
3. 点击主题卡片上的 **应用** 即可切换整套光标
4. 点击 **🖼️ 预览** 可以查看 Normal 光标的真实图片（`.ani` 显示第一帧）以及文件明细和角色完整度
5. 在预览窗口中点击任意文件行，顶部图片会立即切换到该文件的第一帧
6. 点击左下角 **⚙️ 设置** 可以切换外观模式

## 主题文件命名

程序会自动识别以下常见文件名（忽略大小写和扩展名）：

| 文件名 | 注册表角色 | 说明 |
|---|---|---|
| Normal | Arrow | 正常选择 |
| Help | Help | 帮助选择 |
| Working | AppStarting | 后台运行 |
| Busy | Wait | 忙碌 |
| Precision | Crosshair | 精确选择 |
| Text | IBeam | 文本选择 |
| Handwriting | NWPen | 手写 |
| Unavailable | No | 不可用 |
| Vertical | SizeNS | 垂直调整大小 |
| Horizontal | SizeWE | 水平调整大小 |
| Diagonal1 | SizeNWSE | 对角线调整大小 1 |
| Diagonal2 | SizeNESW | 对角线调整大小 2 |
| Move | SizeAll | 移动 |
| Alternate | UpArrow | 候选选择 |
| Link | Hand | 链接选择 |
| Pin | Pin | 位置选择 |
| Person | Person | 人员选择 |

## 数据文件

运行时会自动生成以下文件，位置与 `CursorHub.py` 或 exe 相同：

- `themes.json`：收藏的主题列表
- `settings.json`：外观设置
- `CursorHub.log`：运行日志

这些文件属于本地运行数据，已通过 `.gitignore` 排除，不会提交到仓库。

## 生成图标

修改图标样式后，先重新生成图标文件：

```bat
python generate_icon.py
```

会在项目目录生成：

- `icon.png`：512×512 预览图
- `icon.ico`：包含 16 / 24 / 32 / 48 / 64 / 128 / 256 七种尺寸

## 打包

```bat
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name CursorHub --icon=icon.ico --add-data "icon.ico;." --collect-all customtkinter --collect-all tkinterdnd2 --hidden-import darkdetect --hidden-import ani_file --hidden-import ani_file.ani_file --hidden-import chunk CursorHub.py
```

生成文件：

```text
dist\CursorHub.exe
```

- `--icon=icon.ico`：把图标嵌入 exe，资源管理器和任务栏显示该图标
- `--add-data "icon.ico;."`：把图标一起打包，运行时供 `iconbitmap()` 设置窗口图标
- `--collect-all customtkinter`：收集 customtkinter 的主题、字体等资源文件

项目里也提供了 `CursorHub.spec`，等价命令：

```bat
python -m PyInstaller CursorHub.spec --noconfirm
```

## 技术说明

- 光标配置保存在 `HKEY_CURRENT_USER\Control Panel\Cursors`
- 写入注册表后，优先尝试 `SystemParametersInfoW(SPI_SETCURSORS)` 刷新
- 如果系统不刷新，则使用 `LoadImageW` + `SetSystemCursor` 兜底
- 使用 `LR_DEFAULTSIZE` 按系统光标尺寸加载，避免高分辨率主题文件显示过大
- 某些系统上 `NWPen` / `Pin` / `Person` 可能无法立即刷新，但注册表已写入，注销或重启后会生效
- 图片预览使用 Pillow：`.cur` 直接解码
- `.ani` 优先用 `ani_file` 库提取第一帧到临时文件，再交给 Pillow 解码并清理临时文件
- 如果 `ani_file` 解析失败，自动回退到内置 RIFF 解析器；两种方式都失败时显示灰色默认箭头和「无法提取预览」
- 支持 PNG-in-CUR：Pillow 原生 CUR 插件不支持 PNG 压缩帧，程序会手动解析 CUR 目录并直接解码 PNG 帧，或把单个尺寸重新封装后交给 Pillow
- 预览失败或找不到 `Normal` 文件时，会显示 PIL 绘制的灰色默认箭头
- 窗口图标通过 `iconbitmap()` 设置，失败时回退到 `iconphoto()`；打包后图标从 `sys._MEIPASS` 读取
- 启动时设置 AppUserModelID，让 Windows 把程序视为独立应用，任务栏才会使用自定义图标
- 程序只操作当前用户注册表，不需要管理员权限

## 目录结构

```text
CursorHub.py       主程序（单文件）
generate_icon.py   图标生成脚本
icon.png           512×512 图标预览
icon.ico           Windows 多尺寸图标
requirements.txt   运行依赖
README.md          项目说明
CLAUDE.md          项目开发说明
PLAN.md            项目设计计划
```

## 说明

主题文件本身（`.cur` / `.ani`）不包含在本仓库中，使用时请自行准备光标主题文件夹。
