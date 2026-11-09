# NeuroNav 交接文档（HANDOFF）

> 写给接手这个项目的新会话。只读这一份文件就能接着做。
> 最后更新：2026-11-09。用户：Ning（交流用中文，代码和标识符用英文）。

---

## 0. 现在做到哪了（先看这里）

| 项目 | 状态 |
|---|---|
| 第 1 步：重构代码结构 | **已完成**，在 `refactor` 分支（未合并）。70/70 测试通过，没有加任何新功能 |
| 等 Ning 做的事 | 在实验室电脑（连着 OptiTrack 相机和机械臂）上手动测试 `refactor`，确认和旧版行为一致 |
| 测试通过以后 | 把 `refactor` 合并到 `master`，然后开始第 2 步（声场库） |
| GitHub | 私有仓库 `ningwang318/NeuroNav`。`master` = 97ed215（重构前快照），`refactor` = 2eb6feb 之后加了本文件 |

**不要**在 Ning 确认实验室测试通过之前合并 `refactor`。第 2 步可以在 Ning 同意后提前开始，两者互不影响。

---

## 1. 项目目标

NeuroNav 是一个 Windows 桌面程序（PySide6 + pyqtgraph.opengl），用于**经颅聚焦超声（tFUS）**实验导航：

1. **跟踪**：OptiTrack Trio 3 相机，通过 Motive API（独立子进程）或 NatNet 网络流获取刚体位姿（Head、Transducer）。
2. **影像**：显示 MRI（NIfTI/DICOM）三视图、3D 剖面，以及头模与跟踪坐标的对应。
3. **声场**：把 500 kHz 换能器的声场显示在影像和 3D 场景里。

后续要做的三件事：

- **第 2 步：预计算声场库**。Ning 用自己的 MATLAB/Python 算法，对头骨表面的每个点离线计算穿颅声场。程序根据跟踪到的换能器位置自动选点、加载并显示。
- **第 3 步：仿体验证**。用 PicoLog TC-08 热电偶测温升，换算成 Isppa，和预测值比较误差。
- **第 4 步：真实实验显示**。被试 MRI，以及由它生成的 pseudo-CT 计算出的声场，按换能器位姿自动加载显示。

---

## 2. 代码结构（`refactor` 分支）

```
app.py                  入口（等同 python -m neuronav）
Launch NeuroNav.cmd     双击启动，使用 .venv
setup.ps1               创建 .venv（参数 -Python <路径>、-Recreate；自动找 64 位 Python 3.11–3.13）
requirements.txt        依赖（注意：故意不含 h5py，见第 5 节）
local.example.toml      本机路径模板，复制为 local.toml（gitignore）
neuronav/
  __main__.py           命令行参数，启动 QApplication
  core/                 不依赖 Qt
    frames.py           DISPLAY 坐标变换（Motive Y-up 米 → GL Z-up）、四元数(xyzw)、欧拉角、reference_data
    config.py           路径、读 local.toml、JSON 读写（原子写入）
    head_phantom.py     头模；preview_to_tracker 是唯一的变换链
    table_layout.py     桌面布局
    diagnostics.py      Save diagnostics… 导出
  tracking/
    datatypes.py        Body / Frame / Models
    natnet.py           NatNet 组播客户端
    motive_api.py       启动并读取 motive_service 子进程
    motive_service.py   独立子进程（只用标准库 + ctypes 调 MotiveAPI.dll），stdout 发 JSON
    reference_plane.py  三点参考平面 → 导航坐标系
  imaging/              mri_volume.py（加载）、volume_geometry.py
  acoustics/field.py    实测焦域声场（assets/acoustics/measured_500khz.npz）、放置、换能器几何
  validation/           空，留给第 3 步
  ui/                   main_window.py（侧栏 + 33 ms refresh 循环）、workspace_view.py、mri_workspace.py、
                        mri_3d.py、acoustic_scene.py、camera_bar.py、lab_setup.py、gl_utils.py、style.py、
                        panels/（tracking_source, live_status, display）、
                        dialogs/（reference_plane, table_layout, phantom, acoustic）
tests/                  10 个文件，70 个测试：$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m unittest -v
tools/                  probe_tracking.py、inspect_acoustic_fig.m
config/                 版本化的 json：reference_plane, head_phantom, table_layout, acoustic_placement, transducer_500khz
assets/acoustics/       measured_500khz.npz（9.5 MB）
```

README.md 有更详细的使用说明，以及 Motive 子进程的架构说明。

### 第 1 步具体做了什么（commit 记录）
- 5d21e83：模块移进 `neuronav/` 包，测试放进 `tests/`，一次性脚本放进 `tools/`
- 609aa66：拆分原来 1041 行的 app.py，删掉重复的辅助函数
- 865b4f2：保留每处原有的 GL context 语义，影像依赖改为模块顶层导入
- 2eb6feb：路径都相对项目目录，新增 local.toml、.venv 启动器、setup.ps1
- 删除了：多余的虚拟环境（env/、旧的 .venv、%TEMP% 里的 venv）、diagnostics/、日志、旧的绝对路径

已知小问题：测试里有一条无害的 pyqtgraph 警告 "Failed to disconnect … destroyed()"，原因未确认。

---

## 3. 环境

- 当前开发机：Ning 的 Windows 电脑，代码在 `D:\Projects\NeuroNav\app`（本地 git，remote origin 指向 GitHub）。
- 那台电脑上的 `.venv` 是基于 `anaconda3\envs\nbs_tfus`（Python 3.11.16）建的。Motive 服务也用这个 conda 环境，**不要删它**。
- 实验室电脑（连着相机和机械臂）是最终运行环境，旧版代码就是从那里拷来的。目标是程序**不依赖具体机器**：一个 .venv 从 requirements 建，没有硬编码路径，每台机器的路径写在 `local.toml` 里。
- 实验室电脑的部署方法：
  ```
  git clone -b refactor https://github.com/ningwang318/NeuroNav.git app-new
  cd app-new
  powershell -ExecutionPolicy Bypass -File .\setup.ps1
  ```
  然后把两个 Motive 文件拷进 `config\`（见第 4 节）。先关掉 Motive 程序，再双击 `Launch NeuroNav.cmd`。
- 实验室测试的检查项：LIVE TRACKING 约 120 fps、相机数 3、刚体移动时 3D 视图同步、Reference plane → Use saved XYZ、加载 MRI 后头模跟着 Head 刚体动、Transducer 设为 Live marker preview、Stop/Start tracking 反复切换、关闭后再开相机能正常释放。出问题时要 `motive-service.log`、截图和 Save diagnostics… 导出的 json。
- 注意：点 Demo（合成 MRI）会改写 `config\head_phantom.json`（旧版也是这样），测完用 `git checkout config` 恢复。

---

## 4. 不在 git 里的本地数据

| 内容 | 位置 | 说明 |
|---|---|---|
| Motive 相机标定 `System Calibration.mcal` | 旧版代码文件夹的 `config\`（实验室电脑）；也可以从 Motive 导出 | 只对那一套相机有效，被 gitignore |
| Motive 资产配置 `MotiveProfile.motive` | 同上 | 刚体定义，被 gitignore |
| 每台机器自己的设置 `settings.ini`、`local.toml` | 项目根目录 | gitignore |
| 示例 CT 头骨 `skull_c_remove_marker.mat` | Ning 电脑 `D:\Projects\NeuroNav\visual\` | 变量 `v`，221×301×596 uint16，0.5 mm 各向同性（文件里没存间距） |
| 头骨点元数据 `metadata/crop_metadata.mat` | 同上 | **v7.3 格式（HDF5）**，475 个点 |
| 预计算声场 `reconstructed_field/skull_patch_0001..0475.mat` | 同上 | 变量 `recovered_amp_3D`，128³ single，单位 Pa，频率 500 kHz，总共约 3.5 GB，**不要放进 git** |
| 参考 MATLAB 脚本 | 同上 | `crop_skull_transducer_rois_fast.m`、`interactive_field_viewer.m`、`threeD_field_plot.m` |

`visual` 里的 CT **只是例子**（见第 6 节）。

---

## 5. 关键技术约定和坑

### h5py 不能装进程序环境
pyqtgraph 检测到 h5py 会自动导入它，之后后台加载 MRI 的 QThread 大约一半的概率会卡死。所以 requirements 里**故意没有 h5py**。要读 v7.3 .mat（比如 crop_metadata.mat），只能二选一：
- 在单独的子进程里读；或者
- 用 `tools/` 下的脚本离线转成 npz/json，程序只读转换后的文件。

### 坐标
- Motive 是 Y 轴朝上，单位米，四元数顺序 xyzw。`frames.DISPLAY` 把它转成 GL 的 Z 轴朝上。
- 导航坐标系由三点参考平面确定。
- 头模和 MRI 的对应目前是**假定的偏移**（界面提示 "MRI → markers: ASSUMED OFFSET"），不是实测配准。

### 声场的放置（照搬 interactive_field_viewer.m）
- 第 k 个头骨点：中心 `c = centers(k,:)`（MATLAB 从 1 开始的 [row col slice] 体素坐标）、`normalsIn`、`normalsOut`、`globalRef`、`diskDiameter`。
- `e2` = globalRef [0 0 -1] 投影到切平面后归一化，`e1 = e2 × nOut`。
- 体素 (a,b,k) 的位置：`P = c + (b−h)·e1 + (a−h)·e2 + (depthStart+k−1)·nIn`，其中 `h = (N+1)/2`。
- 声场第 1 维沿 e2，第 2 维沿 e1，第 3 维沿 nIn（指向颅内）。
- **depthStart = 30**（475 个点都一样）。紧贴头骨的那一段声场故意没存：那里的声场在 −6 dB 以下本来就会被滤掉，不存它能让声场保持立方体。这是仿真设置的一部分，代码里必须加注释说明。

### 尺寸都是参数，不能写死
- dx = 0.5 mm。声场立方体边长 N = 换能器直径 / dx（现在是 128）。直径、patch 大小、声场大小以后都可能变，**不要硬编码 128**。
- 仿真设置（频率、声源压力、直径、dx、depthStart 等）在程序里要做成可选项（SimulationSetup），写进声场库的 `library.json`。以后可能在程序里直接计算。

### 显示
- 以每个声场自己的峰值为参考，画 −3 / −6 / −12 dB 等值面，颜色分别为红 / 绿 / 蓝（同 threeD_field_plot.m），用 scikit-image 的 marching cubes 生成。
- 换能器沿表面法向放置，等值面放在所选的头骨点上。

### 根据跟踪选点（程序里的主要方式）
- 把换能器的轴线变换到影像坐标，求它和头骨表面的交点，再找 475 个点里最近的那个。
- 容差：**距离 ≤ 5 mm 且角度 ≤ 10°，两个都满足才显示声场**。否则提示“非垂直入射”或“换能器未贴近头骨”。加上迟滞，避免在两个点之间来回跳。
- 鼠标悬停选点只作为离线浏览模式保留，效果参考 interactive_field_viewer。
- 头骨和换能器上**还没有 marker**：两者都用“预设中心坐标 + x/y/z 偏移”，可以在界面里改，以后换成实测值。

---

## 6. 影像策略（重要的计划变更）

- 程序**以 MRI 为主**。实际实验流程：MRI → pseudo-CT（离线）→ 用 pseudo-CT 算声场（离线）。程序只导入 **MRI 和对应的声场库文件夹**。
- 声场显示在**计算它所用的那个影像**上，所以不需要另做 CT→MRI 配准（pseudo-CT 和 MRI 在同一个网格上）。
- 现在这个头骨还没有 MRI，所以第 2 步开发时先用 `visual` 里的 CT .mat 当底图（按 `MRIVolume` 读入，间距 0.5 mm）。
- 声场库的 manifest 里记录底图信息（文件、形状、affine），导入时检查和当前加载的影像是否一致。

---

## 7. 后续计划

### 第 2 步：声场库（合并 refactor 之后开始）
- `FieldProvider` 接口：`FreeField`（现有的实测焦域）/ `Library`（预计算库）/ 以后的 `Solver`。
- 声场库文件夹 = `library.json`（SimulationSetup + 底图信息 + 每个点的 c / nIn / nOut / e1 / e2）+ 每个点的声场文件。用 tools/ 脚本把 crop_metadata.mat 离线转换出来（不能用 h5py，见第 5 节）。
- 读 `.mat` 声场：v7 及以下可以用 scipy.io.loadmat；如果是 v7.3，同样在子进程里读或离线转换。
- 把 CT .mat 当影像加载、按需加载单个声场并缓存、画 dB 等值面、根据跟踪选点（5 mm / 10°）、SimulationSetup 面板。
- `local.toml` 的 `[paths] field_library` 已经预留。
- 还要引入 `FrameGraph`（统一管理坐标变换）和 `AppMode`。

### 第 3 步：仿体验证
- 温度源接口 `TemperatureSource`：PicoSDK `usbtc08` 实时流（`usb_tc08_run` / `usb_tc08_get_temp`），以及 PicoLog 6 CSV 离线回放。TC-08 有 8 个热电偶通道（就是板上的 8 个接口），另有冷端通道。单通道约 10 Hz，8 通道全开大约 0.9 s 采一次。
- 换算用初始升温速率法：`I = ρ·c_p/(2α) · dT/dt`，`p = √(2ρcI)`，再除以占空比得到 Isppa。**吸收系数暂时按水算**（500 kHz 下约 0.006 Np/m），所以算出来的结果只是占位。Ning 之后会给出正式公式。
- 输出：测量值和预测值的对比以及误差。

### 第 4 步：真实实验显示
- 被试 MRI 和对应的 pseudo-CT 声场库，按换能器位姿自动加载。

---

## 8. 还没定的事

- 实验室电脑的测试结果，以及合并 `refactor` 的确认（等 Ning）。
- Ning 正式的温度→声压公式。
- 仿体吸收系数的实际值。
- 头骨和换能器的 marker，以及实测配准（目前用预设中心 + 偏移）。
- 实验室电脑上的 Python/conda 环境：setup.ps1 会自动找 3.11–3.13，也可以用 `-Python` 指定。
- 那条 pyqtgraph 警告要不要查（可选）。

---

## 9. 和 Ning 合作的方式

- 回复用中文，代码里的名字保持英文。
- 不可撤销的操作要先问 Ning：合并 master、删除文件、推送到 GitHub。
- Ning 希望进度透明。如果某一步会花很久，先说一声，或者先给一个时间盒。
- 用户手册和计划文档最早写在旧账号的 Claude Docs 里，新账号看不到。需要的话以本文件和 README.md 为准。
