# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

AskTony 是一个 CNB（Cloud Native Backend）数据分析 CLI 工具，基于 DuckDB + DuckLake 构建。数据从 CNB API 采集后写入分层数据湖（Bronze/Silver/Gold），再通过分析命令生成报表和可视化。

## 常用命令

```bash
# 安装/更新开发版本
pip install -e .

# 运行 CLI
asktony --help

# 配置 CNB 凭证
asktony config set --username <user> --token <token> --group <org_slug>

# 数据采集
asktony ingest all --months 6                  # 全量采集（首次）
asktony ingest incremental --overlap-days 1     # 增量采集（日常定时）
asktony ingest enrich-commit-stats --months 2  # 回填 additions/deletions
asktony ingest rebuild-silver-commits          # 用 Bronze 重建 Silver

# 建模
asktony model build                             # 构建 Gold 层（维表/事实表）

# 分析
asktony analyze active-members --months 2
asktony analyze active-employee-score --months 2
asktony analyze project-activity --months 2

# 可视化
asktony visualize line-manager-dev-activity --months 2
asktony visualize active-employee-score --months 2

# 维度管理
asktony export-member-template -o dim_member_template.csv
asktony import-dim-info --member-file dim_member_filled.csv --dry-run

# 项目维度
asktony export-project-collection -o project_info_collection.xlsx
asktony import-project-info --input project_info_collection.xlsx --dry-run

# Universe 导出（全量 XLSX 报表）
asktony universe export --months 2 --output output/universe.xlsx

# 月度绩效 critic
asktony critic monthly-assessment --input monthly.xlsx --output monthly_critic.xlsx --months 2
```

## 架构

### 数据湖分层（`~/.asktony/lake/`）

- **Bronze**（`bronze/`）：CNB API 原始 JSONL 快照，按采集时间戳存储
- **Silver**（`silver/`）：结构化 Parquet + DuckDB 表（`silver.repos`、`silver.members`、`silver.commits`、`silver.commit_stats`）
- **Gold**（`gold/`）：DuckDB 中的维度模型（`gold.dim_member_enrichment`、`gold.fact_commit_employee`、`gold.dim_project` 等）

### 核心源码文件

- `src/asktony/cli.py` — Typer 根应用，顶层命令（export-template、import-dim-info、import-project-info）
- `src/asktony/lake.py` — Bronze/Silver 读写（`Lake` 类），watermark 管理
- `src/asktony/warehouse.py` — Gold 层查询（`Warehouse` 类），所有分析逻辑
- `src/asktony/db.py` — DuckDB 连接封装（`DB` 类）
- `src/asktony/config.py` — 从 `~/.asktony/config.toml` 加载配置
- `src/asktony/cnb_client.py` — CNB API 客户端
- `src/asktony/dim_admin.py` — 维度导入/导出（成员、仓库）
- `src/asktony/project_admin.py` — 项目维度导入
- `src/asktony/commands/` — 子命令组：ingest、analyze、visualize、model、config、universe、critic

### 数据库位置

DuckDB 数据库位于 `~/.asktony/asktonydb.duckdb`。同一时间只能有一个进程持有锁——运行 `model build` 前请先关闭其他客户端（如 DBeaver）。

### 评分规则

员工评分和反刷规则文档位于 `docs/scoring.md`。

## 开发注意事项

- 要求 Python 3.13+
- 使用 `typer` 构建 CLI、`rich` 输出、`pandas`/`matplotlib`/`seaborn` 做可视化
- 每次 upsert 后 `Lake._materialize_parquet()` 会将 DuckDB 表写出为 Parquet 文件
- `meta.repo_watermark` 中的 watermark 记录每个仓库的最后提交时间，用于增量采集
- Commit 归并口径：`author_email` 是将 commit 映射到员工的主键（`gold.fact_commit_employee`）