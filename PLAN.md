# 拟定方案：CursorHub — Windows 鼠标光标快速切换工具

> 制定日期：2026-11-10
> 依据：`CLAUDE.md`

---

## 一、总体方案

**技术链路：**

```
选文件夹 → 扫描 .cur/.ani → 文件名归一化 → 匹配注册表角色
        → 预览（冲突/缺失检查）→ 自动备份当前配置
        → 用 winreg 批量写入 HKCU\Control Panel\Cursors
        → SPI_SETCURSORS 刷新 → 若无效 → SetSystemCursor 兜底
```

两条职责要分清：

| 动作 | 手段 | 作用 |
|---|---|---|
| 持久化 | `winreg` 写注册表 | 注销/重启后主题仍然保留 |
| 立即生效 | `SystemParametersInfoW(0x57)` | 让当前会话立刻换光标 |
| 兜底立即生效 | `SetSystemCursor` | SPI 失效时绕过它，仅当前会话有效，但注册表已写，重启后照样生效 |

全程只依赖 Python 标准库（`winreg`、`ctypes`、`os`、`json`），不需要管理员权限。

---

## 二、对 CLAUDE.md 原方案的补充（6 个点）

1. **字典缺了第 17 个角色 AppStarting。** 现在字典只有 16 项，缺少“后台忙碌”（箭头旁带沙漏）。光标包里通常叫 `Working.ani`，CLAUDE.md 未覆盖，导致应用后这个状态仍显示旧主题。
2. **映射建议做成“一个角色对应多个别名”**，并统一归一化（去扩展名、转小写、去空格/下划线/连字符）。每个角色的别名至少包含：包常用名 + 注册表键名本身。例如 `Arrow` 接受 `Normal` / `Arrow`，`Wait` 接受 `Busy` / `Wait`。这样能少踩命名差异的坑。
3. **冲突和缺失要显式处理。** 同一角色匹配到多个文件（`Normal.cur` + `Normal.ani`）要提示而不是静默选一个；包里没有的角色会沿用旧主题导致“混搭”，必须打印缺失清单。
4. **加自动备份/恢复。** 每次 apply 前把当前全部值（值名/值/类型）快照到 `backups/*.json`，提供 `restore`。对零基础用户，这是防“把光标弄丢”的安全网。
5. **遍历注册表时要过滤杂项值。** 里面有默认值 `(Default)`（值名是空字符串）和 `Scheme Source`（DWORD），直接当光标角色处理会报类型错误。只对 17 个白名单角色读写。
6. **提前做一次“技术验证关卡”（原第 2 步）。** 先只换 Arrow 一个角色，确认这台机器上 SPI 能不能刷新。结论出来之前不要往下写第 3、4 步，否则后面全建立在错误假设上。

---

## 三、分阶段实施计划

### 第一周（CLI 最小闭环）

| 阶段 | 内容 | 验收标准 | 预估 |
|---|---|---|---|
| **0. 环境自检** | 确认 Python 及 `winreg` 可用 | `python --version` 正常；`python -c "import winreg, ctypes; print('ok')"` 输出 ok | 10 分钟 |
| **1. 读取当前光标** | `list_current_cursors()`，跳过 `(Default)` / `Scheme Source` | 打印约 17 个角色及路径，与“设置→鼠标→指针”里的当前方案一致 | 20–30 分钟 |
| **2. 单角色写入 + 刷新**（关键关卡） | `set_cursor()` + `refresh_cursors()`，手动测试 Arrow | 鼠标立即变化。若不变：回读注册表确认写入成功 → 换 `SPIF_SENDCHANGE` → 仍不行则用 `SetSystemCursor` 单角色验证 | 30–60 分钟 |
| **3. 扫描 + 自动匹配** | `scan_and_map()`，加入归一化、`Working`→AppStarting、冲突/未识别清单 | 用真实主题包测试，所有角色正确，`README.txt`/`.inf` 被正确忽略 | 30–45 分钟 |
| **4. 一键应用整套** | `apply_theme()`：预览 → 备份 → 批量写 → 刷新一次 → 回读打印 | 整套立即生效；**注销重登后主题仍在**（证明持久化成功） | 30 分钟 |

第一周合计约 **2.5–3.5 小时**（含调试），严格按 CLAUDE.md 的要求一步一验。

### 后续阶段（第二周起）

- **P2 健壮性**：`preview`（dry-run）、`backup` / `restore`、冲突交互选择、缺失角色“回落 Windows 默认”、写入前用 `LoadImage` 预校验所有文件（坏文件就整批中止，避免半套生效）。
- **P3 GUI（可选）**：`tkinter`（标准库），选文件夹 → 匹配结果表格（角色/文件/状态）→ 一键应用/恢复。针对“下载了很多套包”的场景，再往后可加**主题库列表**（记住常用文件夹，一键切换）。
- **P4 分发（可选）**：写 `start.bat` 双击运行；或 PyInstaller 打包（第三方，需另装）。

---

## 四、风险与兜底

| 风险 | 应对 |
|---|---|
| Win11 某些版本 SPI 调用成功但不刷新 | 第 2 步提前验证；`SetSystemCursor` 兜底；最坏情况注销一次（注册表已写好） |
| `SetSystemCursor` 对 NWPen/Pin/Person 的 ID 支持不确定 | 第 2 步用全部角色做矩阵实测并记录；不能兜底的角色接受“注销后生效” |
| 命名不规范（中文名、后缀） | 先 dry-run 输出报告；P2 支持自定义映射 JSON，不改代码 |
| 主题包不完整 | 打印缺失角色清单，P2 提供回落默认选项 |
| 同名 `.cur` / `.ani` | 明确提示冲突，默认 `.ani` 优先，应用前需确认 |
| 中文路径 / 空格路径 | 全程使用 W 版本 API（`SystemParametersInfoW` / `LoadImageW`）+ 绝对路径 + 路径不加引号 |
| 损坏的 `.ani` 导致整批中断 | P2 预校验，失败即中止，不改注册表 |
| 误操作丢失当前主题 | 强制自动备份 + `restore` 命令 |

---

## 五、最终 CLI 形态（目标）

```
python CursorHub.py list              # 查看当前光标配置
python CursorHub.py preview <文件夹>   # 只预览匹配结果，不写注册表
python CursorHub.py apply <文件夹>     # 备份 → 写入 → 刷新
python CursorHub.py backup            # 手动备份
python CursorHub.py restore           # 恢复最近一次备份
```

MVP 阶段（第一周）先用简单交互式输入文件夹路径，不引入 argparse。

---

## 六、动手前待确认

1. **Windows 版本**：运行 `winver` 确认，用来预判 SPI 刷新问题的概率。
2. **主题包文件名**：查看某个光标包文件夹，确认有没有 `Working.ani`（AppStarting），以及有没有非标准命名。
3. **最终形态**：第一周先命令行，之后是否需要 GUI。
