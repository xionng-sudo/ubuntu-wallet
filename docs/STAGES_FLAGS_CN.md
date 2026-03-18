# Stage 与 Feature Flags 配置说明

> 本文档说明 `common/settings.py` 中引入的"阶段（Stage）+ 开关（Feature Flags）"控制面，
> 解释各 Stage 的默认开关状态，以及如何通过环境变量进行配置或单项覆盖。

---

## 一、为什么引入 Stage + Feature Flags？

ubuntu-wallet 从"准生产研究系统"演进为"可持续维护的生产候选系统"需要逐步开启能力，
避免一次性堆上所有功能导致难以定位问题。Stage + Flags 体系提供：

- **统一配置入口**：只改 `STAGE` 环境变量，即可切换整套能力组合。
- **单项覆盖**：需要灰度某个功能时，用 `ENABLE_XXX=true/false` 单独覆盖，
  无需修改代码。
- **可观测性**：`/healthz` 接口实时回显当前 `stage` 与所有 `flags`，
  确认服务器上的开关状态一目了然。
- **可回滚**：任何 Flag 默认关闭时不影响现有行为；开关打开才启用新逻辑。

---

## 二、Stage 定义

| Stage | 值   | 含义                       |
|-------|------|----------------------------|
| S0    | `S0` | 研究验证期：实验工具，非破坏性 |
| S1    | `S1` | 生产候选（**推荐默认**）：稳定基线 + 报告 + registry |
| S2    | `S2` | 可运维：增加 drift/calibration 监控 |
| S3    | `S3` | 准实盘：增加永续风控守卫     |
| S4    | `S4` | 平台化：自动重训 + 自动晋升/回滚 |

---

## 三、Flag 与 Stage 对应关系

下表展示各 Flag 在每个 Stage 下的默认值（✅ = 开启，❌ = 关闭）。

| Flag 名称                           | 对应 Issue | S0 | S1 | S2 | S3 | S4 |
|-------------------------------------|-----------|----|----|----|----|-----|
| `ENABLE_MTF_FEATURES`               | Issue 1   | ✅ | ✅ | ✅ | ✅ | ✅ |
| `ENABLE_MODEL_REGISTRY`             | Issue 2   | ❌ | ✅ | ✅ | ✅ | ✅ |
| `ENABLE_THRESHOLD_GRID_REPORT`      | Issue 3   | ✅ | ✅ | ✅ | ✅ | ✅ |
| `ENABLE_DAILY_EVAL_REPORT`          | Issue 4   | ❌ | ✅ | ✅ | ✅ | ✅ |
| `ENABLE_EXOGENOUS_FEATURES`         | Issue 5   | ❌ | ❌ | ❌ | ❌ | ❌ |
| `ENABLE_DRIFT_MONITOR`              | Issue 6   | ❌ | ❌ | ✅ | ✅ | ✅ |
| `ENABLE_CALIBRATION_REPORT`         | Issue 7   | ❌ | ❌ | ✅ | ✅ | ✅ |
| `ENABLE_PERP_RISK_GUARDS`           | Issue 12  | ❌ | ❌ | ❌ | ✅ | ✅ |
| `ENABLE_SCHEDULED_RETRAIN`          | Issue 13  | ❌ | ❌ | ❌ | ❌ | ✅ |
| `ENABLE_PROMOTE_ROLLBACK_AUTOMATION`| Issue 14  | ❌ | ❌ | ❌ | ❌ | ✅ |

> **注意**：`ENABLE_EXOGENOUS_FEATURES`（Issue 5）在所有 Stage 下默认关闭，
> 因为外生因子采集管线（funding/OI/taker ratio）尚未实现，需要手动单项覆盖开启。

---

## 四、如何配置

### 4.1 通过 `.env` 文件（推荐用于开发/本地测试）

复制 `.env.example` 为 `.env` 并修改 `STAGE`：

```bash
cp .env.example .env
# 编辑 .env，设置 STAGE=S1（或 S0/S2/S3/S4）
```

然后在启动时加载：

```bash
set -a; source .env; set +a
python3 ml-service/app.py
```

### 4.2 通过 systemd 环境变量（推荐用于生产服务器）

在 `/etc/systemd/system/ml-service.service` 的 `[Service]` 段添加：

```ini
[Service]
Environment=STAGE=S1
# 可选的单项覆盖：
# Environment=ENABLE_DRIFT_MONITOR=true
```

修改后重新加载并重启：

```bash
sudo systemctl daemon-reload
sudo systemctl restart ml-service
```

### 4.3 单项覆盖（不修改 Stage）

如果想在 S1 阶段临时开启 drift 监控，而不升级到 S2：

```bash
# 临时测试（当前 shell）
ENABLE_DRIFT_MONITOR=true python3 ml-service/app.py

# 或在 .env 中添加：
STAGE=S1
ENABLE_DRIFT_MONITOR=true
```

---

## 五、验证开关是否生效

启动服务后，访问 `/healthz` 接口：

```bash
curl http://localhost:8000/healthz | python3 -m json.tool
```

响应中包含 `stage` 与 `flags` 字段，例如：

```json
{
  "ok": true,
  "stage": "S1",
  "flags": {
    "ENABLE_THRESHOLD_GRID_REPORT": true,
    "ENABLE_DAILY_EVAL_REPORT": true,
    "ENABLE_CALIBRATION_REPORT": false,
    "ENABLE_DRIFT_MONITOR": false,
    "ENABLE_MODEL_REGISTRY": true,
    "ENABLE_PROMOTE_ROLLBACK_AUTOMATION": false,
    "ENABLE_MTF_FEATURES": true,
    "ENABLE_EXOGENOUS_FEATURES": false,
    "ENABLE_PERP_RISK_GUARDS": false,
    "ENABLE_SCHEDULED_RETRAIN": false
  },
  ...
}
```

---

## 六、在代码中使用

任意 Python 入口（ml-service、scripts、python-analyzer）均可：

```python
from common.settings import get_settings

s = get_settings()

if s.flags.ENABLE_MODEL_REGISTRY:
    # 新路径：通过 registry/current 解析模型
    ...
else:
    # 旧路径：固定路径加载，行为不变
    ...
```

> `get_settings()` 使用 `@lru_cache`，整个进程只读取一次环境变量。
> 在单元测试中可调用 `get_settings.cache_clear()` 来重置。

---

## 七、进阶：调用 `common` 模块时的路径问题

`common/` 位于仓库根目录。`ml-service/app.py` 在启动时会自动将仓库根目录插入
`sys.path`，因此 `from common.settings import get_settings` 无需额外配置即可工作。

如果在 `scripts/` 或 `python-analyzer/` 中使用，需在脚本顶部添加：

```python
import sys, os
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from common.settings import get_settings
```

---

## 八、与 ROADMAP Issue 的对应关系

详见 [`docs/ROADMAP_ISSUES_CN.md`](ROADMAP_ISSUES_CN.md)，其中每个 Issue 均标注了
对应的 Flag 名称、当前实现状态，以及相关文件路径。
