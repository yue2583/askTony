分析依次执行[asktony ingest all --months 6],[asktony ingest enrich-commit-stats --months 2],[asktony ingest rebuild-silver-commits],[asktony model build],[asktony import-dim-info --member-file dim_member_filled.csv --repo-file dim_repo_filled.csv],[asktony analyze member-commits --months 3]的逻辑，其中有没有只统计合并到主分支的提交，还是所有分支都统计了

没有。。。
保持一致即可