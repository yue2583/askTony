# 全量拉取逻辑分析

## 代码入口

`asktony ingest all` 命令，源码：`src/asktony/commands/ingest.py` 第 182-234 行

## 执行流程

```
ingest all --months 6
           │
           ▼
1. client.get_group_sub_repos()     # 获取 group 下所有仓库
           │
           ▼
2. lake.write_bronze("group_sub_repos")    # 落 bronze
   lake.upsert_silver_repos()               # 落 silver repos
           │
           ▼
3. 遍历每个 repo：
   ┌─────────────────────────────────────────────┐
   │ since = now - 30 * months                   │
   │                                             │
   │ top_contributors = client.top_contributors  │
   │ members = client.list_all_members          │
   │ commits = client.list_commits(since=since)  │
   │                                             │
   │ → 全部写入 bronze 层                        │
   │ → 全部 upsert 到 silver 层                 │
   └─────────────────────────────────────────────┘
           │
           ▼
4. console.print 完成统计
```

## 关键点

| 项目 | 说明 |
|------|------|
| **months 默认 6** | 只采集最近 N 个月的 commits |
| **watermark** | 全量拉取**不使用** watermark，每次按 `since` 重新拉 |
| **容错** | 单个 repo 失败不影响其他（`_safe_api_call` 捕获异常） |
| **幂等性** | 失败时不覆盖 silver，避免把历史数据删掉写空 |

## 全量 vs 增量采集

| | 全量 `ingest all` | 增量 `ingest incremental` |
|---|---|---|
| commits 范围 | `now - months` 固定窗口 | watermark - overlap_days |
| watermark | 不使用 | 核心依赖，用于计算 since |
| 目标 | 首次采集/历史回补 | 日常定时增量同步 |

---

---

# enrich-commit-stats 逻辑分析

## 代码入口

`asktony ingest enrich-commit-stats` 命令，源码：`src/asktony/commands/ingest.py` 第 327-466 行

## 执行流程

```
enrich-commit-stats --months 1 --max-commits 20000
                    │
                    ▼
1. 从 silver.commits 查询待回填的 commit
   条件：committed_at >= (now - months)
         AND 不在 silver.commit_stats 中（除非 --force）
   按 committed_at DESC 排序，最多 max_commits 条
                    │
                    ▼
2. 遍历每条 commit：
   从 raw JSON 中提取第一个父 commit sha（base_sha）
   （没有父 commit 的跳过，如 initial commit）
                    │
                    ▼
3. 并发调用 client.compare_commits(repo_id, base_sha, sha)
   计算 additions / deletions / changed_lines
                    │
                    ▼
4. 批量 upsert 到 silver.commit_stats（每 500 条一批）
```

## 核心原理

CNB 的 `list_commits` 接口**不返回** additions/deletions 等统计信息，只返回 commit 元数据。

所以需要用 `compare_commits(base, head)` 接口来计算——这个接口返回两个 commit 之间的差异文件列表，从中累加每条的 additions 和 deletions。

## 关键点

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--months` | 2 | 只回填最近 N 个月的 commit |
| `--max-commits` | 5000 | 安全 guard，避免一次请求过多 |
| `--force` | False | 默认跳过已有 stats 的 commit |
| `--concurrency` | 4 | 并发数，注意 API 限流 |
| `--dry-run` | False | 只统计，不写入 |

## 为什么需要提取 parent sha？

`compare(base, head)` 需要知道 base commit 和 head commit。  
commit 的"父 commit"就是它的 base——commit A 的第一个父 commit 就是从 A 能看到的所有变更的起点。

- **普通 commit**：有 1 个父 commit → 可以 compare
- **Merge commit**：有 2+ 个父 commit → 仍取第一个父 commit 来 compare
- **Initial commit**：没有父 commit → 无法 compare，跳过

## 输出字段（写入 silver.commit_stats）

| 字段 | 说明 |
|------|------|
| `repo_id` | 仓库标识 |
| `sha` | 当前 commit sha |
| `base_sha` | 父 commit sha（比较基准） |
| `additions` | 增加的行数 |
| `deletions` | 删除的行数 |
| `changed_lines` | additions + deletions |
| `is_merge` | 是否为 merge commit |
| `computed_at` | 计算时间 |
| `raw` | 少量元数据（文件数） |

---

# model build 逻辑分析

## 代码入口

`asktony model build` 命令，源码：`src/asktony/commands/model.py` 第 13-18 行  
实际逻辑：`src/asktony/warehouse.py` 第 151 行起 `Warehouse.build()`

## 执行流程

```
model build
   │
   ▼
1. 创建 gold schema（如果不存在）
   │
   ▼
2. 构建 dim_repo（仓库维度）
   │
   ▼
3. 构建 dim_member（成员维度）
   │
   ▼
4. 构建 bridge_repo_member（仓库-成员桥表）
   │
   ▼
5. 构建 fact_commit（提交事实表，按月分区到 parquet）
   │
   ▼
6. 构建 fact_member_repo_month（月度聚合事实表）
```

## 各层说明

### dim_repo（仓库维度）

```
gold.dim_repo_base      → silver.repos 原始数据
gold.dim_repo_enrichment → 用户导入的部门信息
gold.dim_department_level2/3 → 部门维度
gold.dim_repo           → VIEW，三者 LEFT JOIN 合并
```

### dim_member（成员维度）

数据来源（三合一）：
- `silver.members`（仓库成员列表）
- `silver.top_contributors`（贡献者排行）
- `silver.commits`（提交记录中的 author）

member_key 规范化规则：
- 企业邮箱 `x.y@clife.cn` → 取 `@` 前缀转为小写
- 纯数字邮箱 `123@clife.cn` → `partner-123`
- 用户名符合规范 → 直接用 `LOWER(username)`
- 否则 → `LOWER(COALESCE(username, email, user_id))`

### fact_commit（提交事实表）

- 数据源：`silver.commits` + `silver.commit_stats`（左连接）
- 输出到：`~/.asktony/lake/gold/fact_commit/commit_month=YYYY-MM/*.parquet`
- Hive 分区格式，按提交月份分目录存储
- 通过 `author_email` 作为主键关联到员工

### fact_member_repo_month（月度聚合）

基于 fact_commit，按 `member_key + repo_id + commit_month` 聚合，方便快速查询月度统计。

## 关键点

| 项目 | 说明 |
|------|------|
| **幂等设计** | 每次 build 先 `DROP VIEW/TABLE IF EXISTS`，再重建 |
| **enrichment 保留** | `dim_member_enrichment` 和 `dim_repo_enrichment` 用 `CREATE TABLE IF NOT EXISTS`，保留用户导入数据 |
| **分区策略** | fact_commit 按 `commit_month` 分区，避免大表全量扫描 |
| **空数据保护** | 无任何 commits 时创建空 view，保证后续分析不报错 |
| **schema 演进** | enrichment 表支持 `ADD COLUMN IF NOT EXISTS`，兼容旧版本升级 |

