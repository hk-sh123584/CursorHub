# 项目：CursorHub — Windows 鼠标光标快速切换工具

## 背景与目标
用户下载了很多套 Windows 光标主题包（散装 .cur / .ani 文件）。
目标：做一个工具，让用户选择一个包含光标文件的文件夹，程序能自动识别每个文件对应的系统角色，一键应用整套主题，并立即生效。

## 核心设计决策
1. 用户按“整套”切换，而不是单个文件切换。
2. 光标包的命名非常规范（如 Normal.ani, Link.ani）。程序必须内置一个“文件名到注册表键名”的映射字典，实现自动匹配。
3. 不需要解析文件夹里的 .inf 文件，直接扫描 .cur 和 .ani 写入注册表。
4. 环境：Windows，Orca 终端中的 Claude Code，Python 标准库。
5. 用户零基础，请拆解任务，每次只写一步，并给出验证命令。

## 核心原理
Windows 光标配置在注册表：
`HKEY_CURRENT_USER\Control Panel\Cursors`

修改各角色对应的值（需要写绝对路径），然后调用：
`ctypes.windll.user32.SystemParametersInfoA(0x0057, 0, None, 0)`
让系统立即重载光标。

## 自动映射字典（文件名前缀 -> 注册表键名）
请把以下逻辑内置到代码中，匹配时忽略大小写和扩展名：
- Normal -> Arrow
- Link -> Hand
- Busy -> Wait
- Precision -> Crosshair
- Text -> IBeam
- Handwriting -> NWPen
- Unavailable -> No
- Vertical -> SizeNS
- Horizontal -> SizeWE
- Diagonal1 -> SizeNWSE
- Diagonal2 -> SizeNESW
- Move -> SizeAll
- Alternate -> UpArrow
- Help -> Help
- Pin -> Pin
- Person -> Person

## 第一周任务（命令行版最小闭环）

### 第 1 步：读取当前光标
创建单文件脚本 `CursorHub.py`。
只写函数 `list_current_cursors()`：用 `winreg.EnumValue` 遍历 `HKCU\Control Panel\Cursors`，打印所有值名和当前路径。
验证命令：`python CursorHub.py`

### 第 2 步：写注册表 + 立即刷新
增加：
- `set_cursor(role, filepath)`：用 `winreg.SetValueEx` 写入对应值。
- `refresh_cursors()`：调用 `SystemParametersInfoA(0x0057, 0, None, 0)`。
在 `if __name__ == "__main__":` 里手动测试一次（比如把 Arrow 换成文件夹里的 Normal.ani），确认鼠标立即变化。

### 第 3 步：扫描文件夹并自动匹配
增加 `scan_and_map(folder_path)`：
- 用 `os.listdir` 获取所有文件。
- 过滤出 `.cur` 和 `.ani`。
- 提取文件名（不含扩展名），通过上面的字典，匹配出对应的注册表角色键名。
- 返回一个字典，例如 `{"Arrow": "C:\\...\\Normal.ani", "Hand": "C:\\...\\Link.ani"}`。
- 打印出匹配结果，让用户检查是否有遗漏。

### 第 4 步：一键应用整套
增加 `apply_theme(folder_path)`：
- 调用 `scan_and_map`。
- 遍历结果，调用 `set_cursor` 批量写入。
- 所有写入完成后，调用一次 `refresh_cursors()`。
- 在 `if __name__ == "__main__":` 里测试，输入文件夹路径，一键应用。

## 已知风险与兜底方案
Win11 某些版本存在 `SPI_SETCURSORS` 调用返回成功但光标不刷新的问题。
如果第 2 步不生效，改用兜底方案：用 `ctypes` 直接调用 `SetSystemCursor`，对各个角色分别装入文件，绕过 `SPI_SETCURSORS`。
第一周的任务之一就是确认用户机器上哪条路走得通。

## 给 Claude Code 的工作要求
- 每次只做一小步，不要一次性生成完整代码。
- 每次修改后告诉用户运行什么命令、预期看到什么结果。
- 代码优先使用 Python 标准库，避免额外依赖。
- 遇到报错时，先让用户粘贴完整报错，再判断原因。